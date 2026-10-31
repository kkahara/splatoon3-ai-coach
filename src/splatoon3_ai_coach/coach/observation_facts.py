"""Timestamped factual roster and sparse map-ink selections for coaching."""

from __future__ import annotations

from typing import Any

from splatoon3_ai_coach.coach.claim_catalog import RosterSample
from splatoon3_ai_coach.coach.coach_input import CoachInput

ROSTER_FRAME = (
    "At video {video_time:g}s ({timing_description}), the observed roster was "
    "{ally_alive_count} allies alive and {opponent_alive_count} opponents alive."
)
MAP_INK_FRAME = (
    "At video {video_time:g}s ({timing_description}), visible ink coverage within "
    "the registered polygon was {visible_ink_coverage_percent:.2f}%. "
    "Ally share of detected ink: {ally_share_of_detected_ink_display}; "
    "opponent share of detected ink: {opponent_share_of_detected_ink_display}."
)
OBSERVATION_FRAMES = {
    "observed_roster_counts": ROSTER_FRAME,
    "observed_map_ink": MAP_INK_FRAME,
}


def observation_timing(coach_input: CoachInput, video_time: float) -> dict[str, Any]:
    """Describe a signed video-time offset without asserting continuous state."""
    death = coach_input.primary_context.death_episode
    is_death = death is not None and death.death_time is not None
    anchor = float(
        death.death_time if is_death else coach_input.primary_scenario.start_time
    )
    offset = float(video_time) - anchor
    label = "death" if is_death else "scenario anchor"
    relation = "before" if offset < 0 else "after" if offset > 0 else "at"
    timing = (
        f"{abs(offset):g}s {relation} {label} on the recording timeline"
        if offset
        else f"at {label}"
    )
    if is_death and offset > 0:
        timing += "; post-death observation"
    return {
        "video_time": float(video_time),
        "anchor_video_time": anchor,
        "offset_from_anchor_seconds": offset,
        "temporal_relationship": f"{relation}_{'death' if is_death else 'anchor'}",
        "timing_description": timing,
    }


def observed_roster_counts(
    coach_input: CoachInput, sample: RosterSample | None
) -> dict[str, Any] | None:
    """Use the reliable pre-death sample already selected for this unit."""
    players = coach_input.primary_context.players
    if not coach_input.roster_evidence or sample is None or players is None:
        return None
    death = coach_input.primary_context.death_episode
    if players.unreliable_at(sample.video_time) or (
        death is not None and players.unreliable_at(death.death_time)
    ):
        return None
    if not any(
        point.reliable
        and point.video_time == sample.video_time
        and point.ally_alive_count == sample.ally_alive_count
        and point.opponent_alive_count == sample.opponent_alive_count
        for point in players.trajectory
    ):
        return None
    return {
        **observation_timing(coach_input, sample.video_time),
        "ally_alive_count": sample.ally_alive_count,
        "opponent_alive_count": sample.opponent_alive_count,
        "source_path": sample.source_path,
    }


def observed_map_ink(coach_input: CoachInput) -> dict[str, Any] | None:
    """Nearest admitted ink observation; equal distances prefer pre-anchor."""
    map_ctx = coach_input.primary_context.map
    if map_ctx is None or map_ctx.ink is None or not map_ctx.ink.observations:
        return None
    anchor = observation_timing(coach_input, 0.0)["anchor_video_time"]
    index, sample = min(
        enumerate(map_ctx.ink.observations),
        key=lambda item: (abs(item[1].video_time - anchor), item[1].video_time > anchor),
    )
    if sample.visible_ink_fraction is None:
        return None
    ally = _percent(sample.ally_share_of_detected_ink)
    opponent = _percent(sample.opponent_share_of_detected_ink)
    return {
        **observation_timing(coach_input, sample.video_time),
        "visible_ink_coverage_percent": sample.visible_ink_fraction * 100.0,
        "ally_share_of_detected_ink_percent": ally,
        "opponent_share_of_detected_ink_percent": opponent,
        "ally_share_of_detected_ink_display": _display(ally),
        "opponent_share_of_detected_ink_display": _display(opponent),
        "source_path": f"primary_context.map.ink.observations[{index}]",
    }


def render_observation_fact(claim_id: str, values: dict[str, Any]) -> str:
    """Render a known factual template for deterministic evidence presentation."""
    return OBSERVATION_FRAMES[claim_id].format(**values)


def _percent(fraction: float | None) -> float | None:
    return None if fraction is None else fraction * 100.0


def _display(percent: float | None) -> str:
    return "unavailable" if percent is None else f"{percent:.2f}%"
