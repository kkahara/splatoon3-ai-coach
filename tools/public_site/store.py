"""Filesystem store for public submissions. Separate from ``analysis/_platform``."""

from __future__ import annotations

import fcntl
import json
import os
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from public_site.models import Submission, VideoInput

_ID = re.compile(r"^[0-9a-f]{32}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
UNFINISHED = frozenset({"received", "validating", "queued", "processing"})
IN_FLIGHT = frozenset({"validating", "processing"})
_CLAIM_LOCK = threading.Lock()
_RECORD_LOCKS: dict[str, threading.RLock] = {}
_RECORD_LOCKS_GUARD = threading.Lock()


def _record_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _RECORD_LOCKS_GUARD:
        return _RECORD_LOCKS.setdefault(key, threading.RLock())


_LIFECYCLE_FIELDS = (
    "status",
    "step",
    "error",
    "outcome",
    "analysis_dir",
    "worker_id",
    "processing_started_at",
    "updated_at",
)


def copy_lifecycle(source: Submission, target: Submission) -> None:
    """Copy status and processing fields only. Notice and contact fields stay as stored."""
    for name in _LIFECYCLE_FIELDS:
        setattr(target, name, getattr(source, name))


def _write_atomic(path: Path, text: str) -> None:
    """Replace ``path`` so a concurrent reader never sees half a record."""
    temporary = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def utc_now() -> str:
    """UTC timestamp stored on records."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: str) -> datetime:
    """Parse a timestamp written by ``utc_now``."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def add_days(stamp: str, days: int) -> str:
    """Return ``stamp`` plus ``days`` as a UTC timestamp."""
    return (parse_utc(stamp) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_id(value: str) -> str:
    if not _ID.fullmatch(value):
        raise ValueError("invalid submission id")
    return value


class PublicStore:
    """JSON records keyed by submission id. Token lookup uses the hash only."""

    def __init__(self, root: Path) -> None:
        self.root = root
        for name in ("submissions", "videos", "by_token"):
            (root / name).mkdir(parents=True, exist_ok=True)

    def save_submission(self, submission: Submission) -> None:
        """Write one internal submission record."""
        _require_id(submission.id)
        path = self._submission_path(submission.id)
        with _record_lock(path):
            _write_atomic(path, submission.model_dump_json(indent=2) + "\n")

    def update(
        self,
        submission_id: str,
        mutate: Callable[[Submission], None],
    ) -> Submission | None:
        """Reload, change, and save one record so concurrent writers keep each other's fields."""
        path = self._submission_path(_require_id(submission_id))
        with _record_lock(path):
            current = self.get(submission_id)
            if current is None:
                return None
            mutate(current)
            _write_atomic(path, current.model_dump_json(indent=2) + "\n")
            return current

    def _submission_path(self, submission_id: str) -> Path:
        return self.root / "submissions" / f"{submission_id}.json"

    def save_video(self, video: VideoInput) -> None:
        """Write the video input bound to a submission."""
        _require_id(video.submission_id)
        path = self.root / "videos" / f"{video.submission_id}.json"
        _write_atomic(path, video.model_dump_json(indent=2) + "\n")

    def remember_token(self, submission_id: str, token_hash: str) -> None:
        """Point a token hash at a submission id. The raw token is not written."""
        _require_id(submission_id)
        if not _HASH.fullmatch(token_hash):
            raise ValueError("invalid token hash")
        (self.root / "by_token" / token_hash).write_text(submission_id + "\n", encoding="utf-8")

    def get(self, submission_id: str) -> Submission | None:
        """Load a submission by id, or None when the id is unknown."""
        if not _ID.fullmatch(submission_id):
            return None
        path = self.root / "submissions" / f"{submission_id}.json"
        if not path.is_file():
            return None
        return Submission.model_validate_json(path.read_text(encoding="utf-8"))

    def get_video(self, submission_id: str) -> VideoInput | None:
        """Load the video input for a submission."""
        if not _ID.fullmatch(submission_id):
            return None
        path = self.root / "videos" / f"{submission_id}.json"
        if not path.is_file():
            return None
        return VideoInput.model_validate_json(path.read_text(encoding="utf-8"))

    def get_by_hash(self, token_hash: str) -> Submission | None:
        """Resolve a token hash to a submission."""
        if not _HASH.fullmatch(token_hash):
            return None
        pointer = self.root / "by_token" / token_hash
        if not pointer.is_file():
            return None
        return self.get(pointer.read_text(encoding="utf-8").strip())

    def list_submissions(self) -> list[Submission]:
        """Every internal submission record. Not exposed by the API."""
        records: list[Submission] = []
        for path in sorted((self.root / "submissions").glob("*.json")):
            records.append(Submission.model_validate_json(path.read_text(encoding="utf-8")))
        return records

    def unfinished_count(self) -> int:
        """Submissions that still occupy a queue slot."""
        return sum(1 for item in self.list_submissions() if item.status in UNFINISHED)

    def count_status(self, status: str) -> int:
        """How many submissions currently have ``status``."""
        return sum(1 for item in self.list_submissions() if item.status == status)

    def claim_oldest_queued(self, worker_id: str, now: str) -> Submission | None:
        """Move the oldest queued submission to ``processing`` for ``worker_id``.

        Local-worker mechanism only. The thread lock covers job threads in this
        process and ``flock`` covers processes that share this directory on one
        filesystem. Neither is the multi-host claim protocol: a worker on
        another host needs the claim moved into the shared database as one
        atomic operation.
        """
        with _CLAIM_LOCK, self._claim_file_lock():
            for candidate in self._queued_oldest_first():
                claimed = self._claim_one(candidate.id, worker_id, now)
                if claimed is not None:
                    return claimed
        return None

    def _claim_one(self, submission_id: str, worker_id: str, now: str) -> Submission | None:
        """Own one record only if it is still queued after reloading it."""
        won: list[bool] = []

        def take(item: Submission) -> None:
            if item.status != "queued":
                return
            item.status = "processing"
            item.step = "analyzing"
            item.worker_id = worker_id
            item.processing_started_at = now
            item.updated_at = now
            won.append(True)

        claimed = self.update(submission_id, take)
        return claimed if won else None

    def _queued_oldest_first(self) -> list[Submission]:
        queued = [item for item in self.list_submissions() if item.status == "queued"]
        return sorted(queued, key=lambda item: item.created_at)

    @contextmanager
    def _claim_file_lock(self) -> Iterator[None]:
        with (self.root / ".claim.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def fail_in_flight(self, message: str, now: str) -> list[Submission]:
        """Mark validating and processing submissions failed. Does not requeue.

        Every in-flight record is treated as orphaned, whichever ``worker_id``
        it carries, because this deployment has exactly one worker. This is
        not safe with a second worker: cross-worker orphan recovery needs
        leases or heartbeats.
        """
        failed: list[Submission] = []
        for item in self.list_submissions():
            if item.status not in IN_FLIGHT:
                continue
            updated = self._fail_one(item.id, message, now)
            if updated is not None:
                failed.append(updated)
        return failed

    def _fail_one(self, submission_id: str, message: str, now: str) -> Submission | None:
        changed: list[bool] = []

        def fail(current: Submission) -> None:
            if current.status not in IN_FLIGHT:
                return
            current.status = "failed"
            current.error = message
            current.updated_at = now
            changed.append(True)

        updated = self.update(submission_id, fail)
        return updated if changed else None

    def rate_path(self) -> Path:
        """JSON file of IP hashes and recent timestamps."""
        return self.root / "rate_limits.json"

    def read_rate(self) -> dict[str, list[str]]:
        """Load the rate-limit file. Missing file means no recent submissions."""
        path = self.rate_path()
        if not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {str(key): [str(item) for item in value] for key, value in payload.items()}

    def write_rate(self, payload: dict[str, list[str]]) -> None:
        """Replace the rate-limit file. Values are timestamps, keys are hashes."""
        self.rate_path().write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
