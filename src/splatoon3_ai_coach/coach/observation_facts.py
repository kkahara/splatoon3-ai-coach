"""Timestamped factual roster and sparse map-ink selections for coaching.

Each selection is one internal dict with full provenance (video times,
``source_path``, raw percentages). Three projections read it:

* :func:`view_block` — the CoachLlmView evidence block: raw numeric leaves stay
  (they are the citation targets); English narration and display strings go.
* :func:`claim_values` — the model-facing summary a claim frame words: timing
  relative to the death and whole-number percentages, no recording-clock times.
* :func:`render_observation_debug` — developer text (VMV supporting evidence).
"""

from __future__ import annotations

from typing import Any

from splatoon3_ai_coach.coach.claim_catalog import RosterSample
from splatoon3_ai_coach.coach.coach_input import CoachInput

ROSTER_FRAME = (
    "The roster observed {timing} the death showed {ally_alive_count} teammates "
    "and {opponent_alive_count} opponents alive."
)
MAP_INK_FRAME = (
    "In the map observation {timing} the death, allies accounted for about "
    "{ally_share_of_detected_ink_rounded_percent}% and opponents about "
    "{opponent_share_of_detected_ink_rounded_percent}% of the detected ink."
)

ROSTER_DEBUG_FRAME = (
    "At video {video_time:g}s ({timing_description}), the observed roster was "
    "{ally_alive_count} allies alive and {opponent_alive_count} opponents alive."
)
MAP_INK_DEBUG_FRAME = (
    "At video {video_time:g}s ({timing_description}), visible ink coverage within "
    "the registered polygon was {visible_ink_coverage_percent:.2f}%. "
    "Ally share of detected ink: {ally_share_of_detected_ink_display}; "
    "opponent share of detected ink: {opponent_share_of_detected_ink_display}."
)
OBSERVATION_DEBUG_FRAMES = {
    "observed_roster_counts": ROSTER_DEBUG_FRAME,
    "observed_map_ink": MAP_INK_DEBUG_FRAME,
}

_NARRATION_FIELDS = frozenset(
    {
        "timing_description",
        "ally_share_of_detected_ink_display",
        "opponent_share_of_detected_ink_display",
    }
)
_ROUNDED_PERCENTS = {
    "visible_ink_coverage_percent": "visible_ink_coverage_rounded_percent",
    "ally_share_of_detected_ink_percent": "ally_share_of_detected_ink_rounded_percent",
    "opponent_share_of_detected_ink_percent": (
        "opponent_share_of_detected_ink_rounded_percent"
    ),
}
_SUMMARY_FIELDS = (
    "timing",
    "temporal_relationship",
    "offset_from_anchor_seconds",
    "ally_alive_count",
    "opponent_alive_count",
)


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


def view_block(values: dict[str, Any] | None) -> dict[str, Any] | None:
    """CoachLlmView evidence block: raw leaves, provenance and wording summary.

    Raw numbers are kept unchanged; ``timing`` and the rounded percentages are
    added beside them for wording. English narration and display strings go.
    """
    if values is None:
        return None
    block = {k: v for k, v in values.items() if k not in _NARRATION_FIELDS}
    block["timing"] = str(values["temporal_relationship"]).split("_", 1)[0]
    for raw, rounded in _ROUNDED_PERCENTS.items():
        if raw in values:
            block[rounded] = rounded_percent(values[raw])
    return block


def claim_values(values: dict[str, Any] | None) -> dict[str, Any] | None:
    """Model-facing summary: timing relative to the death, rounded percentages.

    Every value equals a :func:`view_block` leaf; recording-clock times, raw
    percentages and provenance stay out of the claim.
    """
    block = view_block(values)
    if block is None:
        return None
    keys = (*_SUMMARY_FIELDS, *_ROUNDED_PERCENTS.values())
    return {key: block[key] for key in keys if key in block}


def rounded_percent(percent: float | None) -> int | None:
    """Whole-number percentage for wording; ``None`` stays unavailable."""
    return None if percent is None else int(round(float(percent)))


def render_observation_debug(claim_id: str, values: dict[str, Any]) -> str:
    """Developer text with recording-clock times; never player-facing."""
    return OBSERVATION_DEBUG_FRAMES[claim_id].format(**values)


def _percent(fraction: float | None) -> float | None:
    return None if fraction is None else fraction * 100.0


def _display(percent: float | None) -> str:
    return "unavailable" if percent is None else f"{percent:.2f}%"
