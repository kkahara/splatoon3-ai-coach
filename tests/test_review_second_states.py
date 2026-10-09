"""Per-second Review evidence: score, merge, projection, and respawn bounds."""

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
import typer

from splatoon3_ai_coach.cli.review import _battle_mode
from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.review.coverage import derive_death_episodes
from splatoon3_ai_coach.review.cursor import detect_cursor
from splatoon3_ai_coach.review.cursor_score import CursorScoreReader, penalty_result
from splatoon3_ai_coach.review.models import (
    ControlSegment,
    CoverageReport,
    CursorObservation,
    CursorScoreObservation,
    CursorSlotObservation,
    ReviewTimelineDataset,
    ReviewTimelineSample,
    SlotState,
    SpecialUseEvent,
    TimelineEventMarker,
    project_elapsed_second,
)
from splatoon3_ai_coach.review.second_states import (
    PASS_DISAGREEMENT,
    annotate_coverage,
    build_second_states,
)
from splatoon3_ai_coach.review.team_identity import observe_row_hues
from splatoon3_ai_coach.review.timeline_pipeline import _mode_from_gameplay

FIXTURES = Path(__file__).parent / "fixtures" / "review"


def test_projection_rounds_halves_away_from_zero() -> None:
    assert project_elapsed_second(12.49) == 12
    assert project_elapsed_second(12.50) == 13
    assert project_elapsed_second(12.51) == 13


def test_schema_11_stays_without_synthesized_states() -> None:
    dataset = ReviewTimelineDataset(
        schema_version="1.1",
        coverage=CoverageReport(sample_count=0, clocked_sample_count=0),
    )
    loaded = ReviewTimelineDataset.model_validate(
        dataset.model_dump(mode="json") | {"second_states": []}
    )
    assert loaded.schema_version == "1.1"
    assert loaded.second_states == []
    assert ReviewTimelineDataset(
        coverage=CoverageReport(sample_count=0, clocked_sample_count=0)
    ).schema_version == "1.2"


def test_dense_interval_default_is_every_frame() -> None:
    config = ReviewTimelineConfig()
    assert config.video.sample_interval_seconds == 0
    assert config.video.frames_per_second_bucket == 1


def test_orange_penalty_stays_with_its_label_above_the_other_team() -> None:
    observation = _read_fixture("timeline_21-56-52.jpg")
    assert observation.top_remaining == 77
    assert observation.top_penalty == 18
    assert observation.top_penalty_pill_visible is True
    assert observation.bottom_remaining == 72
    assert observation.bottom_penalty == 0
    assert observation.bottom_penalty_pill_visible is False


def test_both_penalty_pills_can_be_visible() -> None:
    observation = _read_fixture("timeline_penalty_both.png")
    assert observation.top_remaining == 78
    assert observation.top_penalty == 14
    assert observation.bottom_remaining == 40
    assert observation.bottom_penalty == 32
    assert observation.top_penalty_pill_visible is True
    assert observation.bottom_penalty_pill_visible is True


def test_team_identity_uses_color_when_the_lower_label_is_the_top_team() -> None:
    image = cv2.imread(str(FIXTURES / "timeline_21-56-52.jpg"))
    assert image is not None
    swapped = _swap_label_rows(image)
    observation = _read_image(swapped, top_hue=6.0, bottom_hue=136.0)
    assert observation.top_remaining == 77
    assert observation.top_penalty == 18
    assert observation.bottom_remaining == 72
    assert observation.bottom_penalty == 0


def test_mode_is_metadata_from_cli_or_gameplay(tmp_path: Path) -> None:
    assert _battle_mode(None) is None
    for mode in ("splat_zones", "tower_control", "rainmaker", "clam_blitz"):
        assert _battle_mode(mode) == mode
    with pytest.raises(typer.BadParameter):
        _battle_mode("turf_war")
    assert _mode_from_gameplay(None) is None
    assert _mode_from_gameplay(tmp_path) is None
    (tmp_path / "match_identity.json").write_text(
        json.dumps({"battle_mode_id": "rainmaker"}), encoding="utf-8"
    )
    assert _mode_from_gameplay(tmp_path) == "rainmaker"


def test_penalty_semantics_distinguish_zero_from_unknown() -> None:
    assert penalty_result(None, 0.0, pill_present=False) == (0, 1.0, False, None)
    assert penalty_result(14, 0.8, pill_present=True) == (14, 0.8, True, None)
    assert penalty_result(None, 0.0, pill_present=True) == (
        None,
        0.0,
        True,
        "unreadable",
    )


