"""Observed-open run bounds, scheduling, and persistence contracts."""

from pathlib import Path

import cv2
import pytest

from splatoon3_ai_coach.analysis.basket_open_runs import (
    BasketOpenRunsArtifact,
    basket_open_runs_are_current,
    build_basket_open_runs,
    write_basket_open_runs,
)
from splatoon3_ai_coach.config.models import ClamBasketStateDetectorConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.vision.clam_basket_state import ClamBasketStateDetector
from splatoon3_ai_coach.vision.models import (
    AnalysisIdentity,
    ClamBasketStateReading,
    DetectorResult,
    GameEvent,
    GameStateSnapshot,
    MatchIntroReading,
    SourceFrameReference,
    VisionFrameResult,
    VisionManifest,
)


def _manifest(states: list[str], *, mode: str = "clam_blitz") -> VisionManifest:
    intro = VisionFrameResult(
        frame_id="intro",
        timestamp=0,
        source="cadence",
        basket_observation_scheduled=False,
        detections=[
            DetectorResult(
                id="intro-result",
                detector_name="match_intro",
                detector_version="test",
                confidence=1,
                reading=MatchIntroReading(
                    stage_id="hammerhead_bridge",
                    battle_mode_id=mode,
                    stage_template_score=0.99,
                    battle_mode_template_score=0.99,
                ),
            )
        ],
    )
    frames = [intro]
    snapshots = [
        GameStateSnapshot(
            timestamp=0, source_frame=SourceFrameReference(frame_id="intro", timestamp=0)
        )
    ]
    for index, state in enumerate(states, 1):
        frame_id = f"f{index}"
        scheduled = None if state == "legacy" else state != "unscheduled"
        observed = state in {"open", "closed", "low"}
        reading = "open" if state == "low" else state
        results = (
            [
                DetectorResult(
                    id=f"r{index}",
                    detector_name="clam_basket_state",
                    detector_version="test",
                    confidence=0.1 if state == "low" else 0.7,
                    reading=ClamBasketStateReading(visual_state=reading),
                )
            ]
            if observed or state == "unknown"
            else []
        )
        frames.append(
            VisionFrameResult(
                frame_id=frame_id,
                timestamp=index,
                source="cadence",
                basket_observation_scheduled=scheduled,
                detections=results,
            )
        )
        snapshots.append(
            GameStateSnapshot(
                timestamp=index,
                match_phase="in_match",
                basket_state=reading if observed else "unknown",
                basket_state_evidence_ids=[f"r{index}"] if observed else [],
                source_frame=SourceFrameReference(frame_id=frame_id, timestamp=index),
            )
        )
    return VisionManifest(
        analysis=AnalysisIdentity(
            analysis_id="a",
            package_version="test",
            vision_config_sha256="v",
            video_identity="video",
            detector_versions={"clam_basket_state": "test"},
            language="en",
        ),
        video_identity="video",
        frame_results=frames,
        state_snapshots=snapshots,
    )


def test_bounded_open_run_and_unscheduled_skip() -> None:
    artifact = build_basket_open_runs(
        _manifest(["closed", "open", "unscheduled", "open", "closed"])
    )
    assert artifact.availability == "available" and len(artifact.runs) == 1
    run = artifact.runs[0]
    assert (
        run.preceding_closed.video_time,
        run.first_observed_open.video_time,
        run.last_observed_open.video_time,
        run.following_closed.video_time,
    ) == (1, 2, 4, 5)
    assert run.first_observed_open.evidence_ids == ["r2"]
    assert run.last_observed_open.evidence_ids == ["r4"]
    assert run.left_censor_reason is None and run.right_censor_reason is None


@pytest.mark.parametrize("gap", ["unknown", "missing", "legacy", "low"])
def test_unknown_or_missing_expected_breaks_open_runs(gap: str) -> None:
    artifact = build_basket_open_runs(
        _manifest(["closed", "open", gap, "open", "closed"])
    )
    assert len(artifact.runs) == 2
    first, second = artifact.runs
    assert first.preceding_closed.video_time == 1 and first.following_closed is None
    assert (
        first.right_censor_reason == "unknown_break"
        and first.ending_break.frame_id == "f3"
    )
    assert (
        first.ending_break.reason
        == {
            "unknown": "unknown_observation",
            "missing": "missing_expected_observation",
            "legacy": "schedule_unknown",
            "low": "unknown_observation",
        }[gap]
    )
    assert (
        second.preceding_closed is None and second.left_censor_reason == "unknown_break"
    )
    assert second.following_closed.video_time == 5
    assert artifact.availability == "partial"


def test_censored_ends_and_match_end_exclusion() -> None:
    manifest = _manifest(["open", "open", "closed"])
    manifest.game_events = [
        GameEvent(start_time=3, event_type="match_end", reason="clock_zero", confidence=1)
    ]
    (run,) = build_basket_open_runs(manifest).runs
    assert run.left_censor_reason == "no_prior_closed"
    assert run.right_censor_reason == "match_end" and run.following_closed is None
    assert run.last_observed_open.video_time == 2
    manifest.game_events = []
    manifest.frame_results = manifest.frame_results[:-1]
    manifest.state_snapshots = manifest.state_snapshots[:-1]
    assert build_basket_open_runs(manifest).runs[0].right_censor_reason == "video_end"


