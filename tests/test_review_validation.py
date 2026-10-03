"""Quality-gate metric contracts for Review timeline artifacts."""

import hashlib
from pathlib import Path

from splatoon3_ai_coach.review.models import (
    CoverageReport,
    CursorObservation,
    CursorSlotObservation,
    PlayerDeathEpisodeEvidence,
    ReviewTimelineDataset,
    ReviewTimelineSample,
    SlotState,
)
from splatoon3_ai_coach.review.validation import (
    ExpectedDeathInterval,
    ValidationLabels,
    ValidationThresholds,
    check_provenance,
    render_report,
    validate_artifact,
)


def test_unknown_is_not_a_roster_error(tmp_path: Path) -> None:
    image = tmp_path / "sample.png"
    image.write_bytes(b"image")
    dataset = _dataset(image, SlotState.unknown)
    labels = ValidationLabels(
        source_artifact="artifact.json",
        source_manifest="manifest.yaml",
        samples=[
            {
                "sample_id": "sample-000000",
                "expected_acceptance": "accepted",
                "expected_clock_state": "readable",
                "expected_elapsed_seconds": -3,
                "expected_cursor_x": 100,
                "expected_slots": {"top_1": "unknown"},
            }
        ],
    )
    result = validate_artifact(
        dataset,
        labels,
        thresholds=ValidationThresholds(min_roster_accuracy=0),
    )
    assert result.metrics.roster_expected_decisions == 0
    assert result.metrics.unknown_decision_errors == 0


def test_clock_compares_normalized_signed_integer() -> None:
    dataset = _dataset(Path("/tmp/does-not-exist"), SlotState.alive)
    labels = ValidationLabels(
        source_artifact="artifact.json",
        source_manifest="manifest.yaml",
        samples=[
            {
                "sample_id": "sample-000000",
                "expected_acceptance": "accepted",
                "expected_clock_state": "readable",
                "expected_elapsed_seconds": -3,
            }
        ],
    )
    result = validate_artifact(
        dataset,
        labels,
        thresholds=ValidationThresholds(min_roster_accuracy=0),
    )
    assert result.metrics.readable_clock_accuracy == 1


def test_interval_iou_is_reported_without_exact_timestamp() -> None:
    dataset = _dataset(Path("/tmp/does-not-exist"), SlotState.alive)
    dataset.death_episodes = [
        PlayerDeathEpisodeEvidence(
            slot_id="top_1",
            last_alive_elapsed=10,
            first_dead_elapsed=11,
        )
    ]
    labels = ValidationLabels(
        source_artifact="artifact.json",
        source_manifest="manifest.yaml",
        samples=[],
        death_intervals=[
            ExpectedDeathInterval(
                slot_id="top_1",
                onset_start=10,
                onset_end=12,
            )
        ],
    )
    result = validate_artifact(dataset, labels)
    assert result.metrics.death_interval_iou == 2 / 3


def test_report_contains_thresholds_and_decision() -> None:
    dataset = _dataset(Path("/tmp/does-not-exist"), SlotState.alive)
    labels = ValidationLabels(
        source_artifact="artifact.json",
        source_manifest="manifest.yaml",
        samples=[],
    )
    result = validate_artifact(dataset, labels)
    report = render_report(result)
    assert "Configured thresholds" in report
    assert "Decision:" in report


def test_provenance_requires_manifest_source_locator(tmp_path: Path) -> None:
    image = tmp_path / "sample.png"
    image.write_bytes(b"image")
    dataset = _dataset(image, SlotState.unknown)
    dataset.samples[0].image_sha256 = hashlib.sha256(b"image").hexdigest()
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "video_samples:\n"
        "  - image_path: sample.png\n"
        "    source_video_time: 1.0\n"
        "    frame_index: 3\n",
        encoding="utf-8",
    )
    assert check_provenance(dataset, manifest) == 0
    manifest.write_text(
        "video_samples:\n"
        "  - image_path: sample.png\n",
        encoding="utf-8",
    )
    assert check_provenance(dataset, manifest) == 1


def _dataset(image: Path, state: SlotState) -> ReviewTimelineDataset:
    return ReviewTimelineDataset(
        samples=[
            ReviewTimelineSample(
                sample_id="sample-000000",
                capture_index=0,
                image_path=str(image),
                image_sha256="",
                elapsed_seconds=-3,
                displayed_clock="-0:03",
                clock_source="manifest",
                clock_status="confirmed",
                clock_confidence=1,
                cursor=CursorObservation(x=100, confidence=1),
                slots=[
                    CursorSlotObservation(
                        slot_id="top_1",
                        team_row="top",
                        state=state,
                        confidence=1,
                        sample_id="sample-000000",
                        elapsed_seconds=-3,
                    )
                ],
            )
        ],
        coverage=CoverageReport(sample_count=1, clocked_sample_count=1),
    )

