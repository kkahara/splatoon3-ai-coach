"""Merge clocked timeline samples into one state per game-clock second."""

from __future__ import annotations

from collections.abc import Callable

from .clock import is_countdown_display
from .models import (
    ControlSegment,
    CoverageReport,
    CursorSlotObservation,
    EvidenceField,
    FrameProvenance,
    PenaltyField,
    ReviewSecondState,
    ReviewTimelineSample,
    SlotState,
    SpecialUseEvent,
    TimelineEventMarker,
    project_elapsed_second,
)

PASS_DISAGREEMENT = "PASS_DISAGREEMENT"
_NO_SAMPLE = "no sample"
_SLOT_IDS = tuple(f"{row}_{index}" for row in ("top", "bottom") for index in range(1, 5))


def build_second_states(
    samples: list[ReviewTimelineSample],
    markers: list[TimelineEventMarker],
    specials: list[SpecialUseEvent],
    segments: list[ControlSegment],
) -> list[ReviewSecondState]:
    """Build one state for every integer second from the first clock to the last.

    Pre-GO countdown labels are not match seconds and share negative values
    with overtime, so they never enter a state.
    """
    clocked = [
        sample
        for sample in samples
        if sample.elapsed_seconds is not None
        and not is_countdown_display(sample.displayed_clock)
    ]
    if not clocked:
        return []
    first = min(sample.elapsed_seconds or 0 for sample in clocked)
    last = max(sample.elapsed_seconds or 0 for sample in clocked)
    by_second: dict[int, list[ReviewTimelineSample]] = {}
    for sample in clocked:
        by_second.setdefault(int(sample.elapsed_seconds or 0), []).append(sample)
    return [
        _state_for_second(second, by_second.get(second, []), markers, specials, segments)
        for second in range(first, last + 1)
    ]


def annotate_coverage(
    coverage: CoverageReport, states: list[ReviewSecondState]
) -> CoverageReport:
    """Record which game-clock seconds were observed. Unknown fields can remain."""
    expected = [state.elapsed_second for state in states]
    missing = [state.elapsed_second for state in states if _NO_SAMPLE in _reasons(state)]
    observed = [second for second in expected if second not in set(missing)]
    disagreements = [
        state.elapsed_second
        for state in states
        if any(warning.startswith(PASS_DISAGREEMENT) for warning in state.warnings)
    ]
    return coverage.model_copy(
        update={
            "expected_seconds": expected,
            "observed_seconds": observed,
            "missing_elapsed_seconds": missing,
            "complete": bool(expected) and not missing,
            "disagreement_seconds": disagreements,
        }
    )


def _state_for_second(
    second: int,
    samples: list[ReviewTimelineSample],
    markers: list[TimelineEventMarker],
    specials: list[SpecialUseEvent],
    segments: list[ControlSegment],
) -> ReviewSecondState:
    """Combine the samples, static events, and control band for one second."""
    if not samples:
        return _empty_state(second)
    slots, slot_warnings = _merge_slots(second, samples)
    warnings = list(slot_warnings)
    fields, field_warnings = _merge_score_fields(samples)
    warnings.extend(field_warnings)
    top_alive, bottom_alive = _alive_counts(slots)
    return ReviewSecondState(
        elapsed_second=second,
        slots=slots,
        top_alive_count=top_alive,
        bottom_alive_count=bottom_alive,
        controller=_controller(second, segments),
        special_ids=_special_ids(second, specials),
        death_marker_ids=_death_ids(second, markers),
        provenance=[_provenance(sample) for sample in samples],
        warnings=warnings,
        **fields,
    )


def _empty_state(second: int) -> ReviewSecondState:
    """A second with no clocked sample stays unknown in every field."""
    unknown = EvidenceField(reason=_NO_SAMPLE)
    penalty = PenaltyField(reason=_NO_SAMPLE)
    return ReviewSecondState(
        elapsed_second=second,
        slots=[_unknown_slot(second, slot_id) for slot_id in _SLOT_IDS],
        top_alive_count=unknown,
        bottom_alive_count=unknown.model_copy(),
        top_remaining=unknown.model_copy(),
        bottom_remaining=unknown.model_copy(),
        top_penalty=penalty,
        bottom_penalty=penalty.model_copy(),
        controller=unknown.model_copy(),
        warnings=[_NO_SAMPLE],
    )


