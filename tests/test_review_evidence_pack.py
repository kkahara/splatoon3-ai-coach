"""Review evidence pack semantics (contract: review/REVIEW_EVIDENCE_PACK.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from splatoon3_ai_coach.review.evidence_pack import (
    AcquisitionMismatch,
    build_review_evidence_pack,
    write_review_evidence_pack,
)
from splatoon3_ai_coach.review.evidence_pack_models import (
    PACK_FILENAME,
    AcquisitionRecord,
)
from splatoon3_ai_coach.review.models import (
    AxisCalibrationEvidence,
    CoverageReport,
    CursorObservation,
    CursorSlotObservation,
    EvidenceField,
    FrameProvenance,
    ReviewSecondState,
    ReviewTimelineDataset,
    ReviewTimelineSample,
    SlotState,
    SpecialUseEvent,
    TeamIdentity,
    TimelineEventMarker,
)

AXIS = AxisCalibrationEvidence(
    slope=-1920.0,
    intercept=300.0,
    point_count=2,
    mean_absolute_error_seconds=0,
    max_absolute_error_seconds=0,
)
A, D, U = SlotState.alive, SlotState.dead, SlotState.unknown


def _sample(second: int, state: SlotState = A) -> ReviewTimelineSample:
    return ReviewTimelineSample(
        sample_id=f"s{second}",
        capture_index=300 - second,
        image_path=f"/somewhere/frame_{second}.jpg",
        image_sha256="0" * 64,
        elapsed_seconds=second,
        displayed_clock=f"{second // 60}:{second % 60:02d}",
        clock_status="ok",
        cursor=CursorObservation(x=float(300 - second), x_norm=(300 - second) / 1920),
        slots=[
            CursorSlotObservation(slot_id="top_1", state=state, sample_id=f"s{second}")
        ],
        source_video_time=float(300 - second) / 30,
        frame_index=300 - second,
        source_pts=1000 * (300 - second),
    )


def _state(
    second: int, lamp: SlotState | None, **fields: EvidenceField
) -> ReviewSecondState:
    """``lamp=None`` is a second with no sample at all (a gap)."""
    if lamp is None:
        slot = CursorSlotObservation(slot_id="top_1", team_row="top", sample_id="")
        return ReviewSecondState(
            elapsed_second=second, slots=[slot], warnings=["no sample"]
        )
    sample_id = "" if lamp is U else f"s{second}"
    slot = CursorSlotObservation(
        slot_id="top_1", team_row="top", state=lamp, confidence=0.9, sample_id=sample_id
    )
    return ReviewSecondState(
        elapsed_second=second,
        slots=[slot],
        provenance=[FrameProvenance(sample_id=f"s{second}", frame_index=300 - second)],
        **fields,
    )


def _dataset(
    lamps: dict[int, SlotState | None],
    *,
    mode: str | None = "splat_zones",
    top_hue: float | None = 10.0,
    bottom_hue: float | None = 100.0,
    ally_row: str | None = None,
    specials: list[SpecialUseEvent] = (),
    markers: list[TimelineEventMarker] = (),
    fields: dict[int, dict[str, EvidenceField]] | None = None,
) -> ReviewTimelineDataset:
    fields = fields or {}
    return ReviewTimelineDataset(
        source_recording_id="pkg",
        battle_mode_id=mode,
        samples=[_sample(s, lamp) for s, lamp in lamps.items() if lamp is not None],
        second_states=[_state(s, lamp, **fields.get(s, {})) for s, lamp in lamps.items()],
        special_events=list(specials),
        match_history_events=list(markers),
        axis_calibration=AXIS,
        team_identity=TeamIdentity(
            top_color=top_hue,
            bottom_color=bottom_hue,
            ally_row=ally_row,
            source="unknown" if ally_row is None else "cli",
        ),
        coverage=CoverageReport(
            sample_count=len(lamps),
            clocked_sample_count=len(lamps),
            missing_elapsed_seconds=[s for s, lamp in lamps.items() if lamp is None],
        ),
    )


def _pack(ds: ReviewTimelineDataset, **kwargs: object):
    return build_review_evidence_pack(ds, dataset_sha256="d" * 64, **kwargs)


def _codes(block) -> set[str]:
    return {item.code for item in block.limitations}


def test_pack_is_review_clock_and_never_authorizes_consumption() -> None:
    pack = _pack(_dataset({200: A}))
    assert pack.clock == "review_remaining"
    assert pack.consumption == "not_authorized_by_inclusion"
    assert pack.acquisition is None
    assert _codes(pack) == {"no_acquisition_provenance"}


def test_slot_runs_break_at_gaps_and_state_changes() -> None:
    lamps = {200: A, 199: A, 198: None, 197: A, 196: D, 195: D, 194: U}
    runs = _pack(_dataset(lamps)).slot_states.runs
    assert [(r.state, r.remaining_start, r.remaining_end) for r in runs] == [
        (A, 200, 199),
        (A, 197, 197),
        (D, 196, 195),
        (U, 194, 194),
    ]
    assert runs[0].source_sample_ids == ["s199", "s200"]
    assert runs[3].source_sample_ids == ["s194"]  # the unreadable second's frame


def test_slot_block_states_the_disconnected_alive_limitation() -> None:
    block = _pack(_dataset({200: A})).slot_states
    assert block.validation_status == "limited"
    assert block.validated_scope == "x_marker_detection"
    assert block.state_semantics["alive"] == "no_x_marker_observed"
    assert block.connection_state == "not_observed"
    assert {"disconnected_slot_reads_alive", "slot_state_runs_not_gated"} <= _codes(block)


def test_gap_seconds_are_listed_not_filled() -> None:
    block = _pack(_dataset({200: A, 199: None, 198: A})).slot_states
    assert block.gap_seconds == [199]
    assert all(199 not in (r.remaining_start, r.remaining_end) for r in block.runs)


@pytest.mark.parametrize(
    ("hue", "status", "code"),
    [
        (128.0, "known_zero_recall", "bottom_row_hue_zero_recall"),
        (120.0, "known_zero_recall", "bottom_row_hue_zero_recall"),
        (151.0, "partial", None),
        (100.0, "unknown", "hue_range_uncharacterized"),
        (None, "unknown", "hue_not_measured"),
    ],
)
def test_bottom_row_recall_status(
    hue: float | None, status: str, code: str | None
) -> None:
    block = _pack(_dataset({200: A}, bottom_hue=hue)).death_markers
    bottom = next(r for r in block.row_recall if r.team_row == "bottom")
    assert bottom.recall_status == status
    assert bottom.absence_is_evidence is False
    assert "absence_not_evidence" in _codes(block)
    if code:
        assert code in _codes(block)


def test_top_row_blue_hue_is_not_classified_from_bottom_row_diagnosis() -> None:
    block = _pack(_dataset({200: A}, top_hue=136.0, bottom_hue=100.0)).death_markers
    top = next(r for r in block.row_recall if r.team_row == "top")
    assert top.recall_status == "unknown"
    assert top.observed_glyph_count == 0
    assert top.diagnostic_lamp_onset_count == 0


def test_death_markers_keep_supporting_samples() -> None:
    marker = TimelineEventMarker(
        marker_id="m1",
        team_row="top",
        kind="death",
        x_norm=0.05,
        elapsed_estimate=204.0,
        event_elapsed_min=203,
        event_elapsed_max=205,
        confidence=0.8,
        supporting_sample_ids=["s200"],
        player_slot_id="top_1",
    )
    special_marker = marker.model_copy(update={"marker_id": "m2", "kind": "special"})
    markers = _pack(
        _dataset({200: A}, markers=[marker, special_marker])
    ).death_markers.markers
    assert [m.fact_id for m in markers] == ["death_marker:m1"]
    assert markers[0].source_sample_ids == ["s200"]
    assert markers[0].remaining_estimate == 204.0


@pytest.mark.parametrize(
    ("mode", "status", "code"),
    [
        (None, "unavailable", "battle_mode_unknown"),
        ("turf_war", "not_applicable", "battle_mode_not_objective"),
    ],
)
def test_score_control_is_mode_gated(mode: str | None, status: str, code: str) -> None:
    block = _pack(_dataset({200: A}, mode=mode)).score_control
    assert block.validation_status == status
    assert block.seconds == []
    assert _codes(block) == {code}


def test_score_control_keeps_only_observed_values() -> None:
    fields = {
        200: {
            "top_remaining": EvidenceField(
                value=80, confidence=0.9, source_sample_id="s200"
            ),
            "controller": EvidenceField(value="top", confidence=0.7),
        },
        199: {"top_remaining": EvidenceField(reason="unreadable")},
    }
    block = _pack(_dataset({200: A, 199: A}, fields=fields)).score_control
    assert block.validation_status == "experimental"
    assert [s.remaining_second for s in block.seconds] == [200]
    second = block.seconds[0]
    assert second.top_remaining.value == 80
    assert second.top_remaining.source_sample_ids == ["s200"]
    assert second.controller.source_sample_ids == []
    assert second.bottom_remaining is None
    assert "controller_without_sample_provenance" in _codes(block)


def _special(x: float) -> SpecialUseEvent:
    return SpecialUseEvent(
        slot_id="top_1",
        team_row="top",
        special_id="ink_storm",
        elapsed_estimate=300.0 - x,
        x_norm=x / 1920,
        center_x=x,
        score=0.9,
        runner_up_margin=0.5,
        supporting_sample_ids=["s200"],
        cursor_obscured_sample_ids=["s199"],
    )


def test_special_uses_cite_supporting_samples_and_side_limits() -> None:
    lamps = {s: A for s in range(205, 195, -1)}
    block = _pack(_dataset(lamps, specials=[_special(100.0)])).special_uses
    assert block.validation_status == "validated"
    entry = block.entries[0]
    assert entry.fact_id == "special_use:0000"
    assert entry.fact.side == "unknown"
    assert entry.source_sample_ids == ["s200"]
    assert entry.cursor_obscured_sample_ids == ["s199"]
    assert {"side_unknown", "special_type_identity_partial"} <= _codes(block)


def test_refused_special_facts_stay_visible() -> None:
    block = _pack(_dataset({200: A}, specials=[_special(400.0)])).special_uses
    assert block.validation_status == "unavailable"
    assert _codes(block) == {"special_facts_refused"}
    assert block.entries == []


def test_declared_side_is_labelled_operator_declared() -> None:
    pack = _pack(_dataset({200: A}, ally_row="top"))
    assert pack.team_identity.side_source == "cli"
    assert _codes(pack.team_identity) == {"side_operator_declared"}


def _acquisition(video_id: str) -> AcquisitionRecord:
    return AcquisitionRecord(
        package_id="pkg",
        source_type="review_code",
        acquisition_method="manual",
        language="en",
        artifact_id="timeline",
        artifact_path="artifacts/timeline.mov",
        artifact_video_identity=video_id,
        artifact_hash_verified=True,
        import_dir_in_package="derived/review_timeline_import",
        gate_contract="timeline-capture-v1",
        gate_contract_status_at_run="frozen",
        gate_automatic_verdict="incomplete",
        gate_verdict="pass",
        gate_checked_at="2026-01-01T00:00:00+00:00",
        gate_result_sha256="r" * 64,
    )


def test_acquisition_must_match_the_imported_video() -> None:
    ds = _dataset({200: A})
    manifest = {"source_video_id": "v" * 64}
    pack = _pack(ds, manifest=manifest, acquisition=_acquisition("v" * 64))
    assert pack.acquisition.package_id == "pkg"
    assert pack.source.source_video_identity == "v" * 64
    assert pack.limitations == []
    with pytest.raises(AcquisitionMismatch):
        _pack(ds, manifest=manifest, acquisition=_acquisition("w" * 64))
    with pytest.raises(AcquisitionMismatch):
        _pack(ds, acquisition=_acquisition("v" * 64))


def test_sample_refs_trace_to_frames_without_paths() -> None:
    sample = _pack(_dataset({200: A})).samples[0]
    assert sample.image_name == "frame_200.jpg"
    assert (sample.frame_index, sample.source_pts) == (100, 100000)


def _write_import(tmp_path: Path, ds: ReviewTimelineDataset) -> Path:
    (tmp_path / "review_timeline.json").write_text(ds.model_dump_json(), encoding="utf-8")
    (tmp_path / "timeline_samples_manifest.yaml").write_text(
        yaml.safe_dump({"source_video_id": "v" * 64}), encoding="utf-8"
    )
    return tmp_path


def test_write_refuses_to_overwrite_and_has_no_game_events(tmp_path: Path) -> None:
    import_dir = _write_import(tmp_path, _dataset({200: A, 199: D}))
    out, pack = write_review_evidence_pack(import_dir)
    assert out == import_dir / PACK_FILENAME
    assert pack.source.manifest_sha256 is not None
    with pytest.raises(FileExistsError):
        write_review_evidence_pack(import_dir)
    write_review_evidence_pack(import_dir, replace=True)
    text = out.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["source"]["source_video_identity"] == "v" * 64
    for forbidden in (
        "GameEvent",
        "SPECIAL_USED",
        "SPECIAL_READY",
        '"user"',
        "local_slot",
    ):
        assert forbidden not in text