def test_unknown_label_leaves_penalty_unknown() -> None:
    image = cv2.imread(str(FIXTURES / "timeline_21-56-52.jpg"))
    assert image is not None
    observation = _read_image(image, top_hue=40.0, bottom_hue=40.0)
    assert observation.top_remaining is None
    assert observation.top_penalty is None
    assert observation.bottom_penalty is None
    assert observation.top_penalty_reason == "label unknown"


def test_fields_may_come_from_different_candidates() -> None:
    lamp_slots = _known_slots("lamps", 20, SlotState.alive)
    lamps = _sample("lamps", 20, "forward", slots=lamp_slots)
    lamps.score = CursorScoreObservation(
        top_remaining=1,
        top_remaining_confidence=0.2,
        bottom_remaining=1,
        bottom_remaining_confidence=0.2,
    )
    score = _sample("score", 20, "forward")
    score.score = CursorScoreObservation(
        top_remaining=77,
        top_remaining_confidence=0.96,
        bottom_remaining=72,
        bottom_remaining_confidence=0.9,
        top_penalty=1,
        top_penalty_confidence=0.2,
        bottom_penalty=0,
        bottom_penalty_confidence=0.2,
        bottom_penalty_pill_visible=False,
    )
    penalty = _sample("penalty", 20, "forward")
    penalty.score = CursorScoreObservation(
        top_penalty=18,
        top_penalty_confidence=0.91,
        top_penalty_pill_visible=True,
        bottom_penalty=0,
        bottom_penalty_confidence=0.4,
        bottom_penalty_pill_visible=False,
    )
    [state] = build_second_states([lamps, score, penalty], [], [], [])
    assert state.slots[0].state == SlotState.alive
    assert state.slots[0].sample_id == "lamps"
    assert state.top_remaining.value == 77
    assert state.top_remaining.source_sample_id == "score"
    assert state.top_penalty.value == 18
    assert state.top_penalty.source_sample_id == "penalty"
    assert state.top_alive_count.value == 4


def test_pass_merge_keeps_known_values_and_rejects_conflicts() -> None:
    forward = _sample("forward", 30, "forward", remaining=77, penalty=18)
    backward_same = _sample("backward", 30, "backward", remaining=77, penalty=18)
    [agreed] = build_second_states([forward, backward_same], [], [], [])
    assert agreed.top_remaining.value == 77
    assert agreed.warnings == []

    only_forward = _sample("forward", 31, "forward", remaining=40, penalty=0)
    [kept] = build_second_states([only_forward], [], [], [])
    assert kept.top_remaining.value == 40
    assert kept.top_penalty.value == 0
    assert kept.top_penalty.pill_visible is False

    conflict = _sample("backward", 31, "backward", remaining=41, penalty=0)
    [disagreed] = build_second_states([only_forward, conflict], [], [], [])
    assert disagreed.top_remaining.value is None
    assert disagreed.top_remaining.reason == PASS_DISAGREEMENT
    assert f"{PASS_DISAGREEMENT}:top_remaining" in disagreed.warnings
    assert disagreed.top_penalty.value == 0


def test_unreadable_pill_stays_unknown_after_merge() -> None:
    sample = _sample("pill", 40, "forward", remaining=60)
    sample.score.top_penalty_pill_visible = True
    sample.score.top_penalty_reason = "unreadable"
    [state] = build_second_states([sample], [], [], [])
    assert state.top_penalty.value is None
    assert state.top_penalty.pill_visible is True
    assert state.top_penalty.reason == "unreadable"


def test_one_state_per_second_across_passes() -> None:
    samples = [
        _sample("f10", 10, "forward"),
        _sample("f11", 11, "forward"),
        _sample("b11", 11, "backward"),
        _sample("b10", 10, "backward"),
    ]
    states = build_second_states(samples, [], [], [])
    assert [state.elapsed_second for state in states] == [10, 11]
    assert {item.sample_id for item in states[1].provenance} == {"f11", "b11"}


def test_controller_is_the_band_team_at_that_second() -> None:
    segments = [
        ControlSegment(start_elapsed=0, end_elapsed=5, controller="top", confidence=1),
        ControlSegment(
            start_elapsed=5, end_elapsed=9, controller="neutral", confidence=1
        ),
    ]
    states = build_second_states(
        [_sample("a", 4, "forward"), _sample("b", 5, "forward")], [], [], segments
    )
    assert [state.controller.value for state in states] == ["top", "neutral"]


