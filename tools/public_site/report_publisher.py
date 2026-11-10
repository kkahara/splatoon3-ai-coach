"""Write-through publishing of completed reports to ``reports/<id>/<run_id>/``.

Local storage stays the only thing that serves results. All network work runs
on one background thread; the worker and service hooks only change local
records. The publisher holds a ``ReportWriter`` (HEAD, PUT, read-back, LIST)
and can never delete; expiry deletion uses a separate ``ReportDeleter`` built
only when ``PUBLIC_REPORT_EXPIRY_DELETE=1``.

Logs carry submission IDs, run IDs, closed-vocabulary error codes and counts;
never paths, URLs, fingerprints, pseudonyms or personal fields.
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from loguru import logger

from public_site.models import Submission
from public_site.report_bundle import (
    BUNDLE_NAME,
    BundleError,
    PreparedBundle,
    PreparedFile,
    prepare_bundle,
    run_key,
)
from public_site.report_canary import CanaryStore
from public_site.report_content import CONTENT_POLICY_VERSION, KnownValues
from public_site.report_known import (
    KnownResult,
    database_lookup,
    recordings_lookup,
    submission_known_values,
)
from public_site.report_publications import (
    ErrorCode,
    PublicationStore,
    ReportPublication,
)
from public_site.report_staging import StagingArea, StagingRun, StagingUnsafe
from public_site.storage import (
    LocalStorage,
    R2Storage,
    ReportEraser,
    ReportWriter,
    file_sha256,
)
from public_site.store import PublicStore, parse_utc

PUBLISH_BACKOFF = (60, 300, 900, 3600, 21600)
PUBLISH_BACKOFF_TAIL = 86400
DELETE_BACKOFF = (60, 300, 900)
DELETE_BACKOFF_TAIL = 3600
LOUD_AFTER_FAILURES = 6
IDLE_SECONDS = 30.0
ERROR_PAUSE_SECONDS = 60.0
BARRIER_DEFER_SECONDS = 60
# Well above the storage request timeout (60 s), so a PUT abandoned by an
# earlier attempt has landed or failed before the record is forgotten.
DELETE_SETTLE_SECONDS = 600
DELETE_RECHECK_SECONDS = 60
MAX_RECORDS_PER_PASS = 50
STAGING_RECOVERY_SECONDS = 3600.0
AUTO_SWEEP_SECONDS = 86400.0
_NEW_RUN_CODES = frozenset({"checksum", "conflict", "local_changed", "staging_unsafe"})
_BLOCKING_CODES = frozenset({"content_policy", "transform_failed"})
Clock = Callable[[], datetime]


def new_run_id(now: datetime) -> str:
    """``YYYYMMDDTHHMMSSZ-<8 hex>``."""
    return f"{now.astimezone(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def publish_backoff(attempts: int) -> int:
    """Seconds before the next publish attempt after ``attempts`` failures."""
    if attempts <= 0:
        return 0
    return (
        PUBLISH_BACKOFF[attempts - 1]
        if attempts <= len(PUBLISH_BACKOFF)
        else PUBLISH_BACKOFF_TAIL
    )


def delete_backoff(attempts: int) -> int:
    """Seconds before the next expiry-deletion attempt after ``attempts`` failures."""
    if attempts <= 0:
        return 0
    return (
        DELETE_BACKOFF[attempts - 1]
        if attempts <= len(DELETE_BACKOFF)
        else DELETE_BACKOFF_TAIL
    )


class PublishFailure(Exception):
    """One failed attempt, by closed-vocabulary code."""

    def __init__(self, code: ErrorCode, rule: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.rule = rule


class _RunInvalidated(Exception):
    """The run's record or submission changed; stop writing to its prefix."""


def unexpected_error_code(exc: BaseException) -> ErrorCode:
    """Closed-vocabulary code for an exception no other handler classified.

    A missing local file is ``local_missing``; anything else (another filesystem
    error or an unexpected exception) is ``local_changed``. Either backs off and
    the next attempt starts a new run.
    """
    return "local_missing" if isinstance(exc, FileNotFoundError) else "local_changed"


