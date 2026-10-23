"""Build ``review_evidence.json`` from one Review import (``REVIEW_EVIDENCE_PACK.md``).

Review clock only: every fact stays on the remaining clock with the sample IDs
that support it. Nothing is interpolated, no ``GameEvent`` is emitted, and no
slot is the "user". Inclusion does not authorize downstream consumption.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from splatoon3_ai_coach.review.evidence_pack_models import (
    PACK_FILENAME,
    AcquisitionRecord,
    CoverageBlock,
    DeathMarkerBlock,
    DeathMarkerEntry,
    Limitation,
    ObservedValue,
    PackSource,
    ReviewEvidencePack,
    RowRecall,
    SampleRef,
    ScoreControlBlock,
    ScoreSecond,
    SlotStateBlock,
    SlotStateRun,
    SpecialUseBlock,
    SpecialUseEntry,
    TeamIdentityBlock,
)
from splatoon3_ai_coach.review.models import (
    EvidenceField,
    ReviewSecondState,
    ReviewTimelineDataset,
)
from splatoon3_ai_coach.review.special_fusion import (
    TIMELINE_FILENAME,
    derive_special_use_facts,
)

MANIFEST_FILENAME = "timeline_samples_manifest.yaml"
OBJECTIVE_MODES = frozenset({"splat_zones", "tower_control", "rainmaker", "clam_blitz"})
# Characterized production recall ranges from the read-only hue diagnosis:
# top 6–82, bottom 151–167 and the red cluster at 0–2. The 120–136 range is
# characterized as zero recall, not a range to extend from new captures.
TOP_PARTIAL_RECALL_HUES = ((6.0, 82.0),)
BOTTOM_PARTIAL_RECALL_HUES = ((0.0, 2.0), (151.0, 167.0))
BOTTOM_ROW_ZERO_RECALL_HUE = (120.0, 136.0)


class AcquisitionMismatch(ValueError):
    """The acquisition record names a different video than the import."""


def write_review_evidence_pack(
    import_dir: Path,
    *,
    acquisition: AcquisitionRecord | None = None,
    replace: bool = False,
) -> tuple[Path, ReviewEvidencePack]:
    """Read an import directory and write ``review_evidence.json`` beside it."""
    out = import_dir / PACK_FILENAME
    if out.exists() and not replace:
        raise FileExistsError(f"{out} exists; pass replace to rebuild it")
    raw = (import_dir / TIMELINE_FILENAME).read_bytes()
    manifest_path = import_dir / MANIFEST_FILENAME
    manifest_raw = manifest_path.read_bytes() if manifest_path.is_file() else None
    pack = build_review_evidence_pack(
        ReviewTimelineDataset.model_validate_json(raw),
        dataset_sha256=hashlib.sha256(raw).hexdigest(),
        manifest=_manifest(manifest_raw),
        manifest_sha256=None
        if manifest_raw is None
        else hashlib.sha256(manifest_raw).hexdigest(),
        acquisition=acquisition,
    )
    out.write_text(pack.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return out, pack


def build_review_evidence_pack(
    ds: ReviewTimelineDataset,
    *,
    dataset_sha256: str,
    manifest: dict[str, Any] | None = None,
    manifest_sha256: str | None = None,
    acquisition: AcquisitionRecord | None = None,
) -> ReviewEvidencePack:
    """Assemble the pack; raises ``AcquisitionMismatch`` on a video mismatch."""
    source = _source(ds, dataset_sha256, manifest, manifest_sha256)
    _check_acquisition(source, acquisition)
    limitations = []
    if acquisition is None:
        limitations.append(
            Limitation(
                code="no_acquisition_provenance",
                detail="built without a capture package; acquisition is unknown",
            )
        )
    return ReviewEvidencePack(
        source=source,
        acquisition=acquisition,
        limitations=limitations,
        axis=ds.axis_calibration,
        samples=[_sample_ref(sample) for sample in ds.samples],
        special_uses=special_use_block(ds, dataset_sha256),
        slot_states=slot_state_block(ds),
        death_markers=death_marker_block(ds),
        score_control=score_control_block(ds),
        coverage=coverage_block(ds),
        team_identity=team_identity_block(ds),
    )


def _manifest(raw: bytes | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    data = yaml.safe_load(raw) or {}
    return data if isinstance(data, dict) else {}


def _source(
    ds: ReviewTimelineDataset,
    dataset_sha256: str,
    manifest: dict[str, Any] | None,
    manifest_sha256: str | None,
) -> PackSource:
    video_id = None if manifest is None else manifest.get("source_video_id")
    return PackSource(
        dataset_file=TIMELINE_FILENAME,
        dataset_sha256=dataset_sha256,
        dataset_schema_version=ds.schema_version,
        manifest_file=None if manifest is None else MANIFEST_FILENAME,
        manifest_sha256=manifest_sha256,
        source_video_identity=str(video_id) if video_id else None,
        source_recording_id=ds.source_recording_id,
        battle_mode_id=ds.battle_mode_id,
        regulation_seconds=ds.regulation_seconds,
        detector_versions=dict(ds.detector_versions),
    )


def _check_acquisition(source: PackSource, acquisition: AcquisitionRecord | None) -> None:
    if acquisition is None:
        return
    if source.source_video_identity is None:
        raise AcquisitionMismatch(
            "acquisition given but the import has no manifest video id"
        )
    if source.source_video_identity != acquisition.artifact_video_identity:
        raise AcquisitionMismatch(
            "manifest source_video_id does not match the capture artifact identity"
        )


def _sample_ref(sample: Any) -> SampleRef:
    return SampleRef(
        sample_id=sample.sample_id,
        capture_index=sample.capture_index,
        image_name=Path(sample.image_path).name,
        image_sha256=sample.image_sha256,
        displayed_clock=sample.displayed_clock,
        remaining_second=sample.elapsed_seconds,
        clock_status=sample.clock_status,
        source_video_time=sample.source_video_time,
        frame_index=sample.frame_index,
        source_pts=sample.source_pts,
    )


def _side_limitations(ds: ReviewTimelineDataset) -> list[Limitation]:
    """Why side (ally / opponent) is unknown or not validated."""
    source = ds.team_identity.source
    if ds.team_identity.ally_row is None:
        return [Limitation(code="side_unknown", detail="no ally row; side is unknown")]
    if source == "gameplay_color":
        return [
            Limitation(
                code="side_from_unvalidated_calibration",
                detail="ally row from gameplay team-colour calibration, which can latch "
                "on scenery before the roster HUD (alignment v2 failure)",
            )
        ]
    if source == "gameplay_color_roster_gated":
        return []
    return [
        Limitation(
            code="side_operator_declared",
            detail="ally row declared by the operator (--ally-row)",
        )
    ]


def special_use_block(ds: ReviewTimelineDataset, dataset_sha256: str) -> SpecialUseBlock:
    """SPECIAL_USE facts with their supporting samples; refusals stay visible."""
    try:
        facts = derive_special_use_facts(ds, dataset_sha256)
    except ValueError as exc:
        return SpecialUseBlock(
            validation_status="unavailable",
            limitations=[Limitation(code="special_facts_refused", detail=str(exc))],
        )
    entries = []
    for fact in facts.facts:
        event = ds.special_events[fact.special_index]
        entries.append(
            SpecialUseEntry(
                fact_id=f"special_use:{fact.special_index:04d}",
                fact=fact,
                source_sample_ids=list(event.supporting_sample_ids),
                cursor_obscured_sample_ids=list(event.cursor_obscured_sample_ids),
            )
        )
    return SpecialUseBlock(
        validation_status="validated",
        validation_basis="SPECIAL_USE timing: gate D PASS, tag special-use-timing-frozen",
        limitations=_special_limitations(ds),
        seconds_per_px=facts.seconds_per_px,
        entries=entries,
    )


def _special_limitations(ds: ReviewTimelineDataset) -> list[Limitation]:
    return [
        Limitation(
            code="special_type_identity_partial",
            detail="only timing is validated; special_id is label-confirmed only "
            "for identity_status=label_confirmed_type",
        ),
        Limitation(
            code="slot_state_at_use_inherits_lamp_limits",
            detail="slot_state_at_use uses roster lamps, where a disconnected "
            "slot reads alive",
        ),
        *_side_limitations(ds),
    ]


def slot_state_block(ds: ReviewTimelineDataset) -> SlotStateBlock:
    """Per-slot runs of observed lamp states; seconds without a sample are gaps."""
    states = sorted(ds.second_states, key=lambda s: s.elapsed_second, reverse=True)
    gaps = [s.elapsed_second for s in states if not s.provenance]
    slot_ids = list(dict.fromkeys(slot.slot_id for s in states for slot in s.slots))
    runs = [run for slot_id in slot_ids for run in _runs_for_slot(slot_id, states)]
    return SlotStateBlock(
        validation_status="limited",
        validation_basis="roster X-marker detection: 184/184 on the GO gate labels; "
        "slot-state runs themselves have no gate",
        limitations=[
            Limitation(
                code="disconnected_slot_reads_alive",
                detail="a disconnected slot shows no X marker and reads alive; "
                "alive means no X marker observed, not connected and alive",
            ),
            Limitation(
                code="slot_state_runs_not_gated",
                detail="only X-marker detection is validated, not per-slot state runs",
            ),
        ],
        gap_seconds=gaps,
        runs=runs,
    )


def _runs_for_slot(slot_id: str, states: list[ReviewSecondState]) -> list[SlotStateRun]:
    """Group consecutive sampled seconds (descending remaining) with one state."""
    groups: list[list[tuple[ReviewSecondState, Any]]] = []
    previous: int | None = None
    for state in states:
        slot = next((s for s in state.slots if s.slot_id == slot_id), None)
        if slot is None or not state.provenance:
            previous = None
            continue
        second = state.elapsed_second
        if groups and previous == second + 1 and groups[-1][-1][1].state == slot.state:
            groups[-1].append((state, slot))
        else:
            groups.append([(state, slot)])
        previous = second
    return [_run(slot_id, index, group) for index, group in enumerate(groups)]


def _run(
    slot_id: str, index: int, group: list[tuple[ReviewSecondState, Any]]
) -> SlotStateRun:
    """A lamp read cites its own sample; an unreadable lamp cites the second's frames."""
    sources: set[str] = set()
    for state, slot in group:
        if slot.sample_id:
            sources.add(slot.sample_id)
        else:
            sources.update(p.sample_id for p in state.provenance)
    first, slot0 = group[0]
    return SlotStateRun(
        fact_id=f"slot_run:{slot_id}:{index:04d}",
        slot_id=slot_id,
        team_row=slot0.team_row,
        state=slot0.state,
        remaining_start=first.elapsed_second,
        remaining_end=group[-1][0].elapsed_second,
        second_count=len(group),
        min_confidence=min(slot.confidence for _, slot in group),
        source_sample_ids=sorted(sources),
    )


