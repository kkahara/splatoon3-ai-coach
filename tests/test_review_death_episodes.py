"""Death episodes on the Review remaining clock (larger value = earlier)."""

from splatoon3_ai_coach.review.coverage import (
    DEATH_MARKER_LAMP_DISAGREEMENT,
    derive_death_episodes,
)
from splatoon3_ai_coach.review.models import (
    CursorSlotObservation,
    ReviewSecondState,
    SlotState,
    TimelineEventMarker,
)

ALIVE, DEAD, UNKNOWN = SlotState.alive, SlotState.dead, SlotState.unknown


def _states(
    lamps: list[tuple[int, SlotState]], slot_id: str = "top_1"
) -> list[ReviewSecondState]:
    return [
        ReviewSecondState(
            elapsed_second=second,
            slots=[
                CursorSlotObservation(
                    slot_id=slot_id,
                    team_row="top",
                    state=state,
                    confidence=1,
                    sample_id=f"s{second}",
                    elapsed_seconds=second,
                )
            ],
        )
        for second, state in lamps
    ]


def _death(estimate: float, slot_id: str = "top_1") -> TimelineEventMarker:
    return TimelineEventMarker(
        marker_id=f"death-{slot_id}-{estimate}",
        team_row="top",
        kind="death",
        x_norm=0.5,
        elapsed_estimate=estimate,
        player_slot_id=slot_id,
    )


def test_marker_episode_follows_the_countdown() -> None:
    states = _states([(205, ALIVE), (199, DEAD), (190, DEAD), (180, ALIVE)])
    [episode] = derive_death_episodes(states, [_death(200)])
    assert episode.last_alive_elapsed == 205
    assert episode.first_dead_elapsed == 199
    assert episode.first_dead_sample_id == "s199"
    assert episode.last_dead_elapsed == 190
    assert episode.next_alive_elapsed == 180
    assert episode.recovery_sample_id == "s180"
    assert episode.respawn_seconds_min == 10
    assert episode.respawn_seconds_max == 20
    assert episode.warnings == []


def test_same_slot_deaths_stay_in_their_own_episode() -> None:
    states = _states(
        [
            (205, ALIVE),
            (199, DEAD),
            (190, DEAD),
            (180, ALIVE),
            (150, ALIVE),
            (140, DEAD),
            (130, DEAD),
            (120, ALIVE),
        ]
    )
    first, second = derive_death_episodes(states, [_death(145), _death(200)])
    assert first.death_elapsed_estimate == 200
    assert (first.last_alive_elapsed, first.first_dead_elapsed) == (205, 199)
    assert (first.last_dead_elapsed, first.next_alive_elapsed) == (190, 180)
    assert second.death_elapsed_estimate == 145
    assert (second.last_alive_elapsed, second.first_dead_elapsed) == (150, 140)
    assert (second.last_dead_elapsed, second.next_alive_elapsed) == (130, 120)


def test_next_death_bounds_the_episode_without_a_recovery_lamp() -> None:
    states = _states([(205, ALIVE), (199, DEAD), (140, DEAD), (120, ALIVE)])
    first, second = derive_death_episodes(states, [_death(200), _death(145)])
    assert first.last_dead_elapsed == 199
    assert first.next_alive_elapsed is None
    assert first.respawn_seconds_max is None
    assert second.first_dead_elapsed == 140
    assert second.next_alive_elapsed == 120


def test_episode_closes_at_first_recovery_before_an_unmarked_dead_run() -> None:
    states = _states([(205, ALIVE), (199, DEAD), (190, DEAD), (180, ALIVE), (150, DEAD)])
    [episode] = derive_death_episodes(states, [_death(200)])
    assert episode.last_dead_elapsed == 190
    assert episode.next_alive_elapsed == 180
    assert episode.respawn_seconds_max == 20


def test_missing_seconds_widen_the_bounds() -> None:
    states = _states([(205, ALIVE), (199, DEAD), (180, ALIVE)])
    [episode] = derive_death_episodes(states, [_death(200)])
    assert episode.last_dead_elapsed == 199
    assert episode.next_alive_elapsed == 180
    assert episode.respawn_seconds_min == 1
    assert episode.respawn_seconds_max == 20


def test_unknown_lamps_are_not_evidence() -> None:
    states = _states(
        [(205, ALIVE), (201, UNKNOWN), (199, DEAD), (185, UNKNOWN), (180, ALIVE)]
    )
    [episode] = derive_death_episodes(states, [_death(200)])
    assert episode.last_alive_elapsed == 205
    assert episode.last_dead_elapsed == 199
    assert episode.next_alive_elapsed == 180


def test_match_ending_before_recovery_leaves_the_upper_bound_open() -> None:
    states = _states([(12, ALIVE), (9, DEAD), (0, DEAD)])
    [episode] = derive_death_episodes(states, [_death(10)])
    assert episode.last_dead_elapsed == 0
    assert episode.next_alive_elapsed is None
    assert episode.respawn_seconds_min == 10
    assert episode.respawn_seconds_max is None
    assert episode.warnings == []


def test_overtime_continues_below_zero() -> None:
    states = _states([(4, ALIVE), (1, DEAD), (-3, DEAD), (-8, ALIVE)])
    [episode] = derive_death_episodes(states, [_death(2)])
    assert episode.last_dead_elapsed == -3
    assert episode.next_alive_elapsed == -8
    assert episode.respawn_seconds_min == 5
    assert episode.respawn_seconds_max == 10


def test_dead_lamps_from_earlier_in_the_match_do_not_confirm_a_marker() -> None:
    states = _states([(210, DEAD), (205, ALIVE), (195, ALIVE)])
    [episode] = derive_death_episodes(states, [_death(200)])
    assert episode.first_dead_elapsed is None
    assert episode.warnings == [DEATH_MARKER_LAMP_DISAGREEMENT]


def test_lamp_only_episodes_are_chronological() -> None:
    states = _states([(205, ALIVE), (199, DEAD), (190, DEAD), (180, ALIVE), (140, DEAD)])
    first, second = derive_death_episodes(states)
    assert (first.last_alive_elapsed, first.first_dead_elapsed) == (205, 199)
    assert (first.last_dead_elapsed, first.next_alive_elapsed) == (190, 180)
    assert (second.last_alive_elapsed, second.first_dead_elapsed) == (180, 140)
    assert second.next_alive_elapsed is None
