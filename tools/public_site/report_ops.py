"""Operator actions behind ``s3-coach public-site-reports``.

- ``status``: local records and staging (no R2); ``--remote`` adds LIST-only
  per-run storage.
- ``scan``: read-only content-policy check of local analyses; counts by rule
  and a key-path inventory diff. No R2, no writes (except an explicitly
  requested inventory file).
- ``sweep``: dry-run plan by default; apply needs the plan digest.
- ``retry_blocked``: moves one ``blocked`` record back to ``pending`` (local).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from public_site.report_bundle import (
    BundleError,
    key_paths,
    known_values,
    prepare_file,
    run_key,
    select_files,
)
from public_site.report_content import KnownValues
from public_site.report_publications import PublicationStore
from public_site.report_publisher import open_report_storage
from public_site.report_staging import StagingArea
from public_site.report_sweep import ReportSweeper, local_status, remote_status
from public_site.report_transform import (
    MANIFEST_PATH,
    TransformError,
    new_pseudonym,
    read_identity,
    verify_published,
)
from public_site.settings import PublicSettings
from public_site.storage import ReportEraser, ReportLister
from public_site.store import PublicStore, utc_now

SCAN_SUBMISSION = "0" * 32
SCAN_RUN = "19700101T000000Z-00000000"
_HEX_ID = re.compile(r"^[0-9a-f]{32}$")
_DATA_KEY = re.compile(r"^(?:[0-9a-f]{16,}|[0-9]+(?:\.[0-9]+)?|.*[:@/ ].*)$")
_CATEGORIES = (
    ("coach_inputs/coaching_index.json", "inputs_index"),
    ("coach_prototype/coaching_index.json", "prototype_index"),
    ("coach_inputs/*.coaching.json", "coaching"),
    ("coach_inputs/*.coach_input.json", "coach_input"),
    ("coach_inputs/*.llm_view.json", "llm_view"),
    ("coach_prototype/*.llm_view.json", "prototype_llm_view"),
    ("coach_prototype/*.output.json", "output"),
    ("coach_prototype/*.flags.json", "flags"),
    ("coach_prototype/*.revalidation.json", "revalidation"),
    (MANIFEST_PATH, "vision_manifest"),
    ("match_identity.json", "match_identity"),
)


def require_r2(settings: PublicSettings) -> None:
    """Remote commands need credentials (never the three report settings).

    Raises:
        ValueError: R2 credentials are missing.
    """
    if not settings.r2_ready:
        raise ValueError("R2 credentials are required for this command")


def status(settings: PublicSettings, *, remote: bool, details: bool) -> dict:
    """Local section always; remote section (LIST only) when asked."""
    store = PublicStore(settings.root)
    publications = PublicationStore(settings.root)
    now = datetime.now(UTC)
    payload = {
        "local": local_status(
            settings=settings,
            store=store,
            publications=publications,
            staging=StagingArea(settings.root),
            now=now,
        )
    }
    if remote:
        require_r2(settings)
        lister = ReportLister(open_report_storage(settings))
        payload["remote"] = remote_status(
            lister=lister,
            store=store,
            publications=publications,
            now=now,
            details=details,
        )
    return payload


def sweeper(settings: PublicSettings, **thresholds) -> ReportSweeper:
    """An operator sweeper (credentials required; PUBLIC_REPORT_SWEEP is not)."""
    require_r2(settings)
    return ReportSweeper(
        eraser=ReportEraser(open_report_storage(settings)),
        store=PublicStore(settings.root),
        publications=PublicationStore(settings.root),
        **thresholds,
    )


def retry_blocked(settings: PublicSettings, submission_id: str) -> bool:
    """Move a ``blocked`` record back to ``pending`` for a fresh run. Local only."""
    publications = PublicationStore(settings.root)

    def change(record):
        if record is None or record.state != "blocked":
            return None
        record.state = "pending"
        record.blocked_rule = None
        record.last_error = None
        record.attempt_run_id = None
        record.attempts = 0
        record.next_attempt_at = utc_now()
        return record

    return publications.update(submission_id, change) is not None


@dataclass
class ScanReport:
    """Counts only: no values, paths or IDs."""

    analyses: int = 0
    analyses_clean: int = 0
    files: int = 0
    findings: dict[str, int] = field(default_factory=dict)
    clips_with_metadata: int = 0
    inventory: dict[str, set[str]] = field(default_factory=dict)

    def add(self, rule: str) -> None:
        """Count one finding."""
        self.findings[rule] = self.findings.get(rule, 0) + 1


def scan(settings: PublicSettings, analysis_root: Path | None = None) -> ScanReport:
    """Run the content policy and transforms over every local analysis, file by file."""
    root = analysis_root or settings.root / "analysis"
    store = PublicStore(settings.root)
    report = ScanReport()
    if not root.is_dir():
        return report
    for analysis in sorted(path for path in root.iterdir() if path.is_dir()):
        if not (analysis / MANIFEST_PATH).is_file():
            continue
        report.analyses += 1
        before = sum(report.findings.values())
        _scan_one(analysis, _scan_known(settings, store, analysis.name), report)
        if sum(report.findings.values()) == before:
            report.analyses_clean += 1
    return report


def _scan_known(settings: PublicSettings, store: PublicStore, name: str) -> KnownValues:
    if not _HEX_ID.fullmatch(name):
        return known_values(settings, None, None)
    return known_values(settings, store.get(name), store.get_video(name))


def _scan_one(analysis: Path, known: KnownValues, report: ScanReport) -> None:
    coached = (analysis / "coach_inputs" / "coaching_index.json").is_file()
    try:
        paths = select_files(analysis, "coached" if coached else None)
        identity = read_identity((analysis / MANIFEST_PATH).read_bytes())
    except BundleError as exc:
        report.add(str(exc))
        return
    except TransformError as exc:
        report.add(f"transform_failed:{exc}")
        return
    run_known = KnownValues(list(known.tokens))
    run_known.add_secret("original_fingerprint", identity.video_identity)
    run_known.add_secret("original_fingerprint", identity.analysis_id)
    pseudonym = new_pseudonym(identity)
    published: dict[str, bytes] = {}
    for path in paths:
        report.files += 1
        try:
            run_key(SCAN_SUBMISSION, SCAN_RUN, path)
            _item, data = prepare_file(
                analysis, path, identity, pseudonym, run_known, None
            )
        except BundleError as exc:
            report.add(str(exc))
            if path.endswith(".mp4") and exc.rule and exc.rule.startswith("mp4_"):
                report.clips_with_metadata += 1
            continue
        if data is not None:
            published[path] = data
            _inventory(report, path, data)
    if MANIFEST_PATH in published:
        try:
            verify_published(published, identity, pseudonym)
        except TransformError as exc:
            report.add(f"transform_failed:{exc}")


def _inventory(report: ScanReport, path: str, data: bytes) -> None:
    category = category_of(path)
    if category is None:
        return
    found = report.inventory.setdefault(category, set())
    found |= {normalize_key_path(item) for item in key_paths(json.loads(data))}


def category_of(path: str) -> str | None:
    """Inventory category of a published JSON path."""
    from fnmatch import fnmatch

    for pattern, name in _CATEGORIES:
        if fnmatch(path, pattern):
            return name
    return None


def normalize_key_path(path: str) -> str:
    """Replace data-like key segments (hashes, numbers, IDs) with ``<key>``."""
    parts = path.split(".")
    out = []
    for part in parts:
        name, brackets = part, ""
        while name.endswith("[]"):
            name, brackets = name[:-2], brackets + "[]"
        out.append(("<key>" if name and _DATA_KEY.fullmatch(name) else name) + brackets)
    return ".".join(out)


def inventory_payload(report: ScanReport) -> dict[str, list[str]]:
    """Sorted key paths per category (key names only)."""
    return {name: sorted(paths) for name, paths in sorted(report.inventory.items())}


def inventory_diff(report: ScanReport, inventory: dict[str, list[str]]) -> dict:
    """Key paths present in the scanned artifacts but not in the inventory, and
    inventory paths not seen (only for categories that were scanned).
    """
    new: dict[str, list[str]] = {}
    unseen: dict[str, list[str]] = {}
    for name, paths in report.inventory.items():
        known = set(inventory.get(name, []))
        if paths - known:
            new[name] = sorted(paths - known)
        if known - paths:
            unseen[name] = sorted(known - paths)
    return {"new": new, "unseen": unseen}