def test_alive_count_requires_four_known_lamps() -> None:
    partial = _sample("partial", 5, "forward")
    partial.slots = _known_slots("partial", 5, SlotState.alive)[:3]
    partial.slots.append(
        CursorSlotObservation(
            slot_id="top_4",
            team_row="top",
            state=SlotState.unknown,
            sample_id="partial",
            elapsed_seconds=5,
        )
    )
    [state] = build_second_states([partial], [], [], [])
    assert state.top_alive_count.value is None


def test_missing_second_is_unknown_and_not_interpolated() -> None:
    samples = [
        _sample("a", 10, "forward", remaining=70, penalty=1),
        _sample("b", 12, "forward", remaining=68, penalty=3),
    ]
    states = build_second_states(samples, [], [], [])
    assert [state.elapsed_second for state in states] == [10, 11, 12]
    missing = states[1]
    assert missing.top_remaining.value is None
    assert missing.top_penalty.value is None
    assert missing.slots[0].state == SlotState.unknown
    coverage = annotate_coverage(
        CoverageReport(sample_count=2, clocked_sample_count=2), states
    )
    assert coverage.missing_elapsed_seconds == [11]
    assert coverage.observed_seconds == [10, 12]
    assert coverage.complete is False
    assert coverage.expected_seconds == [10, 11, 12]


def test_events_project_once_onto_the_containing_second() -> None:
    special = SpecialUseEvent(
        slot_id="top_1",
        team_row="top",
        special_id="ink_storm",
        elapsed_estimate=12.5,
        x_norm=0.4,
        center_x=400,
        score=0.9,
        runner_up_margin=0.2,
    )
    duplicate = special.model_copy()
    marker = TimelineEventMarker(
        marker_id="death-1",
        team_row="top",
        kind="death",
        x_norm=0.4,
        elapsed_estimate=12.49,
        player_slot_id="top_1",
    )
    states = build_second_states(
        [_sample("a", 12, "forward"), _sample("b", 13, "forward")],
        [marker, marker],
        [special, duplicate],
        [
            ControlSegment(
                start_elapsed=0, end_elapsed=20, controller="top", confidence=0.8
            )
        ],
    )
    by_second = {state.elapsed_second: state for state in states}
    assert by_second[12].death_marker_ids == ["death-1"]
    assert by_second[12].special_ids == []
    assert by_second[13].special_ids == ["top_1:ink_storm:0.4000"]
    assert by_second[12].controller.value == "top"
    assert len(by_second) == 2


def test_respawn_bound_is_one_second_when_coverage_is_complete() -> None:
    samples = []
    for second in range(10, 16):
        lamp = SlotState.dead if second in {12, 13} else SlotState.alive
        samples.append(
            _sample(
                f"s{second}",
                second,
                "forward",
                slots=_known_slots(f"s{second}", second, lamp),
            )
        )
    built = build_second_states(samples, [], [], [])
    marker = TimelineEventMarker(
        marker_id="death-top-1",
        team_row="top",
        kind="death",
        x_norm=0.5,
        elapsed_estimate=13,
        player_slot_id="top_1",
    )
    [episode] = derive_death_episodes(built, [marker])
    assert episode.death_elapsed_estimate == 13
    assert episode.last_alive_elapsed == 14
    assert episode.last_dead_elapsed == 12
    assert episode.next_alive_elapsed == 11
    assert episode.respawn_seconds_max is not None
    assert episode.respawn_seconds_min is not None
    assert episode.respawn_seconds_max - episode.respawn_seconds_min <= 1
    assert episode.warnings == []


def test_missing_seconds_widen_the_respawn_bound() -> None:
    samples = []
    lamps = ((16, SlotState.alive), (15, SlotState.dead), (11, SlotState.alive))
    for second, lamp in lamps:
        samples.append(
            _sample(
                f"s{second}",
                second,
                "forward",
                slots=_known_slots(f"s{second}", second, lamp),
            )
        )
    states = build_second_states(samples, [], [], [])
    assert [state.elapsed_second for state in states] == [11, 12, 13, 14, 15, 16]
    marker = TimelineEventMarker(
        marker_id="death-top-1",
        team_row="top",
        kind="death",
        x_norm=0.5,
        elapsed_estimate=15,
        player_slot_id="top_1",
    )
    [episode] = derive_death_episodes(states, [marker])
    assert episode.last_dead_elapsed == 15
    assert episode.next_alive_elapsed == 11
    assert episode.respawn_seconds_min == 0
    assert episode.respawn_seconds_max == 4