def death_marker_block(ds: ReviewTimelineDataset) -> DeathMarkerBlock:
    """Death glyphs; a missing glyph is never evidence that no death happened."""
    markers = [m for m in ds.match_history_events if m.kind == "death"]
    recall = [
        _row_recall(
            "top",
            ds.team_identity.top_color,
            markers,
            _diagnostic_lamp_onsets(ds, "top"),
        ),
        _row_recall(
            "bottom",
            ds.team_identity.bottom_color,
            markers,
            _diagnostic_lamp_onsets(ds, "bottom"),
        ),
    ]
    limitations = [
        Limitation(
            code="absence_not_evidence",
            detail="no glyph does not mean no death; observed glyph counts and "
            "lamp-onset comparisons are diagnostics only",
        )
    ]
    if ds.team_identity.bottom_color is not None and (
        BOTTOM_ROW_ZERO_RECALL_HUE[0]
        <= ds.team_identity.bottom_color
        <= BOTTOM_ROW_ZERO_RECALL_HUE[1]
    ):
        limitations.append(_zero_recall_limitation())
    for team_row, identity in (
        ("top", ds.team_identity.top_color),
        ("bottom", ds.team_identity.bottom_color),
    ):
        if identity is not None and not _is_characterized_hue(team_row, identity):
            limitations.append(_uncharacterized_hue_limitation(team_row, identity))
    if ds.team_identity.top_color is None or ds.team_identity.bottom_color is None:
        limitations.append(
            Limitation(
                code="hue_not_measured",
                detail="one or more team-row hues were not measured; recall is "
                "unknown for that row",
            )
        )
    return DeathMarkerBlock(
        validation_status="limited",
        validation_basis="death-glyph recall from previously characterized hue "
        "ranges only; new imports do not extend or retune those ranges",
        limitations=limitations,
        row_recall=recall,
        markers=[_marker(m) for m in markers],
    )


