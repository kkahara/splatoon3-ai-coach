"""Write per-unit coaching inputs for one analysis (no LLM).

Library half of ``s3-coach coach-inputs``: score the match, then write each
unit's CoachInput, scored unit, LLM view and VMV text, the coaching index,
``coach_inputs_meta.json`` and ``claim_eval.md``. ``report_diversity.json``
is written only when the experimental advice-family cap is enabled.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from splatoon3_ai_coach.coach.artifacts import (
    COACH_INPUTS_META_FILENAME,
    CoachingIndexEntry,
    safe_filename,
    unit_file_names,
    write_coaching_index,
    write_json,
)
from splatoon3_ai_coach.coach.claim_catalog import CoachingUnitResult
from splatoon3_ai_coach.coach.coaching_candidates import active_factor_ids
from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view
from splatoon3_ai_coach.coach.load_analysis import (
    load_coach_analysis_bundle,
    select_primary_scenario_ids,
)
from splatoon3_ai_coach.coach.match_scoring import MatchScoring, score_match
from splatoon3_ai_coach.coach.report_diversity import REPORT_DIVERSITY_FILENAME
from splatoon3_ai_coach.coach.vmv import format_vmv_developer, format_vmv_player
from splatoon3_ai_coach.config.models import AppConfig

CLAIM_EVAL_FILENAME = "claim_eval.md"
_CHECKLIST = [
    "",
    "## Checklist",
    "",
    "1. Did top-N pick the right deaths for coaching attention?",
    "2. Are importance factors evidence-grounded (not gates)?",
    "3. Is interpretation a valid principle (not disguised judgment)?",
    "4. Did it invent any “should have” / “bad death” judgments?",
    "5. Does score mean attention worthiness (not how bad)?",
    "6. Does coaching feel useful (selective top-N)?",
    "",
]


@dataclass(frozen=True)
class CoachInputsBuild:
    """Paths and scoring produced by :func:`write_coach_inputs`."""

    out_dir: Path
    index_path: Path
    eval_path: Path
    scoring: MatchScoring
    report_diversity_path: Path | None


def write_coach_inputs(
    analysis_dir: Path,
    app_config: AppConfig,
    *,
    out_dir: Path,
    limit: int,
    max_llm_units: int | None = None,
    on_unit: Callable[[CoachingUnitResult, list[str]], None] | None = None,
) -> CoachInputsBuild | None:
    """Score one analysis and write its coaching inputs; ``None`` without deaths.

    ``on_unit`` is called per unit in rank order with its active factor IDs,
    for progress output.
    """
    bundle = load_coach_analysis_bundle(
        analysis_dir,
        min_usable_confidence=app_config.vision.timer.min_usable_confidence,
        playback_config=app_config.coach.playback_clock,
    )
    if not select_primary_scenario_ids(bundle.scenarios, limit=limit):
        return None
    scoring = score_match(bundle, app_config, limit=limit, max_llm_units=max_llm_units)
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    eval_lines = _eval_header(analysis_dir, scoring)
    entries: list[CoachingIndexEntry] = []
    for unit in scoring.units_by_rank():
        active = active_factor_ids(unit.to_candidate())
        entries.append(_write_unit(out_dir, unit, scoring, app_config, active))
        eval_lines.append(_eval_row(unit, active))
        if on_unit is not None:
            on_unit(unit, active)
    eval_lines.extend(_CHECKLIST)
    index_path = write_coaching_index(out_dir, entries, analysis_id=bundle.analysis_id)
    write_json(
        out_dir / COACH_INPUTS_META_FILENAME, _meta(analysis_dir, scoring, entries)
    )
    eval_path = out_dir / CLAIM_EVAL_FILENAME
    eval_path.write_text("\n".join(eval_lines) + "\n", encoding="utf-8")
    diversity_path = None
    if scoring.report_diversity is not None:
        diversity_path = out_dir / REPORT_DIVERSITY_FILENAME
        write_json(diversity_path, scoring.report_diversity.model_dump(mode="json"))
    return CoachInputsBuild(out_dir, index_path, eval_path, scoring, diversity_path)


def _write_unit(
    out_dir: Path,
    unit: CoachingUnitResult,
    scoring: MatchScoring,
    app_config: AppConfig,
    active: list[str],
) -> CoachingIndexEntry:
    coach_input = scoring.coach_inputs[unit.candidate_id]
    safe_id = safe_filename(unit.candidate_id)
    names = unit_file_names(safe_id)
    llm_view = build_coach_llm_view(
        coach_input, unit, app_config.coach.death_factor_thresholds
    )
    write_json(out_dir / names.coach_input, coach_input.model_dump(mode="json"))
    write_json(out_dir / names.coaching, unit.model_dump(mode="json"))
    write_json(out_dir / names.llm_view, llm_view.model_dump(mode="json"))
    (out_dir / names.vmv_player).write_text(format_vmv_player(unit), encoding="utf-8")
    (out_dir / names.vmv_dev).write_text(format_vmv_developer(unit), encoding="utf-8")
    return CoachingIndexEntry(
        scenario_id=unit.candidate_id,
        candidate_id=unit.candidate_id,
        candidate_type=unit.candidate_type,
        safe_id=safe_id,
        video_time=unit.video_time,
        importance_score=unit.importance_score,
        rank=unit.rank,
        selected_for_llm=unit.selected_for_llm,
        importance_config_hash=scoring.importance_config_hash,
        active_factors=active,
        coach_input_json=names.coach_input,
        coaching_json=names.coaching,
        llm_view_json=names.llm_view,
        vmv_player=names.vmv_player,
        vmv_dev=names.vmv_dev,
    )


def _eval_header(analysis_dir: Path, scoring: MatchScoring) -> list[str]:
    return [
        f"# Claim eval — {analysis_dir.name}",
        "",
        f"match_duration_seconds: {scoring.match_duration_seconds}",
        f"max_llm_units: {scoring.max_llm_units}",
        f"importance_config_hash: {scoring.importance_config_hash}",
        "",
        "| rank | candidate | type | score | selected | active factors |",
        "| ---: | --- | --- | ---: | --- | --- |",
    ]


def _eval_row(unit: CoachingUnitResult, active: list[str]) -> str:
    selected_mark = "Y" if unit.selected_for_llm else "N"
    factors_txt = ", ".join(active) if active else "—"
    return (
        f"| {unit.rank} | `{unit.candidate_id}` | {unit.candidate_type} | "
        f"{unit.importance_score:.1f} | {selected_mark} | {factors_txt} |"
    )


def _meta(
    analysis_dir: Path, scoring: MatchScoring, entries: list[CoachingIndexEntry]
) -> dict[str, object]:
    return {
        "analysis_dir": str(analysis_dir.resolve()),
        "match_duration_seconds": scoring.match_duration_seconds,
        "max_llm_units": scoring.max_llm_units,
        "unit_count": len(entries),
        "selected_for_llm_count": sum(1 for u in scoring.units if u.selected_for_llm),
        "importance_config_hash": scoring.importance_config_hash,
        "importance_config": scoring.importance_config,
    }
