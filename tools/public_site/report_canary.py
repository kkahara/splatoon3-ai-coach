"""Review-code HMAC canaries: catch a review code after the database dropped it.

A review-code submission's code leaves the database at recording handoff, but
its report is published later. At capture the code's HMAC-SHA-256 is stored
under an owner-only key, and the publisher and ``scan`` match every published
byte against it.

Layout (directory ``0700``, files ``0600``), under ``PUBLIC_ROOT``:

- ``report_canary/key.json``: version, 32-byte key, ``key_id``, ``created_at``
  (the canary epoch).
- ``report_canary/<submission_id>.json``: ``key_id``, HMAC, ``created_at``.
- ``report_canary/<submission_id>.pending.json``: a ``correct_code`` HMAC not
  yet finalized. Its presence leaves the submission unchecked.
- ``report_canary.epoch.json`` (beside the directory): the key's ``key_id`` and
  epoch. It is never deleted, so losing the whole directory cannot lead to a
  fresh key and a later epoch that would exempt older submissions.

Every write is a temporary file (``O_EXCL|O_NOFOLLOW``) that is fsynced, then
renamed (or, for the key, linked, so an existing key is never replaced); the
directory is fsynced after every create, replace or unlink. Every read uses
``O_NOFOLLOW`` and checks type, owner and mode. Any violation makes the canary
unavailable, never clean. The key, HMACs and codes are never logged.
"""

from __future__ import annotations

import base64
import contextlib
import errno
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from public_site.models import Submission
from public_site.report_content import CodeMatcher
from public_site.report_staging import StagingUnsafe, _check_dir
from public_site.store import PublicStore, utc_now

CANARY_DIRNAME = "report_canary"
EPOCH_NAME = "report_canary.epoch.json"
KEY_NAME = "key.json"
KEY_LOCK = ".key.lock"
UNCHECKED_CANARY = "review_code_canary"
CANARY_RULE = "known_value:review_code"
ORPHAN_GRACE_SECONDS = 86400
TEMP_GRACE_SECONDS = 600
_VERSION = 1
_DIR_MODE = 0o700
_FILE_MODE = 0o600
_MAX_FILE = 4096
_KEY_ID_LABEL = b"s3coach.review_code_canary.key_id.v1"
_CODE = re.compile(r"[A-Z0-9]{16}")
_SEPARATORS = re.compile(r"[\s-]+", re.ASCII)
_SUBMISSION = re.compile(r"[0-9a-f]{32}")
_RECORD = re.compile(r"^([0-9a-f]{32})\.json$")
_PENDING = re.compile(r"^([0-9a-f]{32})\.pending\.json$")
_TEMP = re.compile(r"^\..+\.[0-9a-f]{12}\.tmp$")

AcquisitionLookup = Callable[[str], dict | None]


class CanaryUnavailable(OSError):
    """Canary key or record cannot be used safely. Carries no path or value."""

    def __init__(self) -> None:
        super().__init__("canary_unavailable")


@dataclass(frozen=True)
class CanaryKey:
    """The HMAC key. ``repr`` never shows the key bytes."""

    key: bytes = field(repr=False)
    key_id: str
    created_at: str


def compact_code(code: str) -> str:
    """16 uppercase letters/digits from a normalized or raw review code."""
    compact = _SEPARATORS.sub("", code).upper()
    if not _CODE.fullmatch(compact):
        raise ValueError("review_code_invalid")
    return compact


def key_id_for(key: bytes) -> str:
    """Truncated SHA-256 of a fixed label plus the key; never reveals the key."""
    return hashlib.sha256(_KEY_ID_LABEL + key).hexdigest()[:16]


