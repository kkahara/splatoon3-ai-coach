"""Coverage and bounded-transition calculations for Review sweeps.

Clock convention: ``elapsed_seconds`` / ``elapsed_estimate`` hold the Review
timeline label, which is a *remaining* game clock (5:00 at the left, 0:00 at
the right; overtime continues below zero). A larger value is earlier in the
match. Death episodes are therefore walked in descending clock order.
"""

from __future__ import annotations

from collections import Counter

from .models import (
    CoverageReport,
    CursorSlotObservation,
    PlayerDeathEpisodeEvidence,
    ReviewSecondState,
    ReviewTimelineSample,
    TimelineEventMarker,
)

DEATH_MARKER_LAMP_DISAGREEMENT = "DEATH_MARKER_LAMP_DISAGREEMENT"


def build_coverage(samples: list[ReviewTimelineSample]) -> CoverageReport:
    """Summarize clock coverage without rejecting imperfect sweeps."""
    clocked = [sample for sample in samples if sample.elapsed_seconds is not None]
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
    states: list[ReviewSecondState],
    markers: list[TimelineEventMarker] | None = None,
) -> list[PlayerDeathEpisodeEvidence]:
    """Infer bounded down episodes from per-second lamp states only.

    The bound is the resolution of the seconds that were actually read.
    Missing seconds are not filled in, so they widen the interval.
    """
    by_slot: dict[str, list[CursorSlotObservation]] = {}
    for state in states:
        for slot in state.slots:
            if slot.elapsed_seconds is None:
                continue
            by_slot.setdefault(slot.slot_id, []).append(slot)

    if markers is not None:
        return _marker_episodes(by_slot, markers)
    episodes: list[PlayerDeathEpisodeEvidence] = []
    for slot_id, observations in sorted(by_slot.items()):
        observations.sort(key=lambda item: -(item.elapsed_seconds or 0))
        episodes.extend(_slot_episodes(slot_id, observations))
    return episodes


def _marker_episodes(
    by_slot: dict[str, list[CursorSlotObservation]],
    markers: list[TimelineEventMarker],
) -> list[PlayerDeathEpisodeEvidence]:
    episodes: list[PlayerDeathEpisodeEvidence] = []
    deaths = [
        marker
        for marker in markers
        if marker.kind == "death"
        and marker.player_slot_id is not None
        and marker.elapsed_estimate is not None
    ]
    # Chronological order: the remaining clock decreases as the match goes on.
    ordered = sorted(deaths, key=lambda item: -float(item.elapsed_estimate or 0))
    for marker in ordered:
        slot_id = str(marker.player_slot_id)
        death = float(marker.elapsed_estimate or 0)
        next_death = next(
            (
                float(candidate.elapsed_estimate or 0)
                for candidate in ordered
                if candidate.player_slot_id == slot_id
                and float(candidate.elapsed_estimate or 0) < death
            ),
            None,
        )
        episodes.append(_episode_for_marker(marker, by_slot.get(slot_id, []), next_death))
    return episodes


def _episode_for_marker(
    marker: TimelineEventMarker,
    observations: list[CursorSlotObservation],
    next_death: float | None,
) -> PlayerDeathEpisodeEvidence:
    """Bound one marked death with same-slot lamps before the slot's next death.

    All comparisons use the remaining clock: "before the death" is a larger
    value, "after" a smaller one, and durations are ``death - later``. The
    episode closes at the first alive lamp after its first dead lamp, so a
    later dead run without its own marker is not absorbed into this one.
    """
    death = float(marker.elapsed_estimate or 0)
    known = [
        (float(item.elapsed_seconds), item)
        for item in observations
        if item.elapsed_seconds is not None
        and item.state.value != "unknown"
        and (next_death is None or float(item.elapsed_seconds) > next_death)
    ]
    alive = [(clock, item) for clock, item in known if item.state.value == "alive"]
    dead = [
        (clock, item)
        for clock, item in known
        if item.state.value == "dead" and clock <= death
    ]
    last_alive = min(
        (pair for pair in alive if pair[0] >= death),
        default=None,
        key=lambda pair: pair[0],
    )
    first_dead = max(dead, default=None, key=lambda pair: pair[0])
    after = first_dead[0] if first_dead else death
    next_alive = max(
        (pair for pair in alive if pair[0] < after),
        default=None,
        key=lambda pair: pair[0],
    )
    last_dead = min(
        (pair for pair in dead if next_alive is None or pair[0] > next_alive[0]),
        default=None,
        key=lambda pair: pair[0],
    )
    return PlayerDeathEpisodeEvidence(
        slot_id=str(marker.player_slot_id),
        team_row=marker.team_row if marker.team_row != "unknown" else None,
        last_alive_elapsed=last_alive[1].elapsed_seconds if last_alive else None,
        first_dead_elapsed=first_dead[1].elapsed_seconds if first_dead else None,
        first_dead_sample_id=first_dead[1].sample_id if first_dead else None,
        last_dead_elapsed=last_dead[1].elapsed_seconds if last_dead else None,
        next_alive_elapsed=next_alive[1].elapsed_seconds if next_alive else None,
        recovery_sample_id=next_alive[1].sample_id if next_alive else None,
        candidate_marker_ids=[marker.marker_id],
        death_elapsed_estimate=death,
        respawn_seconds_min=(
            max(0.0, death - last_dead[0]) if last_dead is not None else None
        ),
        respawn_seconds_max=(
            max(0.0, death - next_alive[0]) if next_alive is not None else None
        ),
        warnings=[] if dead else [DEATH_MARKER_LAMP_DISAGREEMENT],
    )


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
        elif observation.state.value == "alive" and active is not None:
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
