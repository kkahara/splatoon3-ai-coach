"""Review-internal SPECIAL_USE facts (contract: ``SPECIAL_USE_FUSION.md``).

One deterministic, closed-vocabulary fact per detected special-use glyph, fused
with the same match's roster lamps, own death glyphs and own death episodes.
Review clock only: nothing here maps to gameplay video time or emits a
``GameEvent``, and ``analysis/`` / ``coach/`` do not consume these facts.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from splatoon3_ai_coach.review.clock import is_overtime_display
from splatoon3_ai_coach.review.models import (
    AxisCalibrationEvidence,
    PlayerDeathEpisodeEvidence,
    ReviewTimelineDataset,
    SpecialUseEvent,
    TimelineEventMarker,
)
from splatoon3_ai_coach.review.slot_state import (
    SCREEN_WIDTH,
    SEARCH_PX,
    LampSequence,
    lamp_states,
    nearest_transition_px,
    slot_state,
)

FACTS_FILENAME = "special_use_facts.json"
TIMELINE_FILENAME = "review_timeline.json"

# Types with label-confirmed tuning-set events; frozen from the special-type
# coverage table in analysis/review_validation/heldout/sync/REPORT.md.
LABEL_CONFIRMED_SPECIALS: frozenset[str] = frozenset(
    {
        "big_bubbler",
        "booyah_bomb",
        "crab_tank",
        "ink_storm",
        "ink_vac",
        "killer_wail_5.1",
        "tacticooler",
        "triple_inkstrike",
        "triple_splashdown",
        "trizooka",
        "ultra_stamp",
        "wave_breaker",
    }
)

SlotStateValue = Literal["alive", "dead", "unknown"]
SlotStateReason = Literal["near_roster_transition", "no_lamp_frames"]
IdentityStatus = Literal["label_confirmed_type", "unconfirmed_type", "unknown_type"]
IntegrationBlocker = Literal["slot_dead_at_use", "slot_state_unknown", "unknown_type"]


class SpecialUseFact(BaseModel):
    """Facts about one SPECIAL_USE glyph on the Review clock (no judgments)."""

    special_index: int = Field(ge=0)
    slot_id: str
    team_row: Literal["top", "bottom"]
    side: Literal["ally", "opponent", "unknown"]
    special_id: str
    identity_status: IdentityStatus
    score: float = Field(ge=0, le=1)
    runner_up_margin: float = Field(ge=0, le=1)
    template_id: str | None = None
    x_norm: float = Field(ge=0, le=1)
    glyph_x: float
    remaining_estimate: float
    phase: Literal["regulation", "overtime"]
    slot_state_at_use: SlotStateValue
    slot_state_reason: SlotStateReason | None = None
    own_transition_distance_s: float | None = Field(default=None, ge=0)
    seconds_to_next_own_death: float | None = Field(default=None, ge=0)
    next_own_death_marker_id: str | None = None
    seconds_since_own_recovery_observed: float | None = Field(default=None, ge=0)
    integration_valid: bool
    integration_blockers: list[IntegrationBlocker] = Field(default_factory=list)


class SpecialUseFacts(BaseModel):
    """Per-import artifact written next to ``review_timeline.json``."""

    schema_version: str = "1.0"
    clock: Literal["review_remaining"] = "review_remaining"
    source_recording_id: str | None = None
    source_dataset_sha256: str | None = None
    seconds_per_px: float | None = None
    search_px: float = SEARCH_PX
    facts: list[SpecialUseFact] = Field(default_factory=list)


def derive_special_use_facts(
    ds: ReviewTimelineDataset, source_sha256: str | None = None
) -> SpecialUseFacts:
    """Build one fact per ``ds.special_events`` entry, in dataset order.

    Raises ``OutsidePlayableInterval`` if any glyph lies before GO or in a
    negative-clock range not observed as overtime, and ``ValueError`` if the
    cursor frame width is not ``SCREEN_WIDTH``.
    """
    check_frame_width(ds)
    floor = overtime_floor(ds)
    phases = [
        playable_phase(e.elapsed_estimate, ds.regulation_seconds, floor)
        for e in ds.special_events
    ]
    lamps = lamp_states(ds)
    facts = [
        _fact(i, event, ds, lamps.get(event.slot_id, []), phases[i])
        for i, event in enumerate(ds.special_events)
    ]
    axis = ds.axis_calibration
    return SpecialUseFacts(
        source_recording_id=ds.source_recording_id,
        source_dataset_sha256=source_sha256,
        seconds_per_px=None if axis is None else px_to_seconds(axis, 1.0),
        facts=facts,
    )


def _fact(
    index: int,
    event: SpecialUseEvent,
    ds: ReviewTimelineDataset,
    lamps: LampSequence,
    phase: Literal["regulation", "overtime"],
) -> SpecialUseFact:
    g = event.x_norm * SCREEN_WIDTH
    at = slot_state(lamps, g)
    status = identity_status(event.special_id)
    death = next_own_death(event, ds.match_history_events)
    distance = own_transition_distance_s(lamps, g, ds.axis_calibration)
    blockers = integration_blockers(at.state, status)
    return SpecialUseFact(
        special_index=index,
        slot_id=event.slot_id,
        team_row=event.team_row,
        side=side_of(event.team_row, ds.team_identity.ally_row),
        special_id=event.special_id,
        identity_status=status,
        score=event.score,
        runner_up_margin=event.runner_up_margin,
        template_id=event.template_id,
        x_norm=event.x_norm,
        glyph_x=round(g, 1),
        remaining_estimate=event.elapsed_estimate,
        phase=phase,
        slot_state_at_use=at.state,
        slot_state_reason=at.reason,
        own_transition_distance_s=distance,
        seconds_to_next_own_death=None if death is None else death[1],
        next_own_death_marker_id=None if death is None else death[0],
        seconds_since_own_recovery_observed=since_own_recovery(event, ds.death_episodes),
        integration_valid=not blockers,
        integration_blockers=blockers,
    )


def identity_status(special_id: str) -> IdentityStatus:
    """Status of the detected type; never a claim about this glyph's identity."""
    if special_id == "unknown":
        return "unknown_type"
    if special_id in LABEL_CONFIRMED_SPECIALS:
        return "label_confirmed_type"
    return "unconfirmed_type"