def test_marker_without_a_dead_lamp_keeps_the_disagreement_warning() -> None:
    alive = _known_slots("alive", 12, SlotState.alive)
    sample = _sample("alive", 12, "forward", slots=alive)
    marker = TimelineEventMarker(
        marker_id="death-top-1",
        team_row="top",
        kind="death",
        x_norm=0.2,
        elapsed_estimate=12,
        player_slot_id="top_1",
    )
    [episode] = derive_death_episodes(build_second_states([sample], [], [], []), [marker])
    assert episode.warnings == ["DEATH_MARKER_LAMP_DISAGREEMENT"]


def _read_fixture(name: str) -> CursorScoreObservation:
    image = cv2.imread(str(FIXTURES / name))
    assert image is not None
    return _read_image(image, top_hue=None, bottom_hue=None)


def _read_image(
    image: np.ndarray, *, top_hue: float | None, bottom_hue: float | None
) -> CursorScoreObservation:
    config = ReviewTimelineConfig()
    cursor = detect_cursor(image, config)
    assert cursor.x is not None
    if top_hue is None or bottom_hue is None:
        sample = ReviewTimelineSample(
            sample_id="fixture",
            capture_index=0,
            image_path="fixture",
            image_sha256="hash",
            cursor=CursorObservation(x=cursor.x, confidence=cursor.confidence),
        )
        top_hue, bottom_hue = observe_row_hues({"fixture": image}, [sample], config)
    return CursorScoreReader().read(
        image,
        int(cursor.x),
        top_hue=top_hue,
        bottom_hue=bottom_hue,
        config=config,
    )


def _swap_label_rows(image: np.ndarray) -> np.ndarray:
    """Move the orange label below the purple label without changing their colors."""
    from splatoon3_ai_coach.review.cursor_score import _find_labels

    config = ReviewTimelineConfig()
    cursor = detect_cursor(image, config)
    labels = _find_labels(image, int(cursor.x or 0), config)
    labels.sort(key=lambda label: label.hue)
    orange, purple = labels[0], labels[1]
    swapped = image.copy()
    extra = 36
    orange_crop = _crop_with_pill(image, orange, extra)
    purple_crop = _crop_with_pill(image, purple, extra)
    _paint_box(swapped, orange.x, orange.y, orange.width, orange.height + extra)
    _paint_box(swapped, purple.x, purple.y, purple.width, purple.height + extra)
    _paste(swapped, purple_crop, orange.x, orange.y)
    _paste(swapped, orange_crop, purple.x, purple.y)
    return swapped


def _crop_with_pill(image: np.ndarray, label: object, extra: int) -> np.ndarray:
    y = label.y  # type: ignore[attr-defined]
    x = label.x  # type: ignore[attr-defined]
    height = label.height + extra  # type: ignore[attr-defined]
    width = label.width  # type: ignore[attr-defined]
    return image[y : y + height, x : x + width].copy()


def _paint_box(image: np.ndarray, x: int, y: int, width: int, height: int) -> None:
    image[y : y + height, x : x + width] = (40, 40, 40)


def _paste(image: np.ndarray, crop: np.ndarray, x: int, y: int) -> None:
    height, width = crop.shape[:2]
    image[y : y + height, x : x + width] = crop


def _sample(
    sample_id: str,
    elapsed: int,
    direction: str,
    *,
    remaining: int | None = None,
    penalty: int | None = None,
    slots: list[CursorSlotObservation] | None = None,
) -> ReviewTimelineSample:
    score = CursorScoreObservation()
    if remaining is not None:
        score.top_remaining = remaining
        score.top_remaining_confidence = 0.9
        score.bottom_remaining = remaining
        score.bottom_remaining_confidence = 0.9
    if penalty is not None:
        score.top_penalty = penalty
        score.top_penalty_confidence = 0.8
        score.top_penalty_pill_visible = penalty != 0
        score.bottom_penalty = 0
        score.bottom_penalty_confidence = 0.8
        score.bottom_penalty_pill_visible = False
    return ReviewTimelineSample(
        sample_id=sample_id,
        capture_index=elapsed,
        image_path=f"{sample_id}.jpg",
        image_sha256="hash",
        elapsed_seconds=elapsed,
        displayed_clock=f"0:{elapsed:02d}",
        clock_confidence=1,
        scroll_direction=direction,  # type: ignore[arg-type]
        cursor=CursorObservation(x=100, confidence=1),
        slots=slots or [],
        score=score,
    )


def _known_slots(
    sample_id: str, elapsed: int, state: SlotState
) -> list[CursorSlotObservation]:
    return [
        CursorSlotObservation(
            slot_id=f"{row}_{index}",
            team_row=row,  # type: ignore[arg-type]
            state=state,
            confidence=0.9,
            sample_id=sample_id,
            elapsed_seconds=elapsed,
        )
        for row in ("top", "bottom")
        for index in range(1, 5)
    ]