def _row_recall(
    team_row: str,
    hue: float | None,
    markers: list[Any],
    lamp_onsets: int | None,
) -> RowRecall:
    """Classify only against frozen characterization ranges."""
    observed = sum(marker.team_row == team_row for marker in markers)
    if hue is None:
        status = "unknown"
    elif (
        team_row == "bottom"
        and BOTTOM_ROW_ZERO_RECALL_HUE[0] <= hue <= BOTTOM_ROW_ZERO_RECALL_HUE[1]
    ):
        status = "known_zero_recall"
    elif _row_hue_is_characterized(team_row, hue):
        status = "partial"
    else:
        status = "unknown"
    return RowRecall(
        team_row=team_row,
        row_hue=hue,
        recall_status=status,
        observed_glyph_count=observed,
        diagnostic_lamp_onset_count=lamp_onsets,
    )


def _row_hue_is_characterized(team_row: str, hue: float) -> bool:
    ranges = TOP_PARTIAL_RECALL_HUES if team_row == "top" else BOTTOM_PARTIAL_RECALL_HUES
    return any(low <= hue <= high for low, high in ranges)


def _is_characterized_hue(team_row: str, hue: float) -> bool:
    return _row_hue_is_characterized(team_row, hue) or (
        team_row == "bottom"
        and BOTTOM_ROW_ZERO_RECALL_HUE[0] <= hue <= BOTTOM_ROW_ZERO_RECALL_HUE[1]
    )


