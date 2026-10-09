"""Quality-gate metric contracts for Review timeline artifacts."""

import hashlib
from pathlib import Path

from splatoon3_ai_coach.review.models import (
    ControlSegment,
    CoverageReport,
    CursorObservation,
    CursorSlotObservation,
    PlayerDeathEpisodeEvidence,
    ReviewTimelineDataset,
    ReviewTimelineSample,
    SlotState,
    SpecialUseEvent,
    TeamIdentity,
    TimelineEventMarker,
)
from splatoon3_ai_coach.review.validation import (
    ExpectedControlSegment,
    ExpectedDeathInterval,
    ExpectedTimelineEvent,
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


def test_extended_timeline_metrics_are_reported() -> None:
    dataset = _dataset(Path("/tmp/does-not-exist"), SlotState.alive)
    dataset.team_identity = TeamIdentity(ally_row="top", source="cli")
    dataset.control_segments = [
        ControlSegment(
            start_elapsed=10,
            end_elapsed=12,
            controller="top",
            confidence=1,
        )
    ]
    dataset.special_events = [
        SpecialUseEvent(
            slot_id="top_1",
            team_row="top",
            special_id="tacticooler",
            elapsed_estimate=11.5,
            x_norm=0.5,
            center_x=960,
            score=0.9,
            runner_up_margin=0.2,
        )
    ]
    dataset.match_history_events = [
        TimelineEventMarker(
            marker_id="death",
            team_row="bottom",
            kind="death",
            x_norm=0.6,
            elapsed_estimate=13,
            player_slot_id="bottom_4",
        )
    ]
    labels = ValidationLabels(
        source_artifact="artifact.json",
        source_manifest="manifest.yaml",
        samples=[],
        control_segments=[
            ExpectedControlSegment(
                start_elapsed=10,
                end_elapsed=12,
                controller="top",
            )
        ],
        specials=[
            ExpectedTimelineEvent(
                elapsed=12,
                slot="top_1",
                special_id="tacticooler",
            )
        ],
        deaths=[ExpectedTimelineEvent(elapsed=13, slot="bottom_4")],
        ally_row="top",
    )
    metrics = validate_artifact(dataset, labels).metrics
    assert metrics.control_second_agreement == 1
    assert metrics.special_type_accuracy == 1
    assert metrics.special_timestamp_mae == 0.5
    assert metrics.death_slot_accuracy == 1
    assert metrics.ally_row_accuracy == 1


def _special(slot: str, special_id: str, elapsed: float) -> SpecialUseEvent:
    return SpecialUseEvent(
        slot_id=slot,
        team_row="top" if slot.startswith("top") else "bottom",
        special_id=special_id,
        elapsed_estimate=elapsed,
        x_norm=0.5,
        center_x=960,
        score=0.9,
        runner_up_margin=0.2,
    )


def _special_labels(*events: tuple[str, str, float]) -> ValidationLabels:
    return ValidationLabels(
        source_artifact="artifact.json",
        source_manifest="manifest.yaml",
        samples=[],
        specials=[
            ExpectedTimelineEvent(elapsed=t, slot=slot, special_id=sid)
            for slot, sid, t in events
        ],
    )


def test_simultaneous_events_pair_by_slot_first() -> None:
    dataset = _dataset(Path("/tmp/does-not-exist"), SlotState.alive)
    dataset.match_history_events = [
        TimelineEventMarker(
            marker_id=f"death-{slot}",
            team_row="top",
            kind="death",
            x_norm=0.5,
            elapsed_estimate=t,
            player_slot_id=slot,
        )
        for slot, t in (("top_4", 50.1), ("top_1", 49.7))
    ]
    labels = ValidationLabels(
        source_artifact="artifact.json",
        source_manifest="manifest.yaml",
        samples=[],
        deaths=[
            ExpectedTimelineEvent(elapsed=50.0, slot="top_1"),
            ExpectedTimelineEvent(elapsed=50.0, slot="top_4"),
        ],
    )
    assert validate_artifact(dataset, labels).metrics.death_slot_accuracy == 1


def test_unknown_special_is_an_abstention_not_a_false_claim() -> None:
    dataset = _dataset(Path("/tmp/does-not-exist"), SlotState.alive)
    dataset.special_events = [
        _special("bottom_3", "unknown", 248.0),
        _special("top_3", "unknown", 77.7),
    ]
    labels = _special_labels(("bottom_3", "big_bubbler", 248.0))
    metrics = validate_artifact(dataset, labels).metrics
    assert metrics.special_false_claims == 0
    assert metrics.special_candidate_recall == 1
    assert metrics.special_candidate_precision == 0.5
    assert metrics.special_type_accuracy == 0


def test_special_checks_are_unlabeled_without_special_labels() -> None:
    dataset = _dataset(Path("/tmp/does-not-exist"), SlotState.alive)
    dataset.special_events = [_special("top_1", "trizooka", 30.0)]
    result = validate_artifact(dataset, _special_labels())
    assert result.metrics.special_candidate_precision is None
    assert result.metrics.special_false_claims is None
    assert all(v for k, v in result.checks.items() if k.startswith("special"))


def test_typed_unpaired_or_wrong_special_is_a_false_claim() -> None:
    dataset = _dataset(Path("/tmp/does-not-exist"), SlotState.alive)
    dataset.special_events = [
        _special("top_1", "kraken_royale", 30.0),
        _special("top_2", "ink_storm", 90.0),
    ]
    labels = _special_labels(("top_2", "inkjet", 90.0))
    result = validate_artifact(dataset, labels)
    assert result.metrics.special_false_claims == 2
    assert result.checks["special_false_claims"] is False


def _passing_special_dataset(
    count: int,
) -> tuple[ReviewTimelineDataset, ValidationLabels]:
    dataset = _dataset(Path("/tmp/does-not-exist"), SlotState.alive)
    events = [("top_1", "trizooka", 10.0 * index) for index in range(1, count + 1)]
    dataset.special_events = [_special(*event) for event in events]
    return dataset, _special_labels(*events)


def _special_checks(dataset: ReviewTimelineDataset, labels: ValidationLabels) -> dict:
    checks = validate_artifact(dataset, labels).checks
    return {key: value for key, value in checks.items() if key.startswith("special")}


def test_default_special_gate_allows_one_abstention_in_twelve() -> None:
    dataset, labels = _passing_special_dataset(12)
    dataset.special_events[0] = _special("top_1", "unknown", 10.0)
    assert all(_special_checks(dataset, labels).values())


def test_default_special_gate_rejects_two_abstentions_in_twelve() -> None:
    dataset, labels = _passing_special_dataset(12)
    for index in (0, 1):
        event = dataset.special_events[index]
        dataset.special_events[index] = _special(
            "top_1", "unknown", event.elapsed_estimate
        )
    checks = _special_checks(dataset, labels)
    assert checks["special_types"] is False


def test_default_special_gate_rejects_any_slot_error() -> None:
    dataset, labels = _passing_special_dataset(12)
    dataset.special_events[0] = _special("top_2", "trizooka", 10.0)
    checks = _special_checks(dataset, labels)
    assert checks["special_slots"] is False
    assert checks["special_false_claims"] is False


def test_default_special_gate_rejects_timestamp_drift() -> None:
    dataset, labels = _passing_special_dataset(12)
    dataset.special_events = [
        _special(e.slot_id, e.special_id, e.elapsed_estimate + 0.8)
        for e in dataset.special_events
    ]
    assert _special_checks(dataset, labels)["special_timestamp_mae"] is False


def test_label_on_a_different_frame_is_an_alignment_failure() -> None:
    dataset = _dataset(Path("/tmp/does-not-exist"), SlotState.alive)
    dataset.samples[0].frame_index = 120
    base = {
        "sample_id": "sample-000000",
        "expected_acceptance": "accepted",
        "expected_clock_state": "readable",
        "expected_elapsed_seconds": -3,
    }
    for frame_index, failures in ((120, 0), (119, 1), (None, 0)):
        labels = ValidationLabels(
            source_artifact="artifact.json",
            source_manifest="manifest.yaml",
            samples=[{**base, "frame_index": frame_index}],
        )
        result = validate_artifact(dataset, labels)
        assert result.metrics.label_alignment_failures == failures
        assert result.checks["label_alignment"] is (failures == 0)


def test_implausible_clock_sample_is_not_accepted() -> None:
    dataset = _dataset(Path("/tmp/does-not-exist"), SlotState.alive)
    dataset.samples[0].clock_status = "implausible"
    dataset.samples[0].elapsed_seconds = None
    for acceptance, false_acc, false_rej in (("rejected", 0, 0), ("accepted", 0, 1)):
        labels = ValidationLabels(
            source_artifact="artifact.json",
            source_manifest="manifest.yaml",
            samples=[
                {
                    "sample_id": "sample-000000",
                    "expected_acceptance": acceptance,
                    "expected_clock_state": "not_applicable",
                }
            ],
        )
        metrics = validate_artifact(dataset, labels).metrics
        assert (metrics.false_acceptances, metrics.false_rejections) == (
            false_acc,
            false_rej,
        )


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
        "video_samples:\n  - image_path: sample.png\n",
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
