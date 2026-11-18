"""Owner-only staging for transformed report files, with crash-safe cleanup.

Layout: ``PUBLIC_ROOT/report_staging/<submission_id>/<run_id>/``. Directories
are ``0700`` and files ``0600`` regardless of the process umask. Every path
component must be a real directory owned by this uid; files are opened with
``O_NOFOLLOW``.

Each run holds an exclusive ``flock`` on ``<run_id>/.lease`` for its whole
life. The kernel releases it when the process exits, crashes included, so a
held lock proves the run is active. Cleanup removes a run directory only when
it is not active in this process, its lease lock can be taken (and is kept
while deleting), or, without a lease, it is older than the grace period.

``flock`` is advisory and needs a local filesystem; ``PUBLIC_ROOT`` is a local
Docker volume.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

STAGING_DIRNAME = "report_staging"
LEASE_NAME = ".lease"
GRACE_SECONDS = 600
_DIR_MODE = 0o700
_FILE_MODE = 0o600
_SUBMISSION = re.compile(r"^[0-9a-f]{32}$")
_RUN = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")
_STAGED = re.compile(r"^[0-9]{4}\.[a-z]{3,4}$")


class StagingUnsafe(OSError):
    """A staging path failed the owner-only checks. Carries no path."""

    def __init__(self) -> None:
        super().__init__("staging_unsafe")


def valid_run_id(value: str) -> bool:
    """``YYYYMMDDTHHMMSSZ-<8 hex>``."""
    return bool(_RUN.fullmatch(value))


def _check_dir(path: Path) -> None:
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise StagingUnsafe
    if info.st_uid != os.getuid():
        raise StagingUnsafe


def _owner_dir(path: Path) -> None:
    """Create ``path`` as ``0700`` (or adopt an existing safe one) and verify it."""
    try:
        os.mkdir(path, _DIR_MODE)
    except FileExistsError:
        pass
    _check_dir(path)
    os.chmod(path, _DIR_MODE, follow_symlinks=False)
    if stat.S_IMODE(os.lstat(path).st_mode) != _DIR_MODE:
        raise StagingUnsafe


def _open_private(path: Path, flags: int) -> int:
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, _FILE_MODE)
    try:
        os.fchmod(fd, _FILE_MODE)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise StagingUnsafe
    except BaseException:
        os.close(fd)
        raise
    return fd


class StagingRun:
    """One run's private directory and its held lease."""

    def __init__(self, area: StagingArea, submission_id: str, run_id: str) -> None:
        self._area = area
        self.submission_id = submission_id
        self.run_id = run_id
        self.path = area.root / submission_id / run_id
        self._lease_fd: int | None = None
        self._count = 0

    def open(self) -> StagingRun:
        """Create the owner-only directories and take the lease. Raises StagingUnsafe."""
        root = self._area.root
        root.parent.mkdir(parents=True, exist_ok=True)
        for path in (root, root / self.submission_id, self.path):
            _owner_dir(path)
        fd = _open_private(self.path / LEASE_NAME, os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise StagingUnsafe from None
        lease = {"run_id": self.run_id, "created_at": time.time()}
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps(lease).encode())
        self._lease_fd = fd
        return self

    def write(self, data: bytes, suffix: str) -> Path:
        """Write one staged file (``0600``, ``O_EXCL|O_NOFOLLOW``). Returns its path."""
        if self._lease_fd is None:
            raise StagingUnsafe
        self._count += 1
        name = f"{self._count:04d}.{suffix}"
        if not _STAGED.fullmatch(name):
            raise StagingUnsafe
        _check_dir(self.path)
        path = self.path / name
        fd = _open_private(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        return path

    def close(self) -> None:
        """Remove the run directory while still holding the lease, then release it."""
        try:
            if self._lease_fd is not None:
                _remove_tree(self.path)
                _remove_if_empty(self.path.parent)
        finally:
            if self._lease_fd is not None:
                os.close(self._lease_fd)
                self._lease_fd = None
            self._area.release(self.submission_id, self.run_id)


@dataclass
class StagingCounts:
    """Cleanup outcome. Counts only; never paths or values."""

    removed: int = 0
    active: int = 0
    too_recent: int = 0
    unsafe: int = 0


@dataclass
class StagingSummary:
    """Read-only staging view for ``status``."""

    runs: int = 0
    active: int = 0
    abandoned: int = 0
    unsafe: int = 0
    bytes: int = 0


class StagingArea:
    """The staging root plus this process's set of active runs."""

    def __init__(self, public_root: Path, *, grace_seconds: int = GRACE_SECONDS) -> None:
        self.root = public_root / STAGING_DIRNAME
        self.grace_seconds = grace_seconds
        self._active: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    def open_run(self, submission_id: str, run_id: str) -> StagingRun:
        """Mark the run active in this process, then create it and take its lease."""
        if not _SUBMISSION.fullmatch(submission_id) or not valid_run_id(run_id):
            raise StagingUnsafe
        with self._lock:
            self._active.add((submission_id, run_id))
        run = StagingRun(self, submission_id, run_id)
        try:
            return run.open()
        except BaseException:
            self.release(submission_id, run_id)
            raise

    def release(self, submission_id: str, run_id: str) -> None:
        """Forget an in-process active run."""
        with self._lock:
            self._active.discard((submission_id, run_id))

    def is_active(self, submission_id: str, run_id: str) -> bool:
        """True while this process has the run open."""
        with self._lock:
            return (submission_id, run_id) in self._active

    def recover(self, now: float | None = None) -> StagingCounts:
        """Remove abandoned run directories. Logs counts only."""
        counts = StagingCounts()
        if not os.path.lexists(self.root):
            return counts
        try:
            _check_dir(self.root)
        except StagingUnsafe:
            if os.path.islink(self.root):
                os.unlink(self.root)
            counts.unsafe += 1
            self._log(counts)
            return counts
        moment = time.time() if now is None else now
        for entry in sorted(os.listdir(self.root)):
            self._recover_submission(self.root / entry, moment, counts)
        self._log(counts)
        return counts

    def summary(self) -> StagingSummary:
        """Run directories and bytes, classified without removing anything."""
        result = StagingSummary()
        if not os.path.lexists(self.root):
            return result
        try:
            _check_dir(self.root)
        except StagingUnsafe:
            result.unsafe += 1
            return result
        for sid in sorted(os.listdir(self.root)):
            for run_dir, verdict in self._runs_of(self.root / sid):
                result.runs += 1
                if verdict == "unsafe":
                    result.unsafe += 1
                    continue
                result.bytes += _tree_bytes(run_dir)
                if self._lease_held(run_dir, sid):
                    result.active += 1
                else:
                    result.abandoned += 1
        return result

    def _recover_submission(
        self, sid_dir: Path, now: float, counts: StagingCounts
    ) -> None:
        if os.path.islink(sid_dir):
            os.unlink(sid_dir)
            counts.unsafe += 1
            return
        for run_dir, verdict in self._runs_of(sid_dir):
            if verdict == "unsafe":
                if run_dir != sid_dir and os.path.islink(run_dir):
                    os.unlink(run_dir)
                counts.unsafe += 1
                continue
            self._recover_run(sid_dir.name, run_dir, now, counts)
        if _SUBMISSION.fullmatch(sid_dir.name):
            _remove_if_empty(sid_dir)

    def _runs_of(self, sid_dir: Path) -> list[tuple[Path, str]]:
        try:
            _check_dir(sid_dir)
        except (StagingUnsafe, FileNotFoundError):
            return [(sid_dir, "unsafe")]
        if not _SUBMISSION.fullmatch(sid_dir.name):
            return [(sid_dir, "unsafe")]
        found: list[tuple[Path, str]] = []
        for name in sorted(os.listdir(sid_dir)):
            run_dir = sid_dir / name
            try:
                _check_dir(run_dir)
                safe = valid_run_id(name)
            except (StagingUnsafe, FileNotFoundError):
                safe = False
            found.append((run_dir, "ok" if safe else "unsafe"))
        return found

    def _recover_run(
        self, submission_id: str, run_dir: Path, now: float, counts: StagingCounts
    ) -> None:
        if self.is_active(submission_id, run_dir.name):
            counts.active += 1
            return
        lease = run_dir / LEASE_NAME
        if not os.path.lexists(lease):
            if now - os.lstat(run_dir).st_mtime < self.grace_seconds:
                counts.too_recent += 1
                return
            _remove_tree(run_dir)
            counts.removed += 1
            return
        try:
            fd = _open_private(lease, os.O_RDWR)
        except (OSError, StagingUnsafe):
            counts.unsafe += 1
            return
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                counts.active += 1
                return
            _remove_tree(run_dir)
            counts.removed += 1
        finally:
            os.close(fd)

    def _lease_held(self, run_dir: Path, submission_id: str) -> bool:
        if self.is_active(submission_id, run_dir.name):
            return True
        lease = run_dir / LEASE_NAME
        if not os.path.lexists(lease):
            return False
        try:
            fd = _open_private(lease, os.O_RDONLY)
        except (OSError, StagingUnsafe):
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            return True
        finally:
            os.close(fd)
        return False

    @staticmethod
    def _log(counts: StagingCounts) -> None:
        if counts.removed or counts.active or counts.too_recent or counts.unsafe:
            logger.info(
                "report staging cleanup: removed={} active={} too_recent={} unsafe={}",
                counts.removed,
                counts.active,
                counts.too_recent,
                counts.unsafe,
            )


def _remove_tree(path: Path) -> None:
    """Delete a verified run directory. Symlinks inside are unlinked, never followed."""
    _check_dir(path)
    shutil.rmtree(path)


def _remove_if_empty(path: Path) -> None:
    try:
        _check_dir(path)
        os.rmdir(path)
    except (OSError, StagingUnsafe):
        pass


def _tree_bytes(path: Path) -> int:
    total = 0
    for name in os.listdir(path):
        info = os.lstat(path / name)
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
    return total
