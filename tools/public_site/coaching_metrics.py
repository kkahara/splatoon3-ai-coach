"""Closed-label coaching snapshot for one retained analysis directory.

Counts and labels only. Assessment text, prompts, paths, and scenario ids
stay on disk and never enter the stored document.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from public_site.frames import ranked_entries
from public_site.results import load_result
from splatoon3_ai_coach.coach.artifacts import (
    CoachingIndex,
    CoachingIndexEntry,
    coach_inputs_dir,
    coach_inputs_staleness,
    coach_prototype_dir,
    read_coaching_index_or_none,
)

COACHING_SCHEMA_VERSION = 1
COACHING_READER_VERSION = "coaching-metrics-1"
_MAP_CAP = 32
_HEX12 = re.compile(r"^[0-9a-f]{12}$")
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+@-]{0,63}$")
_PUBLIC = ("recommendation", "no_recommendation", "blocked", "not_reviewed")
_VALIDATION = (
    "validated",
    "contains_unverified_wording",
    "blocked_unsupported_claim",
    "not_validated",
)


class PublicCounts(BaseModel):
    """Public coaching states for one label."""

    model_config = ConfigDict(extra="forbid")

    recommendation: int = 0
    no_recommendation: int = 0
    blocked: int = 0
    not_reviewed: int = 0


class CoachingSnapshot(BaseModel):
    """One run's coaching counts. ``snapshot_at`` is added by the database."""

    model_config = ConfigDict(extra="forbid")

    snapshot_schema_version: int = COACHING_SCHEMA_VERSION
    snapshot_reader_version: str = COACHING_READER_VERSION
    evidence_version: int | None = None
    importance_config_hash: str | None = None
    stale: bool = False
    stale_reason: str | None = None
    candidate: int = 0
    positive_score: int = 0
    selected: int = 0
    requested_succeeded: int = 0
    requested_failed: int = 0
    not_requested: int = 0
    unselected_requested: int = 0
    validated: int = 0
    contains_unverified_wording: int = 0
    blocked_unsupported_claim: int = 0
    not_validated: int = 0
    recommendation: int = 0
    no_recommendation: int = 0
    blocked: int = 0
    not_reviewed: int = 0
    review_missing: int = 0
    by_candidate_type: dict[str, PublicCounts] = Field(default_factory=dict)
    by_factor: dict[str, PublicCounts] = Field(default_factory=dict)


def read_coaching_metrics(analysis_dir: Path) -> CoachingSnapshot | None:
    """Summarize retained coaching artifacts. ``None`` when the index is absent."""
    index = read_coaching_index_or_none(coach_inputs_dir(analysis_dir))
    if index is None:
        return None
    entries = ranked_entries(analysis_dir)
    calls = _call_rows(coach_prototype_dir(analysis_dir) / "llm_metrics.jsonl")
    selected_calls, residual = _call_groups(calls)
    snapshot = _selection(index, entries, selected_calls, residual)
    _apply_staleness(snapshot, analysis_dir)
    _apply_reviews(snapshot, analysis_dir, entries)
    snapshot.by_candidate_type = _bounded(snapshot.by_candidate_type)
    snapshot.by_factor = _bounded(snapshot.by_factor)
    return snapshot


def _selection(
    index: CoachingIndex,
    entries: list[CoachingIndexEntry],
    selected_calls: dict[str, list[bool]],
    residual: int,
) -> CoachingSnapshot:
    snapshot = CoachingSnapshot(
        evidence_version=index.evidence_version,
        importance_config_hash=_importance_hash(entries),
        unselected_requested=residual,
    )
    for entry in entries:
        snapshot.candidate += 1
        if entry.importance_score > 0:
            snapshot.positive_score += 1
        if not entry.selected_for_llm:
            continue
        snapshot.selected += 1
        _count_disposition(snapshot, selected_calls.get(entry.scenario_id, []))
    return snapshot


def _apply_staleness(snapshot: CoachingSnapshot, analysis_dir: Path) -> None:
    staleness = coach_inputs_staleness(coach_inputs_dir(analysis_dir), analysis_dir)
    if staleness is None or not staleness.stale:
        return
    snapshot.stale = True
    snapshot.stale_reason = None if staleness.reason is None else staleness.reason.value