def _merge_slots(
    second: int, samples: list[ReviewTimelineSample]
) -> tuple[list[CursorSlotObservation], list[str]]:
    """Resolve each lamp from its own reads. Directions merge without guessing."""
    slots: list[CursorSlotObservation] = []
    warnings: list[str] = []
    for slot_id in _SLOT_IDS:
        chosen, warning = _merge_known(
            _best_by_direction(
                samples,
                lambda sample, slot_id=slot_id: _slot_read(sample, slot_id),
            )
        )
        if warning:
            warnings.append(f"{PASS_DISAGREEMENT}:{slot_id}")
        state = SlotState.unknown if chosen is None else SlotState(str(chosen[0]))
        slots.append(
            CursorSlotObservation(
                slot_id=slot_id,
                team_row=slot_id.split("_", maxsplit=1)[0],  # type: ignore[arg-type]
                state=state,
                confidence=0 if chosen is None else chosen[1],
                sample_id="" if chosen is None else chosen[2],
                elapsed_seconds=second,
            )
        )
    return slots, warnings


def _merge_score_fields(
    samples: list[ReviewTimelineSample],
) -> tuple[dict[str, EvidenceField | PenaltyField], list[str]]:
    """Let each score and penalty field choose its own source sample."""
    names = (
        "top_remaining",
        "bottom_remaining",
        "top_penalty",
        "bottom_penalty",
    )
    fields: dict[str, EvidenceField | PenaltyField] = {}
    warnings: list[str] = []
    for name in names:
        chosen, warning = _merge_known(
            _best_by_direction(
                samples, lambda sample, name=name: _score_read(sample, name)
            )
        )
        if warning:
            warnings.append(f"{PASS_DISAGREEMENT}:{name}")
        field = _field(name, chosen, warning)
        if chosen is None and not warning:
            field = _unknown_field(name, samples)
        fields[name] = field
    return fields, warnings


def _unknown_field(
    name: str, samples: list[ReviewTimelineSample]
) -> EvidenceField | PenaltyField:
    """Keep why a field is unknown, including a visible but unreadable pill."""
    reasons = [getattr(sample.score, f"{name}_reason") for sample in samples]
    reason = next((item for item in reasons if item), "unreadable")
    if not name.endswith("penalty"):
        return EvidenceField(reason=reason)
    visible = [
        sample.sample_id
        for sample in samples
        if getattr(sample.score, f"{name}_pill_visible") is True
    ]
    if visible:
        return PenaltyField(
            pill_visible=True,
            reason="unreadable",
            source_sample_ids=sorted(visible),
        )
    return PenaltyField(reason=reason)


def _best_by_direction(
    samples: list[ReviewTimelineSample],
    read: Callable[[ReviewTimelineSample], tuple[object, float, str] | None],
) -> list[tuple[object, float, str]]:
    """Best known read inside each scroll direction. Unknown reads are skipped."""
    grouped: dict[str, list[ReviewTimelineSample]] = {}
    for sample in samples:
        grouped.setdefault(sample.scroll_direction.value, []).append(sample)
    chosen: list[tuple[object, float, str]] = []
    for group in grouped.values():
        known = [item for item in (read(sample) for sample in group) if item is not None]
        if not known:
            continue
        known.sort(key=lambda item: (item[1], item[2]))
        chosen.append(known[-1])
    return chosen


def _merge_known(
    reads: list[tuple[object, float, str]],
) -> tuple[tuple[object, float, str] | None, bool]:
    """Keep one agreed value. A known value plus an unknown value is not a conflict."""
    if not reads:
        return None, False
    values = {read[0] for read in reads}
    if len(values) == 1:
        return reads[0], False
    return None, True


def _field(
    name: str, chosen: tuple[object, float, str] | None, disagreement: bool
) -> EvidenceField | PenaltyField:
    """Build a score or penalty field, including a confident zero."""
    if disagreement:
        field: EvidenceField | PenaltyField = (
            PenaltyField(reason=PASS_DISAGREEMENT)
            if name.endswith("penalty")
            else EvidenceField(reason=PASS_DISAGREEMENT)
        )
        return field
    if chosen is None:
        if name.endswith("penalty"):
            return PenaltyField(reason="unreadable")
        return EvidenceField(reason="unreadable")
    value, confidence, sample_id = chosen
    if name.endswith("penalty"):
        return PenaltyField(
            value=int(value),  # type: ignore[arg-type]
            confidence=confidence,
            source_sample_id=sample_id,
            pill_visible=int(value) != 0,  # type: ignore[arg-type]
        )
    return EvidenceField(
        value=int(value),  # type: ignore[arg-type]
        confidence=confidence,
        source_sample_id=sample_id,
    )