def test_unavailable_provenance_mode_and_stale_artifact(tmp_path: Path) -> None:
    manifest = _manifest(["open"])
    assert (
        build_basket_open_runs(_manifest(["open"], mode="splat_zones")).availability
        == "unavailable"
    )
    no_detector = manifest.model_copy(deep=True)
    no_detector.analysis.detector_versions.clear()
    assert build_basket_open_runs(no_detector).limitations == [
        "basket_detector_not_enabled"
    ]
    missing_link = manifest.model_copy(deep=True)
    missing_link.state_snapshots[-1].source_frame = None
    assert build_basket_open_runs(missing_link).limitations == [
        "ambiguous_frame_snapshot_link"
    ]
    path = write_basket_open_runs(manifest, tmp_path)
    artifact = BasketOpenRunsArtifact.model_validate_json(path.read_text())
    assert basket_open_runs_are_current(artifact, manifest)
    manifest.frame_results[-1].basket_observation_scheduled = False
    assert not basket_open_runs_are_current(artifact, manifest)


def test_overlay_breaks_and_builder_does_not_mutate_manifest() -> None:
    manifest = _manifest(["closed", "open", "open", "open", "closed"])
    manifest.state_snapshots[3].map_overlay_present = True
    before = manifest.model_dump(mode="json")
    artifact = build_basket_open_runs(manifest)
    assert len(artifact.runs) == 2
    assert artifact.runs[0].ending_break.frame_id == "f3"
    assert artifact.runs[1].left_censor_reason == "unknown_break"
    assert manifest.model_dump(mode="json") == before


def test_duplicate_timestamps_are_unavailable() -> None:
    manifest = _manifest(["open", "closed"])
    manifest.frame_results[2].timestamp = 1
    assert build_basket_open_runs(manifest).limitations == [
        "ambiguous_frame_snapshot_link"
    ]


def test_missing_snapshot_breaks_and_no_scheduled_data_is_unavailable() -> None:
    manifest = _manifest(["closed", "open", "open", "open", "closed"])
    manifest.state_snapshots.pop(3)
    artifact = build_basket_open_runs(manifest)
    assert artifact.availability == "partial" and len(artifact.runs) == 2
    assert artifact.runs[0].ending_break.reason == "missing_snapshot"
    no_observations = _manifest(["unscheduled", "unscheduled"])
    result = build_basket_open_runs(no_observations)
    assert result.availability == "unavailable"
    assert result.limitations == ["no_basket_observations_scheduled"]


def test_legacy_positive_result_allows_run_but_absence_breaks() -> None:
    manifest = _manifest(["closed", "open", "legacy", "open", "closed"])
    for frame in manifest.frame_results:
        frame.basket_observation_scheduled = None
    artifact = build_basket_open_runs(manifest)
    assert artifact.availability == "partial"
    assert len(artifact.runs) == 2
    assert artifact.runs[0].preceding_closed.video_time == 1
    assert artifact.runs[0].ending_break.reason == "schedule_unknown"


def test_normal_censoring_does_not_make_artifact_partial() -> None:
    artifact = build_basket_open_runs(_manifest(["open", "open"]))
    assert artifact.availability == "available"
    assert artifact.limitations == []
    assert artifact.runs[0].left_censor_reason == "no_prior_closed"
    assert artifact.runs[0].right_censor_reason == "video_end"


def test_gaps_without_a_useful_run_are_unavailable() -> None:
    artifact = build_basket_open_runs(_manifest(["unknown", "closed"]))
    assert artifact.availability == "unavailable"
    assert artifact.limitations == ["observation_or_schedule_gaps_prevent_run_analysis"]


def test_contradictory_schedule_and_scheduled_missing_result_break_runs() -> None:
    contradictory = _manifest(["closed", "open", "open", "open", "closed"])
    contradictory.frame_results[3].basket_observation_scheduled = False
    artifact = build_basket_open_runs(contradictory)
    assert artifact.availability == "partial"
    assert artifact.runs[0].ending_break.reason == "schedule_unknown"

    missing = _manifest(["closed", "open", "missing", "open", "closed"])
    assert missing.frame_results[3].basket_observation_scheduled is True
    artifact = build_basket_open_runs(missing)
    assert artifact.availability == "partial"
    assert artifact.runs[0].ending_break.reason == "missing_expected_observation"


def test_mode_gate_ignores_stored_basket_results() -> None:
    artifact = build_basket_open_runs(
        _manifest(["closed", "open", "closed"], mode="splat_zones")
    )
    assert artifact.availability == "unavailable"
    assert artifact.runs == []


def test_colored_black_colored_presentations_are_one_run() -> None:
    detector = ClamBasketStateDetector(ClamBasketStateDetectorConfig(roi=(0, 0, 1, 1)))
    readings = []
    for timestamp in [85, 96, 80]:
        path = PROJECT_ROOT / "tests/fixtures/clam_basket_state" / f"{timestamp}.png"
        reading, _ = detector.detect(cv2.imread(str(path)))
        readings.append(reading.visual_state)
    assert readings == ["open", "open", "open"]
    artifact = build_basket_open_runs(_manifest(["closed", *readings, "closed"]))
    assert len(artifact.runs) == 1


def test_recorded_presentations_create_two_runs_across_map_overlay() -> None:
    """Independently reviewed crops cover closed/open/map/open/closed."""
    detector = ClamBasketStateDetector(ClamBasketStateDetectorConfig(roi=(0, 0, 1, 1)))
    expected = ["closed", "open", "unknown", "open", "closed"]
    timestamps = [75, 80, 81, 85, 97]
    observed = []
    for time in timestamps:
        path = PROJECT_ROOT / "tests/fixtures/clam_basket_state" / f"{time}.png"
        reading, _ = detector.detect(cv2.imread(str(path)))
        observed.append(reading.visual_state)
    assert observed == expected
    artifact = build_basket_open_runs(_manifest(observed))
    assert len(artifact.runs) == 2
    assert artifact.runs[0].right_censor_reason == "unknown_break"
    assert artifact.runs[1].left_censor_reason == "unknown_break"
