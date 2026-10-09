"""Focused contracts for the image-based Review timeline stage."""

from pathlib import Path

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.review.clock import (
    elapsed_from_remaining,
    filename_clock_hint,
    format_elapsed_clock,
    parse_elapsed_clock,
    remaining_seconds,
    resolve_clock,
)
from splatoon3_ai_coach.review.coverage import build_coverage, derive_death_episodes
from splatoon3_ai_coach.review.event_lane import calibrate_axis
from splatoon3_ai_coach.review.models import (
    CursorObservation,
    CursorSlotObservation,
    ReviewSecondState,
    ReviewTimelineSample,
    SlotState,
    TimelineEventMarker,
)
from splatoon3_ai_coach.review.timeline_pipeline import import_timeline


def test_elapsed_clock_is_authoritative_and_signed() -> None:
    assert parse_elapsed_clock("-0:03") == -3
    assert parse_elapsed_clock("5:00") == 300
    assert format_elapsed_clock(-3) == "-0:03"
    assert remaining_seconds(137, 300) == 163
    assert remaining_seconds(301, 300) is None
    assert elapsed_from_remaining(163, 300) == 137


def test_filename_is_only_a_hint_and_conflicts_are_retained() -> None:
    hint = filename_clock_hint("04-59.png")
    assert hint is not None
    resolved, status, warning = resolve_clock(
        manifest=hint,
        filename=None,
        observed=hint.model_copy(update={"elapsed_seconds": 300}),
    )
    assert resolved is not None
    assert status == "conflict"
    assert warning is not None


def test_coverage_reports_missing_duplicates_and_non_monotonic() -> None:
    samples = [_sample(i, value) for i, value in enumerate([0, 1, 1, 3, 2])]
    report = build_coverage(samples)
    assert report.missing_elapsed_seconds == []
    assert report.duplicate_elapsed_seconds == [1]
    assert report.non_monotonic_capture_indexes == [2, 4]


def test_axis_is_least_squares_with_observed_residual_metrics() -> None:
    samples = [_sample(index, elapsed) for index, elapsed in enumerate((1, 12, 19))]
    for sample, x_norm in zip(samples, (0.0, 0.5, 1.0), strict=True):
        sample.cursor.x_norm = x_norm
    axis = calibrate_axis(
        samples,
        ReviewTimelineConfig(
            axis_max_mean_error_seconds=2,
            axis_max_error_seconds=2,
        ),
    )
    assert axis is not None
    assert axis.point_count == 3
    assert axis.mean_absolute_error_seconds > 0
    assert axis.max_absolute_error_seconds > axis.mean_absolute_error_seconds


def test_bad_axis_is_rejected_by_configured_quality_thresholds() -> None:
    samples = [_sample(index, elapsed) for index, elapsed in enumerate((0, 50, 1))]
    for sample, x_norm in zip(samples, (0.0, 0.5, 1.0), strict=True):
        sample.cursor.x_norm = x_norm
    assert calibrate_axis(samples, ReviewTimelineConfig()) is None


def test_axis_excludes_negative_opening_countdown_labels() -> None:
    samples = [_sample(index, elapsed) for index, elapsed in enumerate((-3, 300, 0))]
    for sample, x_norm in zip(samples, (0.08, 0.08, 0.92), strict=True):
        sample.cursor.x_norm = x_norm
    axis = calibrate_axis(
        samples,
        ReviewTimelineConfig(axis_min_points=2),
    )
    assert axis is not None
    assert axis.point_count == 2
    assert round(axis.elapsed_at(0.08), 6) == 300


def test_death_onset_and_recovery_are_bounded_not_exact() -> None:
    samples = [
        _sample(0, 13, SlotState.alive),
        _sample(1, 12, SlotState.dead),
        _sample(2, 11, SlotState.dead),
        _sample(3, 10, SlotState.alive),
    ]
    [episode] = derive_death_episodes(_states(samples))
    assert episode.last_alive_elapsed == 13
    assert episode.first_dead_elapsed == 12
    assert episode.last_dead_elapsed == 11
    assert episode.next_alive_elapsed == 10
    assert not episode.onset_is_exact


def test_death_marker_yields_bounded_respawn_duration() -> None:
    samples = [
        _sample(0, 19, SlotState.alive),
        _sample(1, 16, SlotState.dead),
        _sample(2, 12, SlotState.dead),
        _sample(3, 10, SlotState.alive),
    ]
    marker = TimelineEventMarker(
        marker_id="death-top-1",
        team_row="top",
        kind="death",
        x_norm=0.5,
        elapsed_estimate=17,
        player_slot_id="top_1",
    )
    [episode] = derive_death_episodes(_states(samples), [marker])
    assert episode.death_elapsed_estimate == 17
    assert episode.respawn_seconds_min == 5
    assert episode.respawn_seconds_max == 7
    assert episode.warnings == []


def test_import_is_deterministic_and_does_not_need_video_tools(tmp_path: Path) -> None:
    for index, value in enumerate((10, 11, 13)):
        _write_synthetic_image(tmp_path / f"{index:02d}.png", value)
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "entries:\n"
        "  - {capture_index: 0, image_path: '00.png', elapsed_clock: '0:10'}\n"
        "  - {capture_index: 1, image_path: '01.png', elapsed_clock: '0:11'}\n"
        "  - {capture_index: 2, image_path: '02.png', elapsed_clock: '0:13'}\n",
        encoding="utf-8",
    )
    first = import_timeline(
        tmp_path,
        config=ReviewTimelineConfig(),
        manifest_path=manifest,
    )
    second = import_timeline(
        tmp_path,
        config=ReviewTimelineConfig(),
        manifest_path=manifest,
    )
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.coverage.missing_elapsed_seconds == [12]


def _states(samples: list[ReviewTimelineSample]) -> list[ReviewSecondState]:
    """Project the legacy sample lamps onto the canonical per-second states."""
    return [
        ReviewSecondState(
            elapsed_second=int(sample.elapsed_seconds or 0),
            slots=sample.slots,
        )
        for sample in samples
        if sample.elapsed_seconds is not None
    ]


def _sample(
    index: int,
    elapsed: int,
    state: SlotState = SlotState.unknown,
) -> ReviewTimelineSample:
    return ReviewTimelineSample(
        sample_id=f"sample-{index}",
        capture_index=index,
        image_path=f"image-{index}.png",
        image_sha256="hash",
        elapsed_seconds=elapsed,
        displayed_clock=f"0:{elapsed:02d}",
        clock_source="manifest",
        clock_status="confirmed",
        clock_confidence=1,
        cursor=CursorObservation(x=float(index), confidence=1),
        slots=[
            CursorSlotObservation(
                slot_id="top_1",
                team_row="top",
                state=state,
                confidence=1,
                sample_id=f"sample-{index}",
                elapsed_seconds=elapsed,
            )
        ],
    )


def _write_synthetic_image(path: Path, value: int) -> None:
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    image[300:665, 100 + value : 110 + value] = 255
    image[760:948, 100 + value : 110 + value] = 255
    image[965:1010, 100 + value : 110 + value] = 255
    cv2.imwrite(str(path), image)