def _slot_read(
    sample: ReviewTimelineSample, slot_id: str
) -> tuple[object, float, str] | None:
    """Return one lamp when that lamp itself was read."""
    for slot in sample.slots:
        if slot.slot_id == slot_id and slot.state != SlotState.unknown:
            return slot.state.value, slot.confidence, sample.sample_id
    return None


def _score_read(
    sample: ReviewTimelineSample, name: str
) -> tuple[object, float, str] | None:
    """Return one remaining or penalty value. Zero is a real penalty observation."""
    value = getattr(sample.score, name)
    if value is None:
        return None
    confidence = float(getattr(sample.score, f"{name}_confidence"))
    return value, confidence, sample.sample_id


def _alive_counts(
    slots: list[CursorSlotObservation],
) -> tuple[EvidenceField, EvidenceField]:
    """Count a team only when all four of its lamps are known."""
    return _alive_count(slots, "top"), _alive_count(slots, "bottom")


def _alive_count(slots: list[CursorSlotObservation], team_row: str) -> EvidenceField:
    """Unknown if any lamp on the team row is unknown."""
    team = [slot for slot in slots if slot.slot_id.startswith(f"{team_row}_")]
    if len(team) != 4 or any(slot.state == SlotState.unknown for slot in team):
        return EvidenceField(reason="incomplete lamps")
    alive = sum(slot.state == SlotState.alive for slot in team)
    sources = sorted({slot.sample_id for slot in team if slot.sample_id})
    return EvidenceField(
        value=alive,
        confidence=min(slot.confidence for slot in team),
        source_sample_id=sources[0] if sources else None,
        source_sample_ids=sources,
    )


def _controller(second: int, segments: list[ControlSegment]) -> EvidenceField:
    """Team color shown by the static control band at this clock second.

    Records top, bottom, neutral, or unknown only. No mode interpretation.
    """
    hits = [
        segment
        for segment in segments
        if segment.start_elapsed <= second < segment.end_elapsed
    ]
    if len(hits) != 1:
        reason = "no control segment" if not hits else "overlapping control"
        return EvidenceField(reason=reason)
    return EvidenceField(value=hits[0].controller, confidence=hits[0].confidence)


def _special_ids(second: int, specials: list[SpecialUseEvent]) -> list[str]:
    """Project each special once, using the explicit half-away-from-zero second."""
    ids: list[str] = []
    for event in specials:
        if project_elapsed_second(event.elapsed_estimate) != second:
            continue
        event_id = f"{event.slot_id}:{event.special_id}:{event.x_norm:.4f}"
        if event_id not in ids:
            ids.append(event_id)
    return ids


def _death_ids(second: int, markers: list[TimelineEventMarker]) -> list[str]:
    """Project each death marker once onto the second that contains it."""
    ids: list[str] = []
    for marker in markers:
        if marker.kind != "death" or marker.elapsed_estimate is None:
            continue
        if project_elapsed_second(marker.elapsed_estimate) != second:
            continue
        if marker.marker_id not in ids:
            ids.append(marker.marker_id)
    return ids


def _provenance(sample: ReviewTimelineSample) -> FrameProvenance:
    """Video time stays provenance. The state key is the Review clock second."""
    return FrameProvenance(
        sample_id=sample.sample_id,
        source_video_time=sample.source_video_time,
        frame_index=sample.frame_index,
        source_pts=sample.source_pts,
        decoded_clock=sample.displayed_clock,
        clock_confidence=sample.clock_confidence,
        scroll_direction=sample.scroll_direction,
    )


def _unknown_slot(second: int, slot_id: str) -> CursorSlotObservation:
    """An explicit unknown lamp. It is not inferred from neighbors."""
    return CursorSlotObservation(
        slot_id=slot_id,
        team_row=slot_id.split("_", maxsplit=1)[0],  # type: ignore[arg-type]
        elapsed_seconds=second,
        sample_id="",
    )


def _reasons(state: ReviewSecondState) -> list[str | None]:
    """Reasons used to recognize a second that had no sample."""
    return [
        state.top_remaining.reason,
        state.bottom_remaining.reason,
        state.controller.reason,
    ]
