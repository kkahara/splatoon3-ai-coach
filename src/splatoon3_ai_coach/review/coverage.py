"""Coverage and bounded-transition calculations for Review sweeps."""

from __future__ import annotations

from collections import Counter

from .models import (
    CoverageReport,
    CursorSlotObservation,
    PlayerDeathEpisodeEvidence,
    ReviewTimelineSample,
)


def build_coverage(samples: list[ReviewTimelineSample]) -> CoverageReport:
    """Summarize clock coverage without rejecting imperfect sweeps."""
    clocked = [
        sample for sample in samples if sample.elapsed_seconds is not None
    ]
    values = [sample.elapsed_seconds for sample in clocked]
    counts = Counter(values)
    unique = sorted(set(values))
    missing = (
        [value for value in range(unique[0], unique[-1] + 1) if value not in counts]
        if unique
        else []
    )
    non_monotonic: list[int] = []
    previous: int | None = None
    for sample in sorted(clocked, key=lambda item: item.capture_index):
        current = sample.elapsed_seconds
        assert current is not None
        if previous is not None and current <= previous:
            non_monotonic.append(sample.capture_index)
        previous = current
    return CoverageReport(
        sample_count=len(samples),
        clocked_sample_count=len(clocked),
        first_elapsed_seconds=min(values) if values else None,
        last_elapsed_seconds=max(values) if values else None,
        missing_elapsed_seconds=missing,
        duplicate_elapsed_seconds=sorted(
            value for value, count in counts.items() if count > 1
        ),
        non_monotonic_capture_indexes=non_monotonic,
        unreadable_sample_ids=[
            sample.sample_id for sample in samples if sample.elapsed_seconds is None
        ],
    )


def derive_death_episodes(
    samples: list[ReviewTimelineSample],
) -> list[PlayerDeathEpisodeEvidence]:
    """Infer bounded down episodes from explicit slot states.

    Unknown samples break certainty but do not create an exact timestamp.
    """
    by_slot: dict[str, list[CursorSlotObservation]] = {}
    for sample in samples:
        for slot in sample.slots:
            if slot.elapsed_seconds is not None:
                by_slot.setdefault(slot.slot_id, []).append(slot)

    episodes: list[PlayerDeathEpisodeEvidence] = []
    for slot_id, observations in sorted(by_slot.items()):
        observations.sort(key=lambda item: item.elapsed_seconds or 0)
        episodes.extend(_slot_episodes(slot_id, observations))
    return episodes


def _slot_episodes(
    slot_id: str, observations: list[CursorSlotObservation]
) -> list[PlayerDeathEpisodeEvidence]:
    episodes: list[PlayerDeathEpisodeEvidence] = []
    active: PlayerDeathEpisodeEvidence | None = None
    previous_known: CursorSlotObservation | None = None
    for observation in observations:
        if observation.state.value == "dead" and active is None:
            active = PlayerDeathEpisodeEvidence(
                slot_id=slot_id,
                team_row=observation.team_row,
                last_alive_elapsed=(
                    previous_known.elapsed_seconds
                    if previous_known and previous_known.state.value == "alive"
                    else None
                ),
                first_dead_elapsed=observation.elapsed_seconds,
                first_dead_sample_id=observation.sample_id,
            )
        elif (
            observation.state.value == "alive"
            and active is not None
        ):
            active.next_alive_elapsed = observation.elapsed_seconds
            active.recovery_sample_id = observation.sample_id
            episodes.append(active)
            active = None
        if observation.state.value in {"alive", "dead"}:
            if active is not None and observation.state.value == "dead":
                active.last_dead_elapsed = observation.elapsed_seconds
            previous_known = observation
    if active is not None:
        episodes.append(active)
    return episodes