def storage_error_code(exc: OSError) -> ErrorCode:
    """Map a storage exception (status and operation only) to the closed vocabulary."""
    text = str(exc)
    if "(checksum)" in text:
        return "checksum"
    for status in range(400, 600):
        if f"({status})" in text:
            return "status_4xx" if status < 500 else "status_5xx"
    return "network"


class ReportDeleter:
    """Expiry deletion: LIST and DELETE under ``reports/<id>/`` only."""

    def __init__(self, eraser: ReportEraser) -> None:
        self._eraser = eraser

    def delete_submission(self, submission_id: str) -> tuple[int, bool]:
        """Delete every object under the submission prefix.

        Returns how many objects were deleted and whether LIST is then empty.
        """
        prefix = f"reports/{submission_id}/"
        found = self._eraser.list_objects(prefix)
        for item in found:
            self._eraser.delete(item.key)
        return len(found), not self._eraser.list_objects(prefix)


@dataclass
class _ActiveRun:
    run_id: str
    staging: StagingRun
    prepared: PreparedBundle


class ReportPublisher:
    """Publishes pending records, deletes expired ones, and runs the optional sweep."""

    def __init__(
        self,
        *,
        store: PublicStore,
        publications: PublicationStore,
        staging: StagingArea,
        known: Callable[[Submission], KnownResult],
        writer: ReportWriter | None = None,
        deleter: ReportDeleter | None = None,
        sweeper=None,
        clock: Clock | None = None,
        canary: CanaryStore | None = None,
    ) -> None:
        self.store = store
        self.canary = canary
        self.publications = publications
        self.staging = staging
        self.writer = writer
        self.deleter = deleter
        self.sweeper = sweeper
        self._known = known
        self._clock = clock or (lambda: datetime.now(UTC))
        self._active: dict[str, _ActiveRun] = {}
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_recovery = 0.0
        self._last_sweep = 0.0

    # Hooks: local record changes only, never network I/O.

    def submission_completed(self, submission_id: str) -> None:
        """A report completed: queue it for publishing (only when publishing is on)."""
        if self.writer is None:
            return
        self.publications.update(submission_id, lambda r: self._queue(submission_id, r))
        self.wake()

    def media_added(self, submission_id: str) -> None:
        """Clips or frames were filled in later: publish a new run."""
        if self.writer is None:
            return

        def change(record: ReportPublication | None) -> ReportPublication | None:
            if record is None or record.state not in {"verified", "pending"}:
                return None
            return self._queue(submission_id, record)

        self.publications.update(submission_id, change)
        self.wake()

    def submission_expired(self, submission_id: str) -> None:
        """The submission expired: mark its record ``delete_pending`` (local only)."""

        def change(record: ReportPublication | None) -> ReportPublication | None:
            if record is None or record.state == "delete_pending":
                return None
            record.state = "delete_pending"
            record.attempt_run_id = None
            record.delete_requested_at = _stamp(self._clock())
            record.next_attempt_at = record.delete_requested_at
            record.delete_attempts = 0
            record.delete_empty_at = None
            return record

        self.publications.update(submission_id, change)
        self.wake()

    def _queue(
        self, submission_id: str, record: ReportPublication | None
    ) -> ReportPublication | None:
        if record is not None and record.state in {"blocked", "delete_pending"}:
            return None
        record = record or ReportPublication(submission_id=submission_id)
        record.state = "pending"
        record.attempt_run_id = None
        record.attempts = 0
        record.last_error = None
        record.next_attempt_at = _stamp(self._clock())
        return record

    # Thread lifecycle.

    def start(self) -> None:
        """Start the background thread (startup recovery runs on it first)."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="report-publisher", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the thread to finish and wait briefly."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def wake(self) -> None:
        """Process records now instead of waiting for the idle timeout."""
        self._wake.set()

    def _loop(self) -> None:
        """Startup, then passes. Startup is retried until it succeeds and no
        record is processed before it has. Per-record failures are handled in
        ``run_once``; the pause is the last resort for anything else.
        """
        started = False
        while not self._stop.is_set():
            worked = False
            try:
                if not started:
                    self.startup()
                    started = True
                worked = self.run_once()
                self._periodic()
            except Exception as exc:  # noqa: BLE001 — the thread must survive
                logger.error("report publisher error ({})", type(exc).__name__)
                self._stop.wait(ERROR_PAUSE_SECONDS)
                continue
            if not worked:
                self._wake.wait(IDLE_SECONDS)
                self._wake.clear()

    def _periodic(self) -> None:
        now = self._clock().timestamp()
        if now - self._last_recovery >= STAGING_RECOVERY_SECONDS:
            self._last_recovery = now
            self.staging.recover()
            self._prune_canaries()
        if self.sweeper is not None and now - self._last_sweep >= AUTO_SWEEP_SECONDS:
            self._last_sweep = now
            self.sweeper.run_automatic()

    def startup(self) -> None:
        """Recover abandoned staging, then move records of expired submissions to
        ``delete_pending``. Runs before any record is processed. Local only.
        """
        self.staging.recover()
        self._last_recovery = self._clock().timestamp()
        for record in self.publications.list():
            if record.state == "delete_pending":
                continue
            submission = self.store.get(record.submission_id)
            if submission is not None and submission.status == "expired":
                self.submission_expired(record.submission_id)
        self._prune_canaries()

    def _prune_canaries(self) -> None:
        """Drop canaries no publication can need (expired without a record, orphans)."""
        if self.canary is None:
            return
        try:
            self.canary.prune(
                self.store, lambda sid: self.publications.get(sid) is not None
            )
        except OSError as exc:
            logger.warning("review-code canary cleanup failed ({})", type(exc).__name__)

    def _remove_canary(self, submission_id: str) -> None:
        if self.canary is None:
            return
        try:
            self.canary.remove(submission_id)
        except OSError as exc:
            logger.warning("review-code canary removal failed ({})", type(exc).__name__)

    # Processing.

    def run_once(self) -> bool:
        """Attempt due records, most overdue first. True when something was attempted.

        Stops after the first attempt that completes (successfully or with a
        normal recorded failure). A record whose attempt raises is backed off on
        its own and the next due record is tried in the same pass. Each record
        is tried at most once per pass, and at most ``MAX_RECORDS_PER_PASS``.
        """
        self._drop_stale_runs()
        now = self._clock()
        due = [r for r in self.publications.list() if self._is_due(r, now)]
        due.sort(key=lambda r: (r.state != "delete_pending", r.next_attempt_at or ""))
        for record in due[:MAX_RECORDS_PER_PASS]:
            if self._attempt_record(record):
                break
        return bool(due)

    def _attempt_record(self, record: ReportPublication) -> bool:
        """One record's attempt. False when it raised and was backed off."""
        try:
            if record.state == "delete_pending":
                self._delete(record.submission_id)
            else:
                self._publish(record.submission_id)
        except Exception as exc:  # noqa: BLE001 — isolate this record
            code = unexpected_error_code(exc)
            logger.warning(
                "report record attempt raised {} ({}): submission={}",
                type(exc).__name__,
                code,
                record.submission_id,
            )
            self._close(record.submission_id)
            self._back_off(record.submission_id, record.state, code)
            return False
        return True

    def _back_off(self, submission_id: str, state: str, code: ErrorCode) -> None:
        """Push one record's next attempt out by its normal backoff."""
        now = self._clock()

        def change(record: ReportPublication | None) -> ReportPublication | None:
            if record is None or record.state != state:
                return None
            record.last_error = code
            if state == "delete_pending":
                record.delete_attempts += 1
                delay = delete_backoff(record.delete_attempts)
            else:
                record.attempts += 1
                record.attempt_run_id = None
                delay = publish_backoff(record.attempts)
            record.next_attempt_at = _stamp(now + timedelta(seconds=delay))
            return record

        self.publications.update(submission_id, change)

    def _is_due(self, record: ReportPublication, now: datetime) -> bool:
        if record.state == "delete_pending" and self.deleter is None:
            return False
        if record.state == "pending" and self.writer is None:
            return False
        if record.state not in {"pending", "delete_pending"}:
            return False
        return record.next_attempt_at is None or parse_utc(record.next_attempt_at) <= now

    def _drop_stale_runs(self) -> None:
        for submission_id, active in list(self._active.items()):
            record = self.publications.get(submission_id)
            if (
                record is None
                or record.state != "pending"
                or record.attempt_run_id != active.run_id
            ):
                self._close(submission_id)

    def _close(self, submission_id: str) -> None:
        active = self._active.pop(submission_id, None)
        if active is not None:
            active.staging.close()

    def _publish(self, submission_id: str) -> None:
        """One attempt, holding the write barrier from choosing the run to the end."""
        with self.publications.write_barrier(submission_id) as held:
            if not held:
                self._defer(submission_id, "pending")
                return
            try:
                self._attempt(submission_id)
            except _RunInvalidated:
                self._close(submission_id)
            except PublishFailure as exc:
                self._fail(submission_id, exc.code, exc.rule)
            except Exception as exc:  # noqa: BLE001 — never retry without backoff
                code = unexpected_error_code(exc)
                logger.warning(
                    "report publish attempt raised {} ({}): submission={}",
                    type(exc).__name__,
                    code,
                    submission_id,
                )
                self._close(submission_id)
                self._fail(submission_id, code, None, new_run=True)

    def _attempt(self, submission_id: str) -> None:
        active = self._begin(submission_id)
        if active is None:
            return
        for item in active.prepared.files:
            self._ensure_current(submission_id, active.run_id)
            self._upload(submission_id, active.run_id, item)
        self._commit(submission_id, active)

    def _ensure_current(self, submission_id: str, run_id: str) -> None:
        """Stop before the next object when the run was invalidated (expiry, a
        newer run, a removed record or a submission that is no longer complete).
        """
        record = self.publications.get(submission_id)
        if record is None or record.state != "pending" or record.attempt_run_id != run_id:
            raise _RunInvalidated
        submission = self.store.get(submission_id)
        if submission is None or submission.status != "complete":
            raise _RunInvalidated

    def _defer(self, submission_id: str, state: str) -> None:
        """Another holder has the write barrier: try again shortly. Not a failure."""

        def change(record: ReportPublication | None) -> ReportPublication | None:
            if record is None or record.state != state:
                return None
            later = self._clock() + timedelta(seconds=BARRIER_DEFER_SECONDS)
            record.next_attempt_at = _stamp(later)
            return record

        self.publications.update(submission_id, change)
        logger.info("report prefix busy, deferred: submission={}", submission_id)

    def _begin(self, submission_id: str) -> _ActiveRun | None:
        with self.publications.lock(submission_id):
            record = self.publications.get(submission_id)
            if record is None or record.state != "pending":
                return None
            submission = self.store.get(submission_id)
            if submission is not None and submission.status == "expired":
                self._expire_locked(record)
                return None
            if (
                submission is None
                or submission.status != "complete"
                or not submission.analysis_dir
            ):
                raise PublishFailure("local_missing")
            active = self._active.get(submission_id)
            if active is not None and active.run_id == record.attempt_run_id:
                return active
            self._close(submission_id)
            run_id = new_run_id(self._clock())
            record.attempt_run_id = run_id
            self.publications.save(record)
        return self._prepare(submission, run_id)

    def _prepare(self, submission: Submission, run_id: str) -> _ActiveRun:
        try:
            staging = self.staging.open_run(submission.id, run_id)
        except (StagingUnsafe, OSError):
            raise PublishFailure("staging_unsafe") from None
        try:
            known = self._known_values(submission)
            prepared = prepare_bundle(
                Path(submission.analysis_dir or ""),
                submission_id=submission.id,
                run_id=run_id,
                outcome=submission.outcome,
                created_at=_stamp(self._clock()),
                known=known,
                staging=staging,
            )
        except BundleError as exc:
            staging.close()
            raise PublishFailure(exc.kind, exc.rule) from None
        except BaseException:
            staging.close()
            raise
        prepared.published_json = {}
        active = _ActiveRun(run_id=run_id, staging=staging, prepared=prepared)
        self._active[submission.id] = active
        return active

    def _known_values(self, submission: Submission) -> KnownValues:
        """The canaries, or ``network`` (retry later) when any group is unknown."""
        result = self._known(submission)
        if result.unchecked:
            logger.warning(
                "report canary values unavailable: submission={} groups={}",
                submission.id,
                ",".join(result.unchecked),
            )
            raise PublishFailure("network")
        return result.known

    def _upload(self, submission_id: str, run_id: str, item: PreparedFile) -> None:
        assert self.writer is not None
        key = run_key(submission_id, run_id, item.path)
        try:
            existing = self.writer.head(key)
            if existing is not None:
                self._verify_existing(key, existing.size_bytes, existing.sha256, item)
                return
            self.writer.put_file(
                key,
                item.source,
                item.content_type,
                item.published_sha256,
                item.published_size,
            )
            self._read_back(key, item.published_size, item.published_sha256)
        except FileNotFoundError:
            raise PublishFailure("checksum") from None
        except OSError as exc:
            raise PublishFailure(storage_error_code(exc)) from None

    def _verify_existing(
        self, key: str, size: int, sha256: str | None, item: PreparedFile
    ) -> None:
        if size != item.published_size or sha256 != item.published_sha256:
            raise PublishFailure("conflict")
        found = self.writer.read_sha256(key)  # type: ignore[union-attr]
        if found != (item.published_size, item.published_sha256):
            raise PublishFailure("conflict")

    def _read_back(self, key: str, size: int, sha256: str) -> None:
        found = self.writer.read_sha256(key)  # type: ignore[union-attr]
        if found != (size, sha256):
            raise PublishFailure("checksum")

    def _commit(self, submission_id: str, active: _ActiveRun) -> None:
        """Re-check under the lock, PUT the bundle (barrier held, lock not),
        re-check under the lock again, then mark ``verified``.
        """
        with self.publications.lock(submission_id):
            if self._still_current(submission_id, active) is None:
                return
        _recheck_local(active.prepared)
        self._put_bundle(submission_id, active)
        with self.publications.lock(submission_id):
            record = self._still_current(submission_id, active)
            if record is None:
                return
            self._mark_verified(record, active)
        self._close(submission_id)
        logger.info(
            "report published: submission={} run={} objects={}",
            submission_id,
            active.run_id,
            len(active.prepared.files) + 1,
        )

    def _still_current(
        self, submission_id: str, active: _ActiveRun
    ) -> ReportPublication | None:
        """The record when this run may still commit (call under the record lock).

        Otherwise closes the run, and moves an expired submission's record to
        ``delete_pending``.
        """
        record = self.publications.get(submission_id)
        if (
            record is None
            or record.state != "pending"
            or record.attempt_run_id != active.run_id
        ):
            self._close(submission_id)
            return None
        submission = self.store.get(submission_id)
        if submission is None or submission.status != "complete":
            if submission is not None and submission.status == "expired":
                self._expire_locked(record)
            self._close(submission_id)
            return None
        return record

    def _put_bundle(self, submission_id: str, active: _ActiveRun) -> None:
        prepared = active.prepared
        key = run_key(submission_id, active.run_id, BUNDLE_NAME)
        size = len(prepared.bundle_bytes)
        try:
            if self.writer.head(key) is not None:  # type: ignore[union-attr]
                raise PublishFailure("conflict")
            assert prepared.bundle_source is not None
            if file_sha256(prepared.bundle_source) != prepared.bundle_sha256:
                raise PublishFailure("local_changed")
            self.writer.put_file(  # type: ignore[union-attr]
                key,
                prepared.bundle_source,
                "application/json",
                prepared.bundle_sha256,
                size,
            )
            self._read_back(key, size, prepared.bundle_sha256)
        except FileNotFoundError:
            raise PublishFailure("checksum") from None
        except OSError as exc:
            raise PublishFailure(storage_error_code(exc)) from None

    def _mark_verified(self, record: ReportPublication, active: _ActiveRun) -> None:
        prepared = active.prepared
        if record.run_id and record.run_id != active.run_id:
            if record.run_id not in record.superseded_run_ids:
                record.superseded_run_ids.append(record.run_id)
        record.state = "verified"
        record.run_id = active.run_id
        record.attempt_run_id = None
        record.attempts = 0
        record.next_attempt_at = None
        record.last_error = None
        record.blocked_rule = None
        record.published_at = _stamp(self._clock())
        record.object_count = len(prepared.files) + 1
        record.total_bytes = prepared.bundle.total_bytes + len(prepared.bundle_bytes)
        record.bundle_sha256 = prepared.bundle_sha256
        record.content_policy_version = CONTENT_POLICY_VERSION
        self.publications.save(record)

    def _expire_locked(self, record: ReportPublication) -> None:
        record.state = "delete_pending"
        record.attempt_run_id = None
        record.delete_requested_at = _stamp(self._clock())
        record.next_attempt_at = record.delete_requested_at
        record.delete_empty_at = None
        self.publications.save(record)
        self._close(record.submission_id)

    def _fail(
        self,
        submission_id: str,
        code: ErrorCode,
        rule: str | None,
        *,
        new_run: bool = False,
    ) -> None:
        new_run = new_run or code in _NEW_RUN_CODES
        if new_run or code in _BLOCKING_CODES:
            self._close(submission_id)
        now = self._clock()
        with self.publications.lock(submission_id):
            record = self.publications.get(submission_id)
            if record is None or record.state != "pending":
                return
            record.last_error = code
            if code in _BLOCKING_CODES:
                record.state = "blocked"
                record.blocked_rule = rule if code == "content_policy" else code
                record.attempt_run_id = None
                record.next_attempt_at = None
            else:
                record.attempts += 1
                if new_run:
                    record.attempt_run_id = None
                record.next_attempt_at = _stamp(
                    now + timedelta(seconds=publish_backoff(record.attempts))
                )
            self._log_failure(record, now)
            self.publications.save(record)

    def _log_failure(self, record: ReportPublication, now: datetime) -> None:
        if record.state == "blocked":
            logger.warning(
                "report blocked: submission={} rule={}",
                record.submission_id,
                record.blocked_rule,
            )
            return
        today = now.strftime("%Y-%m-%d")
        if (
            record.attempts >= LOUD_AFTER_FAILURES
            and record.last_error_logged_on != today
        ):
            record.last_error_logged_on = today
            logger.error(
                "report publishing still failing: submission={} error={} attempts={}",
                record.submission_id,
                record.last_error,
                record.attempts,
            )
            return
        logger.warning(
            "report publish attempt failed: submission={} error={} attempts={}",
            record.submission_id,
            record.last_error,
            record.attempts,
        )

    def _delete(self, submission_id: str) -> None:
        """Expiry deletion. Never runs while a publication attempt holds the prefix."""
        assert self.deleter is not None
        self._close(submission_id)
        with self.publications.write_barrier(submission_id) as held:
            if not held:
                self._defer(submission_id, "delete_pending")
                return
            with self.publications.lock(submission_id):
                record = self.publications.get(submission_id)
                if record is None or record.state != "delete_pending":
                    return
                deleted, code = self._delete_prefix(submission_id)
                if code is not None:
                    self._delete_failed(record, code)
                    return
                if not self._settled(record, deleted):
                    return
                self.publications.remove(submission_id)
        self._remove_canary(submission_id)
        logger.info("report prefix deleted: submission={}", submission_id)

    def _settled(self, record: ReportPublication, deleted: int) -> bool:
        """Whether the empty prefix may be forgotten (call under the record lock).

        An empty LIST is final only when it comes at least
        ``DELETE_SETTLE_SECONDS`` after the prefix was first seen empty with
        nothing deleted since; until then the record stays ``delete_pending``
        and is re-listed every ``DELETE_RECHECK_SECONDS``, deleting any late
        object again (which restarts the settle time).
        """
        now = self._clock()
        if deleted or record.delete_empty_at is None:
            record.delete_empty_at = _stamp(now)
        settle_at = parse_utc(record.delete_empty_at) + timedelta(
            seconds=DELETE_SETTLE_SECONDS
        )
        if now >= settle_at:
            return True
        recheck = now + timedelta(seconds=DELETE_RECHECK_SECONDS)
        record.next_attempt_at = _stamp(min(recheck, settle_at))
        record.last_error = None
        self.publications.save(record)
        return False

    def _delete_prefix(self, submission_id: str) -> tuple[int, ErrorCode | None]:
        """Delete and verify the prefix empty: (objects deleted, error code or None)."""
        assert self.deleter is not None
        try:
            deleted, empty = self.deleter.delete_submission(submission_id)
        except OSError as exc:
            return 0, storage_error_code(exc)
        except ValueError:
            return 0, "invalid_key"
        except Exception as exc:  # noqa: BLE001 — never retry without backoff
            logger.warning(
                "report prefix deletion raised {}: submission={}",
                type(exc).__name__,
                submission_id,
            )
            return 0, "network"
        return deleted, None if empty else "network"

    def _delete_failed(self, record: ReportPublication, code: ErrorCode) -> None:
        record.delete_empty_at = None
        record.delete_attempts += 1
        record.last_error = code
        delay = delete_backoff(record.delete_attempts)
        record.next_attempt_at = _stamp(self._clock() + timedelta(seconds=delay))
        self.publications.save(record)
        logger.warning(
            "report prefix deletion failed: submission={} error={} attempts={}",
            record.submission_id,
            code,
            record.delete_attempts,
        )