def _importance_hash(entries: list[CoachingIndexEntry]) -> str | None:
    hashes = {
        entry.importance_config_hash
        for entry in entries
        if entry.importance_config_hash
        and _HEX12.fullmatch(entry.importance_config_hash)
    }
    if len(hashes) != 1:
        return None
    return next(iter(hashes))


def _count_disposition(snapshot: CoachingSnapshot, successes: list[bool]) -> None:
    if not successes:
        snapshot.not_requested += 1
    elif any(not success for success in successes):
        snapshot.requested_failed += 1
    else:
        snapshot.requested_succeeded += 1


def _apply_reviews(
    snapshot: CoachingSnapshot,
    analysis_dir: Path,
    entries: list[CoachingIndexEntry],
) -> None:
    reviews = _reviews_for(analysis_dir)
    if reviews is None or len(reviews) != len(entries):
        snapshot.review_missing = len(entries)
        return
    for entry, (state, status) in zip(entries, reviews, strict=True):
        public = state if state in _PUBLIC else "not_reviewed"
        validation = status if status in _VALIDATION else "not_validated"
        setattr(snapshot, public, getattr(snapshot, public) + 1)
        setattr(snapshot, validation, getattr(snapshot, validation) + 1)
        _bump(snapshot.by_candidate_type, entry.candidate_type, public)
        for factor in entry.active_factors:
            _bump(snapshot.by_factor, factor, public)


def _reviews_for(analysis_dir: Path) -> list[tuple[str, str | None]] | None:
    try:
        result = load_result(analysis_dir)
    except Exception:
        logger.exception("coaching review projection failed")
        return None
    return [
        (moment.coaching_state, moment.assessment_status) for moment in result.moments
    ]


def _bump(counts: dict[str, PublicCounts], key: str, state: str) -> None:
    row = counts.setdefault(key, PublicCounts())
    setattr(row, state, getattr(row, state) + 1)


def collapse_public_counts(counts: dict[str, PublicCounts]) -> dict[str, PublicCounts]:
    """Keep at most 32 labels, folding the rest into ``other``."""
    return _bounded(counts)


def _bounded(counts: dict[str, PublicCounts]) -> dict[str, PublicCounts]:
    safe: dict[str, PublicCounts] = {}
    other = PublicCounts()
    for key, value in counts.items():
        if key != "other" and _KEY.fullmatch(key):
            safe[key] = value
        else:
            other = _merge(other, value)
    if len(safe) > _MAP_CAP or (_total(other) and len(safe) >= _MAP_CAP):
        ranked = sorted(safe, key=lambda key: (-_total(safe[key]), key))
        for key in ranked[_MAP_CAP - 1 :]:
            other = _merge(other, safe[key])
        safe = {key: safe[key] for key in ranked[: _MAP_CAP - 1]}
    if _total(other):
        safe["other"] = other
    return dict(sorted(safe.items()))


def _merge(left: PublicCounts, right: PublicCounts) -> PublicCounts:
    return PublicCounts(
        **{name: getattr(left, name) + getattr(right, name) for name in _PUBLIC}
    )


def _total(counts: PublicCounts) -> int:
    return sum(getattr(counts, name) for name in _PUBLIC)


def _call_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[dict] = []
    for line in text.splitlines():
        row = _object(line)
        if row is not None:
            rows.append(row)
    return rows


def _object(line: str) -> dict | None:
    if not line.strip():
        return None
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    return row if isinstance(row, dict) else None


def _call_groups(rows: list[dict]) -> tuple[dict[str, list[bool]], int]:
    selected: dict[str, list[bool]] = {}
    seen: set[str] = set()
    residual = 0
    for row in rows:
        if row.get("skipped_llm"):
            continue
        scenario = row.get("scenario_id")
        if row.get("selected_for_llm"):
            if isinstance(scenario, str) and scenario:
                selected.setdefault(scenario, []).append(bool(row.get("success")))
            continue
        residual += _residual_unit(scenario, seen)
    return selected, residual


def _residual_unit(scenario: object, seen: set[str]) -> int:
    if not isinstance(scenario, str) or not scenario or scenario in seen:
        return 0 if isinstance(scenario, str) and scenario in seen else 1
    seen.add(scenario)
    return 1
