"""Local publication records: one JSON file per submission.

Stored apart from the submission projection (``PUBLIC_ROOT/report_publications``)
so an older image that rewrites projections never drops publication state.
Records hold run IDs, counts, hashes and closed-vocabulary errors only; never
personal fields, paths, URLs, fingerprints or pseudonyms.
"""

from __future__ import annotations

import fcntl
import os
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from public_site.store import _write_atomic, utc_now

PUBLICATIONS_DIRNAME = "report_publications"
PublicationState = Literal["pending", "verified", "blocked", "delete_pending"]
ErrorCode = Literal[
    "network",
    "status_4xx",
    "status_5xx",
    "checksum",
    "conflict",
    "local_changed",
    "local_missing",
    "invalid_key",
    "content_policy",
    "transform_failed",
    "staging_unsafe",
]
_ID = re.compile(r"^[0-9a-f]{32}$")
_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


class ReportPublication(BaseModel):
    """Publication state for one submission."""

    model_config = ConfigDict(extra="ignore")

    submission_id: str
    state: PublicationState = "pending"
    run_id: str | None = None
    superseded_run_ids: list[str] = Field(default_factory=list)
    attempt_run_id: str | None = None
    attempts: int = 0
    next_attempt_at: str | None = None
    last_error: ErrorCode | None = None
    blocked_rule: str | None = None
    published_at: str | None = None
    object_count: int = 0
    total_bytes: int = 0
    bundle_sha256: str | None = None
    content_policy_version: int | None = None
    delete_attempts: int = 0
    delete_requested_at: str | None = None
    delete_empty_at: str | None = None
    last_error_logged_on: str | None = None
    updated_at: str = Field(default_factory=utc_now)


class PublicationStore:
    """Atomic record files plus a per-submission lock (threads and processes)."""

    def __init__(self, public_root: Path) -> None:
        self.root = public_root / PUBLICATIONS_DIRNAME
        self._locks = public_root / PUBLICATIONS_DIRNAME / ".locks"
        self._writers = public_root / PUBLICATIONS_DIRNAME / ".writers"

    def path(self, submission_id: str) -> Path:
        """Record file for one submission. Raises ValueError for a malformed ID."""
        if not _ID.fullmatch(submission_id):
            raise ValueError("invalid submission id")
        return self.root / f"{submission_id}.json"

    def get(self, submission_id: str) -> ReportPublication | None:
        """The record, or None when absent or unreadable."""
        path = self.path(submission_id)
        if not path.is_file():
            return None
        try:
            return ReportPublication.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            return None

    def save(self, record: ReportPublication) -> None:
        """Write atomically."""
        record.updated_at = utc_now()
        self.root.mkdir(parents=True, exist_ok=True)
        _write_atomic(
            self.path(record.submission_id), record.model_dump_json(indent=2) + "\n"
        )

    def remove(self, submission_id: str) -> None:
        """Delete the record file (after its R2 prefix is confirmed empty)."""
        self.path(submission_id).unlink(missing_ok=True)

    def list(self) -> list[ReportPublication]:
        """Every readable record, by submission ID."""
        if not self.root.is_dir():
            return []
        found = []
        for path in sorted(self.root.glob("*.json")):
            if _ID.fullmatch(path.stem):
                record = self.get(path.stem)
                if record is not None:
                    found.append(record)
        return found

    def update(
        self,
        submission_id: str,
        change: Callable[[ReportPublication | None], ReportPublication | None],
    ) -> ReportPublication | None:
        """Read, change and save under the submission lock. ``None`` saves nothing."""
        with self.lock(submission_id):
            record = change(self.get(submission_id))
            if record is not None:
                self.save(record)
            return record

    @contextmanager
    def lock(self, submission_id: str) -> Iterator[None]:
        """One lock per submission, shared by publish, expiry deletion and the sweep.

        Held only for short record changes, never during uploads (expiry
        deletion holds it for its LIST/DELETE requests).
        """
        self.path(submission_id)
        with _thread_lock(submission_id):
            self._locks.mkdir(parents=True, exist_ok=True)
            fd = os.open(
                self._locks / f"{submission_id}.lock", os.O_RDWR | os.O_CREAT, 0o600
            )
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)

    @contextmanager
    def write_barrier(self, submission_id: str) -> Iterator[bool]:
        """The in-flight barrier for ``reports/<id>/``. Yields True when acquired.

        A publication attempt holds it from choosing its run until the attempt
        ends; expiry deletion and the sweep hold it while deleting. It is
        exclusive across threads and processes and never waits: a holder elsewhere
        yields False. The kernel releases it when the holding process dies, so a
        crash never leaves it held. Always taken before :meth:`lock`.
        """
        self.path(submission_id)
        self._writers.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            self._writers / f"{submission_id}.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            yield True
        finally:
            os.close(fd)


@contextmanager
def _thread_lock(submission_id: str) -> Iterator[None]:
    with _thread_locks_guard:
        lock = _thread_locks.setdefault(submission_id, threading.Lock())
    with lock:
        yield