def _recheck_local(prepared: PreparedBundle) -> None:
    """Re-hash every local file against ``local_sha256`` and every staged copy
    against ``published_sha256``, in full, before commit. Any change is
    ``local_changed``.
    """
    for item in prepared.files:
        checks = [(item.local_path, item.local_sha256)]
        if item.staged:
            checks.append((item.source, item.published_sha256))
        for path, expected in checks:
            try:
                if file_sha256(path) != expected:
                    raise PublishFailure("local_changed")
            except OSError:
                raise PublishFailure("local_changed") from None


def open_report_storage(settings) -> R2Storage:
    """The R2 client for reports. Built only when a report setting is on."""
    return R2Storage(
        account_id=settings.r2_account_id,
        access_key_id=settings.r2_access_key_id,
        secret_access_key=settings.r2_secret_access_key,
        bucket=settings.r2_bucket,
        cache_dir=settings.root / "cache",
    )


def build_report_publisher(
    settings,
    store: PublicStore,
    service,
    storage: LocalStorage | R2Storage | None = None,
    clock: Clock | None = None,
) -> ReportPublisher | None:
    """The publisher for the settings in effect, or None when all three are off.

    With every setting off no report storage client is built, so no report R2
    request can be sent. Each capability is built only when its own setting is
    on: the writer (no delete) for publishing, the deleter for expiry deletion,
    the sweeper for the automatic sweep.

    Raises:
        ValueError: The report settings are unusable (see ``report_problem``).
    """
    problem = settings.report_problem()
    if problem is not None:
        raise ValueError(problem)
    if not settings.reports_enabled:
        return None
    if settings.report_publish and not settings.report_expiry_delete:
        logger.warning(
            "PUBLIC_REPORT_PUBLISH=1 without PUBLIC_REPORT_EXPIRY_DELETE=1: expired "
            "reports stay in R2 (records kept as delete_pending) until deletion is enabled"
        )
    storage = storage or open_report_storage(settings)
    publications = PublicationStore(settings.root)
    lookup = recordings_lookup(service) or database_lookup(settings)
    canary = CanaryStore(settings.root)
    sweeper = None
    if settings.report_sweep:
        from public_site.report_sweep import ReportSweeper

        sweeper = ReportSweeper(
            eraser=ReportEraser(storage),
            store=store,
            publications=publications,
            clock=clock,
        )
    return ReportPublisher(
        store=store,
        publications=publications,
        staging=StagingArea(settings.root),
        known=lambda submission: submission_known_values(
            settings, store, submission, lookup, canary
        ),
        writer=ReportWriter(storage) if settings.report_publish else None,
        deleter=ReportDeleter(ReportEraser(storage))
        if settings.report_expiry_delete
        else None,
        sweeper=sweeper,
        clock=clock,
        canary=canary,
    )
