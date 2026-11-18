"""Report sweep (plan / apply) and ``status`` classification over ``reports/``.

The sweep only ever proposes three kinds of deletion: an expired submission's
whole prefix, a superseded committed run, or a stale partial run. Everything
else is skipped with a reason. Apply needs the plan digest, recomputes the
plan, and re-checks each row under the per-submission lock just before
deleting. ``allow_large`` lifts only the size threshold.

Output holds submission IDs, prefixes, counts, bytes and reasons; never
credentials, URLs, signatures or object contents.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from loguru import logger

from public_site.report_bundle import BUNDLE_NAME
from public_site.report_publications import PublicationStore, ReportPublication
from public_site.report_staging import StagingArea, valid_run_id
from public_site.storage import ObjectStat, ReportEraser, ReportLister
from public_site.store import PublicStore, parse_utc

MIN_AGE_SECONDS = 24 * 3600
THRESHOLD_MIN = 5
THRESHOLD_FRACTION = 0.10
_SUBMISSION = re.compile(r"^[0-9a-f]{32}$")
_SETTLED = {"complete", "failed"}
Clock = Callable[[], datetime]


class SweepAborted(Exception):
    """Apply refused: ``digest_mismatch`` or ``threshold``."""


@dataclass
class RunGroup:
    """Objects of one ``reports/<id>/<run_id>/`` prefix."""

    submission_id: str
    run_id: str
    objects: list[ObjectStat] = field(default_factory=list)

    @property
    def prefix(self) -> str:
        """``reports/<id>/<run_id>/``."""
        return f"reports/{self.submission_id}/{self.run_id}/"

    @property
    def committed(self) -> bool:
        """True when any generation under the run has ``bundle.json``."""
        # Legacy: reports/<id>/<run>/bundle.json
        # Fenced: reports/<id>/<run>/attempt-<n>/bundle.json
        return any(
            item.key == self.prefix + BUNDLE_NAME
            or item.key.endswith("/" + BUNDLE_NAME)
            for item in self.objects
        )

    @property
    def size_bytes(self) -> int:
        """Total object bytes."""
        return sum(item.size_bytes for item in self.objects)

    def newest_age(self, now: datetime) -> float | None:
        """Seconds since the newest object was written (None when unknown)."""
        times = [item.last_modified for item in self.objects if item.last_modified]
        return None if not times else (now - max(times)).total_seconds()


@dataclass
class Grouped:
    """Objects under ``reports/`` grouped by submission and run."""

    runs: dict[str, dict[str, RunGroup]] = field(default_factory=dict)
    invalid: dict[str, list[ObjectStat]] = field(default_factory=dict)


def group_objects(objects: list[ObjectStat]) -> Grouped:
    """Split ``reports/<id>/<run_id>/...`` keys; anything else is an invalid prefix."""
    grouped = Grouped()
    for item in objects:
        parts = item.key.split("/")
        if (
            len(parts) >= 4
            and parts[0] == "reports"
            and _SUBMISSION.fullmatch(parts[1])
            and valid_run_id(parts[2])
        ):
            runs = grouped.runs.setdefault(parts[1], {})
            runs.setdefault(parts[2], RunGroup(parts[1], parts[2])).objects.append(item)
            continue
        prefix = "/".join(parts[:2]) + "/" if len(parts) > 2 else item.key
        grouped.invalid.setdefault(prefix, []).append(item)
    return grouped


@dataclass
class SweepRow:
    """One proposed deletion."""

    submission_id: str
    prefix: str
    reason: str
    object_count: int
    bytes: int
    projection_status: str | None
    record_state: str | None
    newest_object_age_seconds: float | None = None

    def digest_fields(self) -> dict:
        """Fields covered by the plan digest (age changes with time and is excluded)."""
        payload = asdict(self)
        payload.pop("newest_object_age_seconds")
        return payload


@dataclass
class SweepSkip:
    """One prefix the sweep will not touch, and why."""

    submission_id: str | None
    prefix: str
    reason: str


@dataclass
class SweepPlan:
    """A dry-run plan. ``digest`` must be passed back to apply it."""

    mode: str
    bucket: str
    generated_at: str
    threshold_min: int
    threshold_fraction: float
    limit: int
    total_prefixes: int
    rows: list[SweepRow]
    skips: list[SweepSkip]
    digest: str

    @property
    def exceeds_threshold(self) -> bool:
        """True when more prefixes would be deleted than the limit allows."""
        return len(self.rows) > self.limit

    def as_dict(self) -> dict:
        """JSON-ready structure (also used for ``--json``)."""
        payload = asdict(self)
        payload["exceeds_threshold"] = self.exceeds_threshold
        payload["totals"] = {
            "rows": len(self.rows),
            "objects": sum(row.object_count for row in self.rows),
            "bytes": sum(row.bytes for row in self.rows),
            "skips": len(self.skips),
        }
        return payload


@dataclass
class SweepResult:
    """Per-row outcomes of an apply."""

    deleted: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped_on_recheck: list[str] = field(default_factory=list)


def _digest(rows: list[SweepRow]) -> str:
    canonical = json.dumps(
        [row.digest_fields() for row in rows], sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class ReportSweeper:
    """Plans and applies sweeps with a deleting view (LIST and DELETE only)."""

    def __init__(
        self,
        *,
        eraser: ReportEraser,
        store: PublicStore,
        publications: PublicationStore,
        clock: Clock | None = None,
        threshold_min: int = THRESHOLD_MIN,
        threshold_fraction: float = THRESHOLD_FRACTION,
    ) -> None:
        self.eraser = eraser
        self.store = store
        self.publications = publications
        self._clock = clock or (lambda: datetime.now(UTC))
        self.threshold_min = threshold_min
        self.threshold_fraction = threshold_fraction

    def plan(self, mode: str = "dry_run") -> SweepPlan:
        """Classify every prefix under ``reports/``. Read-only (LIST)."""
        now = self._clock()
        grouped = group_objects(self.eraser.list_objects("reports/"))
        rows: list[SweepRow] = []
        skips = [
            SweepSkip(None, prefix, "invalid_prefix")
            for prefix in sorted(grouped.invalid)
        ]
        for submission_id in sorted(grouped.runs):
            found_rows, found_skips = self._classify(
                submission_id, grouped.runs[submission_id], now
            )
            rows.extend(found_rows)
            skips.extend(found_skips)
        total = sum(len(runs) for runs in grouped.runs.values()) + len(grouped.invalid)
        limit = max(self.threshold_min, int(total * self.threshold_fraction))
        return SweepPlan(
            mode=mode,
            bucket=self.eraser.bucket,
            generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            threshold_min=self.threshold_min,
            threshold_fraction=self.threshold_fraction,
            limit=limit,
            total_prefixes=total,
            rows=rows,
            skips=skips,
            digest=_digest(rows),
        )

    def _classify(
        self, submission_id: str, runs: dict[str, RunGroup], now: datetime
    ) -> tuple[list[SweepRow], list[SweepSkip]]:
        whole = f"reports/{submission_id}/"
        submission = self.store.get(submission_id)
        record = self.publications.get(submission_id)
        state = None if record is None else record.state
        if submission is None:
            return [], [SweepSkip(submission_id, whole, "projection_absent")]
        if submission.status == "expired":
            groups = list(runs.values())
            row = SweepRow(
                submission_id=submission_id,
                prefix=whole,
                reason="expired_submission",
                object_count=sum(len(g.objects) for g in groups),
                bytes=sum(g.size_bytes for g in groups),
                projection_status=submission.status,
                record_state=state,
                newest_object_age_seconds=_min_age(groups, now),
            )
            return [row], []
        if submission.status not in _SETTLED:
            return [], [SweepSkip(submission_id, whole, "processing")]
        if record is None:
            return [], [SweepSkip(submission_id, whole, "complete_without_record")]
        rows: list[SweepRow] = []
        skips: list[SweepSkip] = []
        for run_id in sorted(runs):
            group = runs[run_id]
            reason = _run_reason(group, record, now)
            if reason in {"superseded_run", "stale_partial_run"}:
                rows.append(_run_row(group, reason, submission.status, state, now))
            else:
                skips.append(SweepSkip(submission_id, group.prefix, reason))
        return rows, skips

    def apply(self, confirm: str, *, allow_large: bool = False) -> SweepResult:
        """Recompute the plan; delete its rows only if the digest matches.

        Raises:
            SweepAborted: ``digest_mismatch`` or ``threshold``.
        """
        plan = self.plan(mode="apply")
        if plan.digest != confirm:
            raise SweepAborted("digest_mismatch")
        if plan.exceeds_threshold and not allow_large:
            raise SweepAborted("threshold")
        result = SweepResult()
        for row in plan.rows:
            outcome = self._apply_row(row)
            getattr(result, outcome).append(row.prefix)
        logger.info(
            "report sweep applied: deleted={} failed={} skipped_on_recheck={}",
            len(result.deleted),
            len(result.failed),
            len(result.skipped_on_recheck),
        )
        return result

    def run_automatic(self) -> SweepResult | None:
        """The daily sweep: same planner and recheck, never ``allow_large``."""
        try:
            plan = self.plan(mode="automatic")
            if plan.exceeds_threshold:
                logger.warning(
                    "automatic report sweep skipped: {} rows exceed the limit {}",
                    len(plan.rows),
                    plan.limit,
                )
                return None
            return self.apply(plan.digest) if plan.rows else SweepResult()
        except (OSError, SweepAborted, ValueError) as exc:
            logger.warning("automatic report sweep failed ({})", type(exc).__name__)
            return None

    def _apply_row(self, row: SweepRow) -> str:
        """Re-check and delete one row. A publication attempt in flight on the
        submission (write barrier held) skips the row: nothing is deleted while a
        writer can still add objects under the prefix.
        """
        with self.publications.write_barrier(row.submission_id) as held:
            if not held:
                return "skipped_on_recheck"
            return self._apply_locked(row)

    def _apply_locked(self, row: SweepRow) -> str:
        with self.publications.lock(row.submission_id):
            now = self._clock()
            prefix_runs = group_objects(
                self.eraser.list_objects(f"reports/{row.submission_id}/")
            ).runs.get(row.submission_id, {})
            fresh, _skips = self._classify(row.submission_id, prefix_runs, now)
            if not any(r.prefix == row.prefix and r.reason == row.reason for r in fresh):
                return "skipped_on_recheck"
            try:
                for item in self.eraser.list_objects(row.prefix):
                    self.eraser.delete(item.key)
                if self.eraser.list_objects(row.prefix):
                    return "failed"
            except OSError:
                return "failed"
            self._forget(row)
            return "deleted"

    def _forget(self, row: SweepRow) -> None:
        if row.reason == "expired_submission":
            self.publications.remove(row.submission_id)
            return
        record = self.publications.get(row.submission_id)
        run_id = row.prefix.rstrip("/").rsplit("/", 1)[-1]
        if record is not None and run_id in record.superseded_run_ids:
            record.superseded_run_ids.remove(run_id)
            self.publications.save(record)


def _run_reason(group: RunGroup, record: ReportPublication, now: datetime) -> str:
    if group.run_id == record.run_id:
        return "current_run"
    if record.state == "pending" and group.run_id == record.attempt_run_id:
        return "record_pending"
    age = group.newest_age(now)
    if age is None or age < MIN_AGE_SECONDS:
        return "run_too_recent"
    if not group.committed:
        return "stale_partial_run"
    if record.run_id is not None:
        return "superseded_run"
    return "record_pending"


def _run_row(
    group: RunGroup, reason: str, status: str, state: str | None, now: datetime
) -> SweepRow:
    return SweepRow(
        submission_id=group.submission_id,
        prefix=group.prefix,
        reason=reason,
        object_count=len(group.objects),
        bytes=group.size_bytes,
        projection_status=status,
        record_state=state,
        newest_object_age_seconds=group.newest_age(now),
    )


def _min_age(groups: list[RunGroup], now: datetime) -> float | None:
    ages = [age for g in groups if (age := g.newest_age(now)) is not None]
    return min(ages) if ages else None


def local_status(
    *,
    settings,
    store: PublicStore,
    publications: PublicationStore,
    staging: StagingArea,
    now: datetime,
) -> dict:
    """Record counts, settings in effect and the staging summary. No R2 request."""
    records = publications.list()
    states = {name: 0 for name in ("pending", "verified", "blocked", "delete_pending")}
    blocked_rules: dict[str, int] = {}
    overdue = 0
    for record in records:
        states[record.state] += 1
        if record.state == "blocked":
            rule = record.blocked_rule or "unknown"
            blocked_rules[rule] = blocked_rules.get(rule, 0) + 1
        if record.state == "delete_pending" and _past_expiry(store, record, now):
            overdue += 1
    summary = staging.summary()
    return {
        "records": states,
        "blocked_rules": blocked_rules,
        "delete_pending_overdue": overdue,
        "records_with_superseded_runs": sum(1 for r in records if r.superseded_run_ids),
        "superseded_runs": sum(len(r.superseded_run_ids) for r in records),
        "settings": {
            "PUBLIC_REPORT_PUBLISH": "on" if settings.report_publish else "off",
            "PUBLIC_REPORT_EXPIRY_DELETE": "on"
            if settings.report_expiry_delete
            else "off",
            "PUBLIC_REPORT_SWEEP": "on" if settings.report_sweep else "off",
        },
        "staging": asdict(summary),
    }


def _past_expiry(store: PublicStore, record: ReportPublication, now: datetime) -> bool:
    submission = store.get(record.submission_id)
    if submission is None:
        return True
    return parse_utc(submission.expires_at) <= now


_STATUS_ROWS = (
    "current",
    "superseded",
    "partial",
    "unrecorded",
    "expired_holding",
    "other",
)


def remote_status(
    *,
    lister: ReportLister | ReportEraser,
    store: PublicStore,
    publications: PublicationStore,
    now: datetime,
    details: bool = False,
) -> dict:
    """Per-run storage under ``reports/`` by category. LIST only.

    Runs of expired submissions count only under ``expired_holding``. Runs of a
    submission with no local projection or no publication record count under
    ``unrecorded``: the sweep skips those (``projection_absent``,
    ``complete_without_record``), so they are never ``superseded`` or
    ``partial``. Keys that are not ``reports/<id>/<run_id>/...`` count under
    ``other``. The total row is the sum of the rows above it.
    """
    grouped = group_objects(lister.list_objects("reports/"))
    table = {name: {"runs": 0, "objects": 0, "bytes": 0} for name in _STATUS_ROWS}
    table["expired_holding"]["submissions"] = 0
    rows = []
    for submission_id in sorted(grouped.runs):
        runs = grouped.runs[submission_id]
        row = _submission_status(submission_id, runs, store, publications, now, table)
        rows.append(row)
    for items in grouped.invalid.values():
        _add(table["other"], items)
    table["total"] = {
        key: sum(table[name][key] for name in _STATUS_ROWS)
        for key in ("runs", "objects", "bytes")
    }
    payload: dict = {"bucket": lister.bucket, "table": table}
    if details:
        payload["submissions"] = rows
    return payload


def _add(bucket: dict, items: list[ObjectStat], runs: int = 1) -> None:
    bucket["runs"] += runs
    bucket["objects"] += len(items)
    bucket["bytes"] += sum(item.size_bytes for item in items)


def _submission_status(
    submission_id: str,
    runs: dict[str, RunGroup],
    store: PublicStore,
    publications: PublicationStore,
    now: datetime,
    table: dict,
) -> dict:
    submission = store.get(submission_id)
    record = publications.get(submission_id)
    status = None if submission is None else submission.status
    row = {
        "submission_id": submission_id,
        "projection_status": status,
        "record_state": None if record is None else record.state,
        "current_run_id": None if record is None else record.run_id,
        "current_runs": 0,
        "superseded_runs": 0,
        "superseded_bytes": 0,
        "partial_runs": 0,
        "partial_bytes": 0,
        "unrecorded_runs": 0,
        "unrecorded_bytes": 0,
        "total_bytes": sum(g.size_bytes for g in runs.values()),
        "oldest_non_current_age_seconds": None,
    }
    if status == "expired":
        table["expired_holding"]["submissions"] += 1
        for group in runs.values():
            _add(table["expired_holding"], group.objects)
        return row
    ages = []
    for group in runs.values():
        kind = "unrecorded" if submission is None else _status_kind(group, record)
        _add(table[kind], group.objects)
        row[f"{kind}_runs"] += 1
        if kind == "current":
            continue
        row[f"{kind}_bytes"] += group.size_bytes
        age = group.newest_age(now)
        if age is not None:
            ages.append(age)
    row["oldest_non_current_age_seconds"] = max(ages) if ages else None
    return row


def _status_kind(group: RunGroup, record: ReportPublication | None) -> str:
    if record is None:
        return "unrecorded"
    if not group.committed:
        return "partial"
    if group.run_id == record.run_id:
        return "current"
    return "superseded"