def _uncharacterized_hue_limitation(team_row: str, hue: float) -> Limitation:
    return Limitation(
        code="hue_range_uncharacterized",
        detail=f"{team_row} row hue {hue:g} is outside all previously characterized "
        "ranges for that row; recall is unknown",
    )


def _diagnostic_lamp_onsets(ds: ReviewTimelineDataset, team_row: str) -> int | None:
    """Count alive-to-dead lamp transitions, never classification evidence."""
    previous: dict[str, str] = {}
    onsets = 0
    for state in sorted(
        ds.second_states, key=lambda item: item.elapsed_second, reverse=True
    ):
        for slot in state.slots:
            if slot.team_row != team_row or slot.state.value == "unknown":
                continue
            if previous.get(slot.slot_id) == "alive" and slot.state.value == "dead":
                onsets += 1
            previous[slot.slot_id] = slot.state.value
    return onsets


def _zero_recall_limitation() -> Limitation:
    low, high = BOTTOM_ROW_ZERO_RECALL_HUE
    return Limitation(
        code="bottom_row_hue_zero_recall",
        detail=f"bottom row hue in {low:g}-{high:g}: 0 of 139 expected death glyphs "
        "were found (grayscale collapse); bottom-row deaths are unobservable here",
    )


def _marker(marker: Any) -> DeathMarkerEntry:
    return DeathMarkerEntry(
        fact_id=f"death_marker:{marker.marker_id}",
        marker_id=marker.marker_id,
        team_row=marker.team_row,
        player_slot_id=marker.player_slot_id,
        remaining_estimate=marker.elapsed_estimate,
        remaining_min=marker.event_elapsed_min,
        remaining_max=marker.event_elapsed_max,
        confidence=marker.confidence,
        source_sample_ids=list(marker.supporting_sample_ids),
        cursor_obscured_sample_ids=list(marker.cursor_obscured_sample_ids),
    )


