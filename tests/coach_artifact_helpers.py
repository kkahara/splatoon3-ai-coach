"""Complete coach-inputs index rows for tests that fabricate artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from splatoon3_ai_coach.analysis.map_coverage import (
    CoverageStatus,
    EpisodeMapObservability,
    MapCheckStatus,
    MapOverlayCoverage,
    MapUnknownReason,
)
from splatoon3_ai_coach.analysis.scenario_context import MapContext
from splatoon3_ai_coach.coach.artifacts import (
    PrototypeIndex,
    PrototypeIndexEntry,
    unit_file_names,
    write_assessment_artifacts,
    write_prototype_index,
)
from splatoon3_ai_coach.coach.coach_input import UnitDurations
from splatoon3_ai_coach.coach.llm_client import CoachingAssessment
from splatoon3_ai_coach.coach.playback_clock import ClockedDuration
from splatoon3_ai_coach.media.video_source import Observability


def write_prototype_unit(
    prototype_dir: Path,
    safe_id: str,
    assessments: dict[str, str],
    *,
    primary_model: str,
) -> PrototypeIndex:
    """Write one unit's model outputs plus a current-schema prototype index."""
    prototype_dir.mkdir(parents=True, exist_ok=True)
    outputs = [
        write_assessment_artifacts(
            out_dir=prototype_dir,
            safe_id=safe_id,
            model_label=model,
            assessment=CoachingAssessment(assessment=text),
            raw_text=text,
            flags={"hit_count": 0},
            skipped_llm=False,
        )
        for model, text in assessments.items()
    ]
    primary = next((o.output_json for o in outputs if o.model == primary_model), None)
    entry = PrototypeIndexEntry(
        **{
            k: v
            for k, v in index_entry(safe_id).items()
            if k in PrototypeIndexEntry.model_fields
        },
        outputs=outputs,
        primary_output_json=primary,
    )
    index = PrototypeIndex(primary_model=primary_model, entries=[entry])
    write_prototype_index(prototype_dir, index)
    return index


def video_durations(**seconds: float | None) -> UnitDurations:
    """Durations as at verified normal playback (video seconds are play time)."""
    values = {
        name: ClockedDuration(
            video_seconds=value, value=value, clock="video", method="video_elapsed"
        )
        for name, value in seconds.items()
        if value is not None
    }
    return UnitDurations(**values)


def index_entry(safe_id: str, **overrides: Any) -> dict[str, Any]:
    """A valid ``CoachingIndexEntry`` payload; override any field by keyword."""
    names = unit_file_names(safe_id)
    row: dict[str, Any] = {
        "scenario_id": safe_id,
        "candidate_id": safe_id,
        "candidate_type": "death_episode",
        "safe_id": safe_id,
        "video_time": 0.0,
        "importance_score": 0.0,
        "rank": None,
        "selected_for_llm": False,
        "active_factors": [],
        "coach_input_json": names.coach_input,
        "coaching_json": names.coaching,
    }
    row.update(overrides)
    return row


def map_ctx(before: bool | None, **fields: Any) -> MapContext:
    """MapContext whose status matches ``before`` (True / False / None).

    ``False`` models a fully covered episode (rule ``respawn_map_coverage``
    satisfied), so absence is a reliable ``not_observed``.
    """
    match = MapOverlayCoverage(
        status=CoverageStatus.SUFFICIENT if before is not None else (
            CoverageStatus.INSUFFICIENT
        ),
        source_observability=Observability.OBSERVABLE,
        completed_respawns=3,
        respawns_with_overlay=3 if before is not None else 0,
        reason="test fixture",
    )
    episode = EpisodeMapObservability(
        status=match.status,
        pre_death_frame_fraction=1.0,
        max_pre_death_gap_seconds=0.5,
        own_respawn_overlay=before is not None,
    )
    status = {
        True: MapCheckStatus.OBSERVED,
        False: MapCheckStatus.NOT_OBSERVED,
        None: MapCheckStatus.UNKNOWN,
    }[before]
    return MapContext(
        map_check_before_death=before,
        map_check_status=status,
        map_check_unknown_reason=(
            MapUnknownReason.MATCH_COVERAGE_INSUFFICIENT if before is None else None
        ),
        map_coverage=match,
        episode_map_observability=episode,
        **fields,
    )