def code_digest(key: bytes, code: str) -> bytes:
    """HMAC-SHA-256 of the compacted code."""
    return hmac.new(key, compact_code(code).encode("ascii"), hashlib.sha256).digest()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _write_atomic(directory: Path, name: str, data: bytes, *, exclusive: bool) -> None:
    """Durably create or replace ``directory/name``.

    ``exclusive`` links instead of renaming, so an existing target raises
    ``FileExistsError`` and is never replaced.
    """
    temp = directory / f".{name}.{secrets.token_hex(6)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(temp, flags, _FILE_MODE)
    try:
        try:
            os.fchmod(fd, _FILE_MODE)
            _write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        if exclusive:
            os.link(temp, directory / name, follow_symlinks=False)
        else:
            os.rename(temp, directory / name)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)
        _fsync_dir(directory)


def _unlink(directory: Path, name: str) -> bool:
    try:
        os.unlink(directory / name)
    except FileNotFoundError:
        return False
    _fsync_dir(directory)
    return True


def _read_private(path: Path) -> bytes:
    """Bytes of an owner-only regular file. Missing raises FileNotFoundError."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError:
        raise CanaryUnavailable from None
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != _FILE_MODE
            or info.st_size > _MAX_FILE
        ):
            raise CanaryUnavailable
        return os.read(fd, _MAX_FILE + 1)
    finally:
        os.close(fd)


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(_read_private(path))
    except ValueError:
        raise CanaryUnavailable from None
    if not isinstance(payload, dict) or payload.get("version") != _VERSION:
        raise CanaryUnavailable
    return payload


def _encode(payload: dict) -> bytes:
    return (json.dumps({"version": _VERSION, **payload}, sort_keys=True) + "\n").encode()


class CanaryStore:
    """Owner-only canary key and per-submission records under ``PUBLIC_ROOT``."""

    def __init__(self, public_root: Path) -> None:
        self.public_root = Path(public_root)
        self.root = self.public_root / CANARY_DIRNAME
        self._lock = threading.Lock()

    def _directory(self, *, create: bool) -> Path | None:
        """The verified directory, or None when absent and ``create`` is false."""
        root = self.public_root
        if os.path.realpath(root) != os.path.abspath(root):
            raise CanaryUnavailable
        if create:
            try:
                os.mkdir(self.root, _DIR_MODE)
                os.chmod(self.root, _DIR_MODE, follow_symlinks=False)
                _fsync_dir(root)
            except FileExistsError:
                pass
        try:
            _check_dir(self.root)
        except FileNotFoundError:
            if create:
                raise CanaryUnavailable from None
            return None
        except StagingUnsafe:
            raise CanaryUnavailable from None
        if stat.S_IMODE(os.lstat(self.root).st_mode) != _DIR_MODE:
            raise CanaryUnavailable
        return self.root

    @contextlib.contextmanager
    def _key_lock(self, directory: Path) -> Iterator[None]:
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            fd = os.open(directory / KEY_LOCK, flags, _FILE_MODE)
        except OSError:
            raise CanaryUnavailable from None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _entries(self, directory: Path) -> list[tuple[str, str]]:
        """``(name, submission_id)`` of every record and pending file."""
        found = []
        for name in os.listdir(directory):
            match = _RECORD.match(name) or _PENDING.match(name)
            if match:
                found.append((name, match.group(1)))
        return found

    def _epoch(self) -> dict | None:
        try:
            return _read_json(self.public_root / EPOCH_NAME)
        except FileNotFoundError:
            return None

    def load_key(self) -> CanaryKey:
        """The key. A missing key raises FileNotFoundError; anything unsafe,
        corrupt or inconsistent with the epoch marker raises CanaryUnavailable.
        """
        directory = self._directory(create=False)
        if directory is None:
            raise FileNotFoundError(errno.ENOENT, "canary_key_missing")
        payload = _read_json(directory / KEY_NAME)
        try:
            key = base64.b64decode(payload["key"], validate=True)
            key_id, created_at = str(payload["key_id"]), str(payload["created_at"])
        except (KeyError, TypeError, ValueError):
            raise CanaryUnavailable from None
        if len(key) != 32 or key_id_for(key) != key_id:
            raise CanaryUnavailable
        epoch = self._epoch()
        if epoch is not None and (
            epoch.get("key_id") != key_id or epoch.get("created_at") != created_at
        ):
            raise CanaryUnavailable
        return CanaryKey(key=key, key_id=key_id, created_at=created_at)

    def ensure_key(self) -> CanaryKey:
        """Load the key, creating it only on a fresh install.

        A key is created only when no key, no record, no pending file and no
        epoch marker exist; creation runs under ``flock`` and links the key into
        place, so concurrent starts end with exactly one key. An existing key is
        never replaced or regenerated.

        Raises:
            CanaryUnavailable: The key cannot be used and must be restored.
        """
        with self._lock:
            directory = self._directory(create=True)
            with self._key_lock(directory):
                try:
                    key = self.load_key()
                except FileNotFoundError:
                    if self._epoch() is not None or self._entries(directory):
                        raise CanaryUnavailable from None
                    key = self._create_key(directory)
                self._ensure_epoch(key)
                return key

    def _create_key(self, directory: Path) -> CanaryKey:
        key = secrets.token_bytes(32)
        created = CanaryKey(key=key, key_id=key_id_for(key), created_at=utc_now())
        payload = {
            "key": base64.b64encode(key).decode("ascii"),
            "key_id": created.key_id,
            "created_at": created.created_at,
        }
        try:
            _write_atomic(directory, KEY_NAME, _encode(payload), exclusive=True)
        except FileExistsError:
            raise CanaryUnavailable from None
        logger.info("Review-code canary key created: {}", created.key_id)
        return created

    def _ensure_epoch(self, key: CanaryKey) -> None:
        """Write the epoch marker after a crash between key and marker writes."""
        if self._epoch() is not None:
            return
        payload = {"key_id": key.key_id, "created_at": key.created_at}
        with contextlib.suppress(FileExistsError):
            _write_atomic(self.public_root, EPOCH_NAME, _encode(payload), exclusive=True)

    def capture(self, submission_id: str, code: str) -> None:
        """Durably store the HMAC of ``code`` for a new submission.

        Raises:
            CanaryUnavailable: Nothing usable was stored; refuse the submission.
        """
        self._write_digest(submission_id, code, f"{submission_id}.json")

    def stage_correction(self, submission_id: str, code: str) -> None:
        """Durably write ``<id>.pending.json`` before a ``correct_code`` update."""
        self._write_digest(submission_id, code, f"{submission_id}.pending.json")

    def _write_digest(self, submission_id: str, code: str, name: str) -> None:
        if not _SUBMISSION.fullmatch(submission_id):
            raise CanaryUnavailable
        try:
            key = self.ensure_key()
            payload = {
                "key_id": key.key_id,
                "hmac": code_digest(key.key, code).hex(),
                "created_at": utc_now(),
            }
            _write_atomic(self.root, name, _encode(payload), exclusive=False)
        except CanaryUnavailable:
            raise
        except (OSError, ValueError):
            raise CanaryUnavailable from None

    def commit_correction(self, submission_id: str) -> None:
        """Atomically replace the record with the pending correction."""
        directory = self._directory(create=False)
        if directory is None:
            raise CanaryUnavailable
        try:
            os.rename(
                directory / f"{submission_id}.pending.json",
                directory / f"{submission_id}.json",
            )
            _fsync_dir(directory)
        except OSError:
            raise CanaryUnavailable from None

    def discard_correction(self, submission_id: str) -> None:
        """Remove a pending correction whose database update did not happen."""
        directory = self._directory(create=False)
        if directory is not None:
            _unlink(directory, f"{submission_id}.pending.json")

    def remove(self, submission_id: str) -> None:
        """Remove a submission's record and pending file (after expiry deletion)."""
        directory = self._directory(create=False)
        if directory is None:
            return
        for name in (f"{submission_id}.json", f"{submission_id}.pending.json"):
            _unlink(directory, name)

    def _record(self, directory: Path, name: str) -> tuple[str, bytes]:
        payload = _read_json(directory / name)
        try:
            return str(payload["key_id"]), bytes.fromhex(payload["hmac"])
        except (KeyError, TypeError, ValueError):
            raise CanaryUnavailable from None

    def matcher(self, submission: Submission) -> tuple[CodeMatcher | None, bool]:
        """The submission's code matcher and whether its canary group is checked.

        - Not a review-code submission: nothing to match, checked.
        - A pending correction, an unsafe file or key, or a record whose
          ``key_id`` differs from the key: unchecked.
        - A record: always used, whatever the epoch.
        - No record: checked only for a submission created before the epoch
          (the documented pre-canary limitation), or when no canary key has
          ever existed.
        """
        if submission.input_type != "review_code":
            return None, True
        try:
            return self._matcher(submission)
        except (OSError, ValueError):
            return None, False

    def _matcher(self, submission: Submission) -> tuple[CodeMatcher | None, bool]:
        directory = self._directory(create=False)
        if directory is None:
            return None, self._epoch() is None
        if self._pending_names(directory, submission.id):
            return None, False
        try:
            record = self._record(directory, f"{submission.id}.json")
        except FileNotFoundError:
            record = None
        try:
            key = self.load_key()
        except FileNotFoundError:
            fresh = self._epoch() is None and not self._entries(directory)
            return None, fresh and record is None
        if record is None:
            return None, submission.created_at < key.created_at
        if record[0] != key.key_id:
            return None, False
        return CodeMatcher(key.key, record[1]), True

    def _pending_names(self, directory: Path, submission_id: str) -> list[str]:
        """The pending file plus any interrupted pending write for one submission."""
        name = f"{submission_id}.pending.json"
        prefix = f".{name}."
        return [
            entry
            for entry in os.listdir(directory)
            if entry == name or (entry.startswith(prefix) and _TEMP.match(entry))
        ]

    def pending_states(self, lookup: AcquisitionLookup | None) -> dict[str, str]:
        """Classify every pending correction by submission ID. Never promotes.

        ``promotable``: the database code's HMAC equals the pending HMAC.
        ``stale``: the database still holds the recorded (pre-correction) code.
        Anything else fails closed and needs the operator: ``no_code`` (code
        already NULL, e.g. after handoff), ``mismatch`` (neither HMAC matches),
        ``multiple`` (more than one pending entry), ``unreadable`` (key or
        pending file unusable), ``lookup_failed`` and ``unverified`` (no
        database lookup available).
        """
        directory = self._directory(create=False)
        if directory is None:
            return {}
        ids = {sid for name, sid in self._entries(directory) if _PENDING.match(name)}
        try:
            key = self.load_key()
        except OSError:
            return dict.fromkeys(ids, "unreadable")
        return {sid: self._pending_state(directory, key, sid, lookup) for sid in ids}

    def _pending_state(
        self,
        directory: Path,
        key: CanaryKey,
        submission_id: str,
        lookup: AcquisitionLookup | None,
    ) -> str:
        if len(self._pending_names(directory, submission_id)) != 1:
            return "multiple"
        if lookup is None:
            return "unverified"
        try:
            code = (lookup(submission_id) or {}).get("review_code")
        except Exception:  # noqa: BLE001 — any lookup failure: cannot verify
            return "lookup_failed"
        if not code:
            return "no_code"
        try:
            current = code_digest(key.key, code)
            pending = self._record(directory, f"{submission_id}.pending.json")
            if pending[0] == key.key_id and hmac.compare_digest(pending[1], current):
                return "promotable"
            record = self._record(directory, f"{submission_id}.json")
        except (OSError, ValueError):
            return "unreadable"
        if record[0] == key.key_id and hmac.compare_digest(record[1], current):
            return "stale"
        return "mismatch"

    def repair(self, lookup: AcquisitionLookup) -> dict[str, int]:
        """Finish or discard pending corrections left by a crash. Counts by state.

        Only a pending HMAC verified against the database code is promoted; a
        pending file whose database row still holds the recorded code is
        discarded. Every other state leaves the pending file, so the submission
        stays unchecked and ``canary-status`` shows it.
        """
        counts: dict[str, int] = {}
        for submission_id, state in self.pending_states(lookup).items():
            if state == "promotable":
                state = self._apply(self.commit_correction, submission_id, state)
            elif state == "stale":
                state = self._apply(self.discard_correction, submission_id, state)
            if state not in {"promotable", "stale"}:
                logger.warning(
                    "Review-code canary pending unresolved ({}): {}", state, submission_id
                )
            counts[state] = counts.get(state, 0) + 1
        return counts

    def _apply(
        self, action: Callable[[str], None], submission_id: str, state: str
    ) -> str:
        try:
            action(submission_id)
        except OSError as exc:
            logger.warning("Review-code canary repair failed ({})", type(exc).__name__)
            return "repair_failed"
        logger.info("Review-code canary pending {}: {}", state, submission_id)
        return state

    def prune(
        self,
        store: PublicStore,
        has_publication: Callable[[str], bool],
        now: float | None = None,
    ) -> int:
        """Remove canaries no publication can need. Returns how many went.

        Removes stale temporary files, canaries of expired submissions without a
        publication record (covers P1-A rollback periods), and canaries without
        a projection older than ``ORPHAN_GRACE_SECONDS`` (a failed insert).
        """
        directory = self._directory(create=False)
        if directory is None:
            return 0
        now = time.time() if now is None else now
        removed = self._prune_temporary(directory, now)
        for name, submission_id in self._entries(directory):
            if self._prunable(
                directory / name, submission_id, store, has_publication, now
            ):
                removed += _unlink(directory, name)
        return removed

    def _prune_temporary(self, directory: Path, now: float) -> int:
        removed = 0
        for name in os.listdir(directory):
            if _TEMP.match(name):
                age = now - os.lstat(directory / name).st_mtime
                if age > TEMP_GRACE_SECONDS:
                    removed += _unlink(directory, name)
        return removed

    def _prunable(
        self,
        path: Path,
        submission_id: str,
        store: PublicStore,
        has_publication: Callable[[str], bool],
        now: float,
    ) -> bool:
        submission = store.get(submission_id)
        if submission is None:
            return now - os.lstat(path).st_mtime > ORPHAN_GRACE_SECONDS
        return submission.status == "expired" and not has_publication(submission_id)

    def status(
        self, store: PublicStore, lookup: AcquisitionLookup | None = None
    ) -> dict[str, int | bool]:
        """Counts only, for ``public-site-reports canary-status``.

        ``pending_<state>`` counts pending corrections by ``pending_states``;
        any state other than ``promotable`` or ``stale`` needs the operator.
        """
        counts: dict[str, int | bool] = {
            "directory_safe": True,
            "key_present": False,
            "key_valid": False,
            "epoch_marker": self._safe_epoch_present(),
            "records": 0,
            "pending": 0,
            "post_epoch_without_canary": 0,
            "unchecked_review_code_submissions": 0,
        }
        try:
            directory = self._directory(create=False)
        except CanaryUnavailable:
            directory = None
            counts["directory_safe"] = False
        if directory is not None:
            counts["key_present"] = os.path.lexists(directory / KEY_NAME)
            entries = self._entries(directory)
            counts["pending"] = sum(1 for name, _ in entries if _PENDING.match(name))
            counts["records"] = len(entries) - counts["pending"]
            with contextlib.suppress(OSError):
                self.load_key()
                counts["key_valid"] = True
            for state in self.pending_states(lookup).values():
                counts[f"pending_{state}"] = int(counts.get(f"pending_{state}", 0)) + 1
        self._count_submissions(store, counts)
        return counts

    def _safe_epoch_present(self) -> bool:
        try:
            return self._epoch() is not None
        except CanaryUnavailable:
            return True

    def _count_submissions(self, store: PublicStore, counts: dict) -> None:
        epoch = None
        with contextlib.suppress(OSError):
            epoch = self.load_key().created_at
        for submission in store.list_submissions():
            if submission.input_type != "review_code":
                continue
            _matcher, checked = self.matcher(submission)
            if not checked:
                counts["unchecked_review_code_submissions"] += 1
            if epoch is not None and submission.created_at >= epoch:
                if not os.path.lexists(self.root / f"{submission.id}.json"):
                    counts["post_epoch_without_canary"] += 1