def score_control_block(ds: ReviewTimelineDataset) -> ScoreControlBlock:
    """Objective counts, penalties and control band; only for objective modes."""
    mode = ds.battle_mode_id
    if mode is None:
        return ScoreControlBlock(
            validation_status="unavailable",
            limitations=[
                Limitation(code="battle_mode_unknown", detail="battle mode not supplied")
            ],
        )
    if mode not in OBJECTIVE_MODES:
        return ScoreControlBlock(
            validation_status="not_applicable",
            battle_mode_id=mode,
            limitations=[
                Limitation(
                    code="battle_mode_not_objective",
                    detail=f"{mode} has no objective counts or control band",
                )
            ],
        )
    seconds = [s for s in (_score_second(state) for state in ds.second_states) if s]
    return ScoreControlBlock(
        validation_status="experimental",
        battle_mode_id=mode,
        limitations=_score_limitations(),
        seconds=seconds,
    )


def _score_limitations() -> list[Limitation]:
    return [
        Limitation(
            code="score_reader_not_gated",
            detail="remaining counts, penalties and control band have no validation gate",
        ),
        Limitation(
            code="controller_without_sample_provenance",
            detail="controller comes from static control segments that record no "
            "source sample IDs",
        ),
    ]


def _score_second(state: ReviewSecondState) -> ScoreSecond | None:
    names = ("top_remaining", "bottom_remaining", "top_penalty", "bottom_penalty")
    values = {name: _observed(getattr(state, name)) for name in names}
    values["controller"] = _observed(state.controller)
    if not state.provenance or all(value is None for value in values.values()):
        return None
    return ScoreSecond(
        fact_id=f"score:{state.elapsed_second}",
        remaining_second=state.elapsed_second,
        **values,
    )


def _observed(field: EvidenceField) -> ObservedValue | None:
    if field.value is None:
        return None
    sources = list(field.source_sample_ids)
    if not sources and field.source_sample_id:
        sources = [field.source_sample_id]
    return ObservedValue(
        value=field.value, confidence=field.confidence, source_sample_ids=sources
    )


def coverage_block(ds: ReviewTimelineDataset) -> CoverageBlock:
    """Observed and missing remaining seconds, copied from the import."""
    cov = ds.coverage
    limitations = [
        Limitation(
            code="non_monotonic_check_unverified",
            detail="non_monotonic_capture_indexes semantics appear inverted; "
            "not used as evidence",
        )
    ]
    if not cov.complete:
        limitations.append(
            Limitation(
                code="coverage_incomplete",
                detail=f"{len(cov.missing_elapsed_seconds)} remaining seconds "
                "have no sample",
            )
        )
    if ds.axis_calibration is None:
        limitations.append(
            Limitation(code="axis_unavailable", detail="timeline axis fit rejected")
        )
    return CoverageBlock(
        validation_status="limited",
        limitations=limitations,
        first_remaining=cov.first_elapsed_seconds,
        last_remaining=cov.last_elapsed_seconds,
        expected_second_count=len(cov.expected_seconds),
        observed_second_count=len(cov.observed_seconds),
        complete=cov.complete,
        missing_seconds=list(cov.missing_elapsed_seconds),
        duplicate_seconds=list(cov.duplicate_elapsed_seconds),
        disagreement_seconds=list(cov.disagreement_seconds),
        unreadable_sample_count=len(cov.unreadable_sample_ids),
        non_monotonic_capture_indexes=list(cov.non_monotonic_capture_indexes),
    )


def team_identity_block(ds: ReviewTimelineDataset) -> TeamIdentityBlock:
    """Row hues and ally row with the source of the side assignment."""
    identity = ds.team_identity
    return TeamIdentityBlock(
        validation_status="unavailable" if identity.ally_row is None else "experimental",
        limitations=_side_limitations(ds),
        top_hue=identity.top_color,
        bottom_hue=identity.bottom_color,
        ally_row=identity.ally_row,
        side_source=identity.source,
        side_provenance=identity.side_provenance,
        side_evidence_ids=list(identity.side_evidence_ids),
        side_evidence_times=list(identity.side_evidence_times),
        side_provenance_reason=identity.side_provenance_reason,
    )