def side_of(
    team_row: str, ally_row: Literal["top", "bottom"] | None
) -> Literal["ally", "opponent", "unknown"]:
    """Ally / opponent from ``TeamIdentity.ally_row``; unknown without it."""
    if ally_row is None:
        return "unknown"
    return "ally" if team_row == ally_row else "opponent"


class OutsidePlayableInterval(ValueError):
    """A SPECIAL_USE glyph lies outside GO .. last observed match clock."""


def overtime_floor(ds: ReviewTimelineDataset) -> int | None:
    """Most negative observed overtime (``+M:SS``) label, or ``None``."""
    values = [
        s.elapsed_seconds
        for s in ds.samples
        if s.elapsed_seconds is not None
        and s.clock_status != "implausible"
        and is_overtime_display(s.displayed_clock)
    ]
    return min(values, default=None)


def playable_phase(
    remaining: float, regulation_seconds: int, floor: int | None
) -> Literal["regulation", "overtime"]:
    """Phase of a use on the remaining clock; fails closed outside play.

    Pre-GO countdown labels are also negative but are not on the match axis,
    so a negative value is overtime only when overtime labels were observed
    and the value lies within one display second of the last of them.
    """
    if remaining > regulation_seconds:
        raise OutsidePlayableInterval(
            f"remaining {remaining:.2f} is before GO ({regulation_seconds})"
        )
    if remaining >= 0:
        return "regulation"
    if floor is None:
        raise OutsidePlayableInterval(
            f"remaining {remaining:.2f} is negative but no overtime label was observed"
        )
    if remaining < floor - 1:
        raise OutsidePlayableInterval(
            f"remaining {remaining:.2f} is past the last overtime label ({floor})"
        )
    return "overtime"


def check_frame_width(ds: ReviewTimelineDataset, tolerance_px: float = 0.5) -> None:
    """The X = 0 rule compares cursor px with ``x_norm * SCREEN_WIDTH``."""
    for s in ds.samples:
        x, x_norm = s.cursor.x, s.cursor.x_norm
        if x is not None and x_norm is not None and x_norm > 0:
            if abs(x - x_norm * SCREEN_WIDTH) > tolerance_px:
                raise ValueError(
                    f"{s.sample_id}: cursor x {x} is not x_norm * {SCREEN_WIDTH}"
                )


def px_to_seconds(axis: AxisCalibrationEvidence, px: float) -> float:
    """Clock seconds spanned by ``px`` on the axis (``elapsed_at`` takes x_norm)."""
    return abs(axis.elapsed_at(px / SCREEN_WIDTH) - axis.elapsed_at(0.0))


def own_transition_distance_s(
    lamps: LampSequence, g: float, axis: AxisCalibrationEvidence | None
) -> float | None:
    """Seconds from the glyph to the slot's nearest lamp transition bracket."""
    px = nearest_transition_px(lamps, g)
    if px is None or axis is None:
        return None
    return round(px_to_seconds(axis, px), 3)


def integration_blockers(
    state: SlotStateValue, status: IdentityStatus
) -> list[IntegrationBlocker]:
    """Closed reasons this fact may not flow to a later integration step."""
    blockers: list[IntegrationBlocker] = []
    if state == "dead":
        blockers.append("slot_dead_at_use")
    elif state == "unknown":
        blockers.append("slot_state_unknown")
    if status == "unknown_type":
        blockers.append("unknown_type")
    return blockers


def next_own_death(
    event: SpecialUseEvent, markers: list[TimelineEventMarker]
) -> tuple[str, float] | None:
    """Nearest own death glyph at or after the use: (marker id, seconds)."""
    # Remaining clock: later in the match means a smaller value.
    gaps = [
        (event.elapsed_estimate - m.elapsed_estimate, m.marker_id)
        for m in markers
        if m.kind == "death"
        and m.player_slot_id == event.slot_id
        and m.elapsed_estimate is not None
        and m.elapsed_estimate <= event.elapsed_estimate
    ]
    if not gaps:
        return None
    seconds, marker_id = min(gaps)
    return marker_id, round(seconds, 3)


def since_own_recovery(
    event: SpecialUseEvent, episodes: list[PlayerDeathEpisodeEvidence]
) -> float | None:
    """Seconds since the latest own first-alive observation at or before the use."""
    # Remaining clock: earlier in the match means a larger value.
    gaps = [
        e.next_alive_elapsed - event.elapsed_estimate
        for e in episodes
        if e.slot_id == event.slot_id
        and e.next_alive_elapsed is not None
        and e.next_alive_elapsed >= event.elapsed_estimate
    ]
    return round(min(gaps), 3) if gaps else None


def write_special_use_facts(import_dir: Path) -> tuple[Path, SpecialUseFacts]:
    """Read ``review_timeline.json`` and write ``special_use_facts.json`` beside it."""
    raw = (import_dir / TIMELINE_FILENAME).read_bytes()
    ds = ReviewTimelineDataset.model_validate_json(raw)
    facts = derive_special_use_facts(ds, hashlib.sha256(raw).hexdigest())
    out = import_dir / FACTS_FILENAME
    out.write_text(facts.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return out, facts
