"""Synthetic tests for operator-only Arm R declarations and Step 4 records."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from map_ink_reference.errors import MalformedInput, PinMismatch, SealViolation
from map_ink_reference.f11.eligibility import (
    BLIND_INPUT,
    ELIGIBILITY_RECORD,
    ELIGIBILITY_SCHEMA,
    INDEPENDENCE,
    PAIR009_MANIFEST_SHA256,
    SCOPE_ARM_R,
    SOURCE_SCHEMA,
    load_prohibited_sources,
    produce_step4,
    require_declaration,
    require_hash_match,
    require_permitted_source,
    validate_step4_records,
)
from map_ink_reference.f11.guard import tooling_commit
from map_ink_reference.io_utils import sha256_file
from map_ink_reference.pins import STAGE_GEOMETRY_PINS
from map_ink_reference.seal import head_commit

from splatoon3_ai_coach.media.video_identity import video_identity


def _entry() -> dict[str, str]:
    return {
        "analysis_dir": "analysis/example",
        "video_path": "/declared/example.mov",
        "video_sha256": "abc",
        "video_identity": "identity",
        "vision_manifest_sha256": "manifest",
        "language": "ja",
        "acquisition_session": "session-1",
        "authorization": (
            "development-independent media for Amendment 11 Arm R; "
            "not corpus, pilot or held-out media"
        ),
        "development_independence": INDEPENDENCE,
        "declared_by": "operator",
    }


def test_a_complete_declaration_is_accepted() -> None:
    require_declaration(_entry())


def test_independence_is_not_inferred() -> None:
    entry = _entry()
    entry["development_independence"] = ""
    with pytest.raises(MalformedInput, match="missing"):
        require_declaration(entry)


def test_unavailable_or_mismatched_media_is_ineligible() -> None:
    with pytest.raises(MalformedInput, match="hash"):
        require_hash_match(
            expected_sha256="abc",
            actual_sha256="def",
            expected_identity="identity",
            actual_identity="identity",
            manifest_identity="identity",
            expected_manifest_sha256="manifest",
            actual_manifest_sha256="manifest",
        )


def test_prohibited_directories_are_rejected() -> None:
    entry = _entry()
    entry["analysis_dir"] = "analysis/_private"
    with pytest.raises(MalformedInput, match="not an Arm R"):
        require_declaration(entry)


def _repo() -> Path:
    return Path(__file__).resolve().parents[3]


def _outputs(tmp: Path) -> tuple[Path, Path]:
    return tmp / "blind_input_arm_r.json", tmp / "eligibility.json"


def _write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _frame(
    frame_id: str, timestamp: float, *, present: bool, pts: int | None, tb_num: Any
) -> dict:
    frame: dict[str, Any] = {
        "frame_id": frame_id,
        "timestamp": timestamp,
        "source_time_base_num": tb_num,
        "source_time_base_den": 1,
        "detections": [
            {"detector_name": "map_overlay", "reading": {"present": present}},
            {"detector_name": "player_count", "reading": {"roster_visible": not present}},
        ],
    }
    if pts is not None:
        frame["source_pts"] = pts
    return frame


def _manifest(
    identity: str,
    frames: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    language: str,
    *,
    include_video: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "video_identity": identity,
        "frame_results": frames,
        "state_snapshots": snapshots,
        "analysis": {"language": language},
        "game_events": [],
    }
    if include_video:
        payload["video"] = {"analysis_width": 16, "analysis_height": 16}
    return payload


def _cadence(
    stage: str | None,
    intervals: int,
    *,
    ineligible_pts: bool,
    eligible_pts: bool,
    tb_num: Any,
    duplicate: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    frames = [
        _frame("intro", 0.5, present=False, pts=0 if ineligible_pts else None, tb_num=1)
    ]
    if stage is not None:
        frames[0]["detections"].append(_intro_reading(stage))
    snapshots: list[dict[str, Any]] = []
    for index in range(intervals):
        timestamp = 2.0 + index * 2.0
        frames.append(
            _frame(
                f"e{index}",
                timestamp,
                present=True,
                pts=int(timestamp) if eligible_pts else None,
                tb_num=tb_num,
            )
        )
        snapshots.append({"timestamp": timestamp, "match_phase": "in_match"})
    if duplicate:
        frames.append(dict(frames[-1]))
    return frames, snapshots


def _intro_reading(stage: str) -> dict[str, Any]:
    return {
        "detector_name": "match_intro",
        "reading": {
            "kind": "match_intro",
            "stage_id": stage,
            "battle_mode_id": None,
            "stage_template_score": 0.9,
            "battle_mode_template_score": 0.0,
        },
    }


def _recording(
    root: Path,
    name: str,
    *,
    stage: str | None,
    intervals: int,
    ineligible_pts: bool = True,
    eligible_pts: bool = True,
    tb_num: Any = 1,
    language: str = "en",
    manifest_language: str | None = None,
    duplicate: bool = False,
    include_video: bool = True,
    drop_last_pts: bool = False,
) -> dict[str, str]:
    video = root / "videos" / f"{name}.mov"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(f"synthetic-{name}".encode())
    frames, snapshots = _cadence(
        stage,
        intervals,
        ineligible_pts=ineligible_pts,
        eligible_pts=eligible_pts,
        tb_num=tb_num,
        duplicate=duplicate,
    )
    if drop_last_pts:
        frames[-1].pop("source_pts", None)
    manifest = _manifest(
        video_identity(video),
        frames,
        snapshots,
        manifest_language or language,
        include_video=include_video,
    )
    manifest_path = root / "analysis" / name / "vision_manifest.json"
    entry = _entry()
    entry.update(
        {
            "analysis_dir": f"analysis/{name}",
            "video_path": str(video),
            "video_sha256": sha256_file(video),
            "video_identity": video_identity(video),
            "vision_manifest_sha256": _write_json(manifest_path, manifest),
            "language": language,
        }
    )
    return entry


def _produce(
    tmp: Path, entries: list[dict[str, str]], **kwargs: Any
) -> tuple[Path, Path]:
    source = tmp / "sources.json"
    _write_json(source, {"schema": SOURCE_SCHEMA, "entries": entries})
    blind, eligibility = _outputs(tmp)
    produce_step4(
        _repo(),
        source_manifest=source,
        material_root=tmp,
        blind_output=blind,
        eligibility_output=eligibility,
        **kwargs,
    )
    return blind, eligibility


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_artifact_paths_and_schemas_are_sealed() -> None:
    assert BLIND_INPUT.endswith("amendment11/blind_input_arm_r.json")
    assert ELIGIBILITY_RECORD.endswith("amendment11/eligibility.json")
    assert ELIGIBILITY_SCHEMA == "amendment11-arm-r-eligibility/v1"
    assert SCOPE_ARM_R == "f11_arm_r"


def test_declaration_order_includes_null_stage_and_cross_checks(tmp_path: Path) -> None:
    entries = [
        _recording(tmp_path, "nullstage", stage=None, intervals=6),
        _recording(tmp_path, "ready", stage="makomart", intervals=8),
    ]
    blind_path, eligibility_path = _produce(tmp_path, entries)
    _assert_pair(_load(blind_path), _load(eligibility_path), entries, blind_path)


def _assert_pair(
    blind: dict[str, Any],
    record: dict[str, Any],
    entries: list[dict[str, str]],
    blind_path: Path,
) -> None:
    assert blind["schema"] == "amendment3-blind-input/v1"
    assert blind["rule"] == "amendment4-intro-latch-v1"
    assert blind["scope"] == SCOPE_ARM_R
    assert "pilot_source_manifest_sha256" not in blind
    assert [item["recording"] for item in blind["recordings"]] == ["nullstage", "ready"]
    assert blind["recordings"][0]["stage_id"] is None
    assert blind["recordings"][1]["stage_id"] == "makomart"
    assert record["schema"] == ELIGIBILITY_SCHEMA
    assert blind["tool_commit"] == tooling_commit(_repo())
    assert blind["code_commit"] == head_commit(_repo())
    digest = hashlib.sha256(blind_path.read_bytes()).hexdigest()
    assert record["blind_input"]["sha256"] == digest
    assert record["blind_input"]["schema"] == blind["schema"]
    _assert_determinations(record["entries"], blind["recordings"], entries)


def _assert_determinations(
    entries: list[dict[str, Any]],
    recordings: list[dict[str, Any]],
    declarations: list[dict[str, str]],
) -> None:
    assert [item["analysis_dir"] for item in entries] == [
        "analysis/nullstage",
        "analysis/ready",
    ]
    first, second = entries
    assert first["eligible_interval_count"] == 6
    assert first["geometry"]["status"] == "not_applicable_no_stage"
    assert first["geometry"]["files"] == []
    assert first["eligible"] is False
    assert first["ineligibility_reasons"] == [
        "stage_identity_not_latched",
        "fewer_than_8_eligible_intervals",
    ]
    assert second["eligible_interval_count"] == 8
    assert second["geometry"]["status"] == "resolved"
    assert second["ineligibility_reasons"] == []
    assert second["eligible"] is True
    assert second["source_pts"]["complete"] is True
    pin = "configs/stage_maps/makomart/default.yaml"
    assert second["geometry"]["files"] == [
        {"role": "default", "path": pin, "sha256": STAGE_GEOMETRY_PINS[pin]}
    ]
    for declaration, recording, entry in zip(
        declarations, recordings, entries, strict=True
    ):
        for key in ("video_sha256", "video_identity", "vision_manifest_sha256"):
            assert entry[key] == declaration[key] == recording[key]
        assert entry["intro_latch"]["stage_id"] == recording["stage_id"]
        assert entry["geometry"]["stage_id"] == recording["stage_id"]
        assert entry["geometry"]["battle_mode_id"] == recording["battle_mode_id"]
        assert entry["intro_latch"]["blind_input_recording"] == recording["recording"]


def test_ineligible_missing_source_pts_keeps_an_integer_count(tmp_path: Path) -> None:
    entry = _recording(tmp_path, "partial", stage=None, intervals=6, ineligible_pts=False)
    _, eligibility_path = _produce(tmp_path, [entry])
    recorded = _load(eligibility_path)["entries"][0]
    assert recorded["source_pts"]["frame_count"] == 7
    assert recorded["source_pts"]["usable_source_pts_count"] == 6
    assert recorded["source_pts"]["complete"] is False
    assert recorded["eligible_interval_count"] == 6
    assert recorded["ineligibility_reasons"] == [
        "missing_source_pts",
        "stage_identity_not_latched",
        "fewer_than_8_eligible_intervals",
    ]
    assert "required_geometry_missing" not in recorded["ineligibility_reasons"]


def test_missing_required_timing_makes_only_the_interval_count_null(
    tmp_path: Path,
) -> None:
    entry = _recording(
        tmp_path,
        "untimed",
        stage="makomart",
        intervals=2,
        drop_last_pts=True,
    )
    _, eligibility_path = _produce(tmp_path, [entry])
    recorded = _load(eligibility_path)["entries"][0]
    assert recorded["eligible_interval_count"] is None
    assert recorded["geometry"]["status"] == "resolved"
    assert recorded["ineligibility_reasons"] == ["missing_source_pts"]
    assert recorded["eligible"] is False


def test_invalid_time_base_stops_without_a_null_count(tmp_path: Path) -> None:
    entry = _recording(tmp_path, "badbase", stage="makomart", intervals=2, tb_num="bad")
    blind, eligibility = _outputs(tmp_path)
    with pytest.raises(TypeError):
        _produce(tmp_path, [entry])
    assert not blind.exists()
    assert not eligibility.exists()


def test_duplicate_timestamps_stop_without_a_null_count(tmp_path: Path) -> None:
    entry = _recording(tmp_path, "duplicate", stage=None, intervals=2, duplicate=True)
    blind, eligibility = _outputs(tmp_path)
    with pytest.raises(MalformedInput, match="strictly increasing"):
        _produce(tmp_path, [entry])
    assert not blind.exists()
    assert not eligibility.exists()


def test_manifest_missing_video_stops_without_a_null_count(tmp_path: Path) -> None:
    entry = _recording(tmp_path, "novideo", stage=None, intervals=1, include_video=False)
    blind, eligibility = _outputs(tmp_path)
    with pytest.raises(MalformedInput, match="missing 'video'"):
        _produce(tmp_path, [entry])
    assert not blind.exists()
    assert not eligibility.exists()


def test_missing_geometry_still_counts_intervals(tmp_path: Path) -> None:
    entry = _recording(tmp_path, "nogeo", stage="unpinned_stage", intervals=6)
    _, eligibility_path = _produce(tmp_path, [entry])
    recorded = _load(eligibility_path)["entries"][0]
    assert recorded["eligible_interval_count"] == 6
    assert recorded["geometry"]["status"] == "missing"
    assert recorded["geometry"]["files"][0]["sha256"] is None
    assert recorded["ineligibility_reasons"] == [
        "required_geometry_missing",
        "fewer_than_8_eligible_intervals",
    ]


def test_geometry_digest_mismatch_stops_without_artifacts(tmp_path: Path) -> None:
    entry = _recording(tmp_path, "badpin", stage="makomart", intervals=8)
    geometry = tmp_path / "geometry"
    source = _repo() / "configs/stage_maps/makomart/default.yaml"
    dest = geometry / "configs/stage_maps/makomart/default.yaml"
    dest.parent.mkdir(parents=True)
    data = bytearray(source.read_bytes())
    data[-1] ^= 0x01
    dest.write_bytes(data)
    blind, eligibility = _outputs(tmp_path)
    with pytest.raises(PinMismatch, match="pinned hash mismatch"):
        _produce(tmp_path, [entry], geometry_root=geometry)
    assert not blind.exists()
    assert not eligibility.exists()


def test_hash_language_and_declaration_failures_write_nothing(tmp_path: Path) -> None:
    entry = _recording(tmp_path, "broken", stage=None, intervals=1)
    entry["vision_manifest_sha256"] = "1" * 64
    blind, eligibility = _outputs(tmp_path)
    with pytest.raises(MalformedInput, match="manifest hash"):
        _produce(tmp_path, [entry])
    assert not blind.exists()
    assert not eligibility.exists()
    entry = _recording(tmp_path, "lang", stage=None, intervals=1, manifest_language="ja")
    with pytest.raises(MalformedInput, match="language"):
        _produce(tmp_path, [entry])
    entry = _recording(tmp_path, "literal", stage=None, intervals=1)
    entry["authorization"] = "not the sealed literal"
    with pytest.raises(MalformedInput, match="authorization"):
        _produce(tmp_path, [entry])
    assert not blind.exists()
    assert not eligibility.exists()


def test_path_token_capture_package_and_sealed_identities_stop(tmp_path: Path) -> None:
    prohibited = load_prohibited_sources(_repo())
    token = _recording(tmp_path, "note-2026-10-24", stage=None, intervals=1)
    blind, eligibility = _outputs(tmp_path)
    with pytest.raises(MalformedInput, match="prohibited source"):
        _produce(tmp_path, [token])
    packaged = _recording(tmp_path, "packaged", stage=None, intervals=1)
    marker = tmp_path / "analysis" / "packaged" / "capture_package.json"
    marker.write_text("{}", encoding="utf-8")
    with pytest.raises(MalformedInput, match="prohibited source"):
        _produce(tmp_path, [packaged])
    pooled = next(
        path
        for path in prohibited["analysis_dir"]
        if path.startswith("analysis/")
        and path.count("/") == 1
        and not path.split("/")[1].startswith("_")
        and path.split("/")[1] not in {"alignment-v3-heldout", "review_validation"}
    )
    pooled_name = pooled.split("/", 1)[1]
    pooled_entry = _recording(tmp_path, pooled_name, stage=None, intervals=1)
    with pytest.raises(MalformedInput, match="prohibited source"):
        _produce(tmp_path, [pooled_entry])
    digest_entry = _entry()
    digest_entry["video_sha256"] = next(iter(prohibited["video_sha256"]))
    identity_entry = _entry()
    identity_entry["video_identity"] = next(iter(prohibited["video_identity"]))
    pair_entry = _entry()
    pair_entry["vision_manifest_sha256"] = PAIR009_MANIFEST_SHA256
    for entry in (digest_entry, identity_entry, pair_entry):
        with pytest.raises(MalformedInput, match="prohibited source"):
            require_permitted_source(tmp_path, entry, prohibited)
    assert not blind.exists()
    assert not eligibility.exists()


def test_duplicate_recording_validation_writes_nothing(tmp_path: Path) -> None:
    entry = _recording(tmp_path, "same", stage=None, intervals=1)
    blind, eligibility = _outputs(tmp_path)
    with pytest.raises(MalformedInput, match="duplicate recording"):
        _produce(tmp_path, [entry, dict(entry)])
    assert not blind.exists()
    assert not eligibility.exists()


def test_existing_artifact_is_not_overwritten(tmp_path: Path) -> None:
    entry = _recording(tmp_path, "kept", stage=None, intervals=1)
    blind, eligibility = _outputs(tmp_path)
    eligibility.write_text("keep\n", encoding="utf-8")
    with pytest.raises(SealViolation, match="already exists"):
        _produce(tmp_path, [entry])
    assert eligibility.read_text(encoding="utf-8") == "keep\n"
    assert not blind.exists()
    blind.write_text("keep-blind\n", encoding="utf-8")
    with pytest.raises(SealViolation, match="already exists"):
        _produce(tmp_path, [entry])
    assert blind.read_text(encoding="utf-8") == "keep-blind\n"


def test_extra_keys_and_unknown_reasons_are_rejected(tmp_path: Path) -> None:
    entries = [_recording(tmp_path, "nullstage", stage=None, intervals=6)]
    source = tmp_path / "sources.json"
    blind_path, eligibility_path = _produce(tmp_path, entries)
    blind = _load(blind_path)
    source_sha = sha256_file(source)
    extra = _load(eligibility_path)
    extra["note"] = "free text"
    with pytest.raises(MalformedInput, match="sealed keys"):
        validate_step4_records(extra, blind, entries, source_sha)
    unknown = _load(eligibility_path)
    unknown["entries"][0]["ineligibility_reasons"] = ["overextended"]
    with pytest.raises(MalformedInput, match="reasons"):
        validate_step4_records(unknown, blind, entries, source_sha)
