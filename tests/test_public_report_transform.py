"""Sanitized published copies: fingerprint redaction and per-run pseudonyms."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from public_site.report_bundle import BundleError, prepare_bundle
from public_site.report_content import KnownValues
from public_site.report_staging import StagingArea
from public_site.report_transform import (
    PSEUDONYMIZE_ANALYSIS_ID,
    REDACT_VIDEO_IDENTITY,
    REDACTED_SENTINEL,
    TransformError,
    read_identity,
    transform_json,
    verify_published,
)
from public_site.results import load_result
from report_bundle_fixture import add_media, folder_digest, write_report_fixture

from splatoon3_ai_coach.vision.ids import parse_result_id
from splatoon3_ai_coach.vision.models import VisionManifest

SID = "a" * 32


def _prepare(analysis: Path, run_id: str = "20261109T120000Z-0000000a", **kwargs):
    return prepare_bundle(
        analysis,
        submission_id=SID,
        run_id=run_id,
        outcome="coached",
        created_at="2026-11-09T12:00:00Z",
        known=KnownValues(),
        staging=None,
        **kwargs,
    )


@pytest.fixture
def analysis(tmp_path: Path) -> Path:
    folder = tmp_path / SID
    write_report_fixture(folder, death_times=(0.5, 3.0))
    add_media(folder)
    return folder


def _published_folder(analysis: Path, prepared, dest: Path) -> Path:
    for item in prepared.files:
        target = dest / item.path
        target.parent.mkdir(parents=True, exist_ok=True)
        data = prepared.published_json.get(item.path)
        if data is None:
            shutil.copyfile(item.source, target)
        else:
            target.write_bytes(data)
    return dest


def test_originals_appear_in_no_published_byte(analysis: Path) -> None:
    identity = read_identity((analysis / "vision_manifest.json").read_bytes())
    prepared = _prepare(analysis)
    originals = (identity.video_identity.encode(), identity.analysis_id.encode())
    for data in [*prepared.published_json.values(), prepared.bundle_bytes]:
        assert not any(token in data for token in originals)
    for item in prepared.files:
        if item.path.endswith((".mp4", ".jpg")):
            body = item.source.read_bytes()
            assert not any(token in body for token in originals)


def test_published_manifest_validates_with_one_consistent_pseudonym(
    analysis: Path,
) -> None:
    prepared = _prepare(analysis)
    manifest = VisionManifest.model_validate_json(
        prepared.published_json["vision_manifest.json"]
    )
    pseudonym = manifest.analysis.analysis_id
    assert (
        manifest.video_identity == manifest.analysis.video_identity == REDACTED_SENTINEL
    )
    for frame in manifest.frame_results:
        assert frame.frame_id.startswith(f"{pseudonym}:frame:")
        for detection in frame.detections:
            assert parse_result_id(detection.id).analysis_id == pseudonym
    for snapshot in manifest.state_snapshots:
        assert all(
            parse_result_id(e).analysis_id == pseudonym for e in snapshot.evidence_ids
        )
    index = json.loads(prepared.published_json["coach_inputs/coaching_index.json"])
    assert index["analysis_id"] == pseudonym
    unit = json.loads(
        prepared.published_json["coach_inputs/death_episode_0.5.coach_input.json"]
    )
    observation = unit["primary_context"]["special"]["observations"][0]["observation_id"]
    assert observation.startswith(f"{pseudonym}:")


def test_each_run_gets_a_fresh_pseudonym(analysis: Path) -> None:
    first = _prepare(analysis)
    second = _prepare(analysis, run_id="20261109T120001Z-0000000b")
    a = VisionManifest.model_validate_json(first.published_json["vision_manifest.json"])
    b = VisionManifest.model_validate_json(second.published_json["vision_manifest.json"])
    assert a.analysis.analysis_id != b.analysis.analysis_id


def test_transforms_are_recorded_per_file_and_local_hashes_kept(analysis: Path) -> None:
    prepared = _prepare(analysis)
    files = {item.path: item for item in prepared.bundle.files}
    manifest = files["vision_manifest.json"]
    assert manifest.transforms == [REDACT_VIDEO_IDENTITY, PSEUDONYMIZE_ANALYSIS_ID]
    assert manifest.local_sha256 != manifest.published_sha256
    assert files["coach_inputs/coaching_index.json"].transforms == [
        PSEUDONYMIZE_ANALYSIS_ID
    ]
    untouched = files["match_identity.json"]
    assert untouched.transforms == []
    assert untouched.local_sha256 == untouched.published_sha256


def test_results_and_staleness_survive_the_transform(
    analysis: Path, tmp_path: Path
) -> None:
    prepared = _prepare(analysis)
    published = _published_folder(analysis, prepared, tmp_path / "published")
    assert load_result(analysis).stale is False
    assert load_result(published) == load_result(analysis)


def test_stale_report_carries_a_second_fingerprint_and_is_blocked(analysis: Path) -> None:
    stale_index = analysis / "coach_inputs" / "coaching_index.json"
    payload = json.loads(stale_index.read_text())
    payload["analysis_id"] = "f" * 64
    stale_index.write_text(json.dumps(payload))
    assert load_result(analysis).stale is True
    with pytest.raises(BundleError) as caught:
        _prepare(analysis)
    assert (caught.value.kind, caught.value.rule) == (
        "transform_failed",
        "analysis_id_inconsistent",
    )


def test_local_folder_is_never_modified(analysis: Path, tmp_path: Path) -> None:
    before = folder_digest(analysis)
    area = StagingArea(tmp_path / "public")
    run = area.open_run(SID, "20261109T120000Z-0000000a")
    try:
        _prepare_with_staging(analysis, run)
    finally:
        run.close()
    (analysis / "coach_inputs" / "death_episode_0.5.coaching.json").write_text(
        json.dumps({"victim_name": "planted"}), encoding="utf-8"
    )
    planted = folder_digest(analysis)
    with pytest.raises(BundleError):
        _prepare(analysis)
    assert folder_digest(analysis) == planted != before


def _prepare_with_staging(analysis: Path, run) -> None:
    prepared = prepare_bundle(
        analysis,
        submission_id=SID,
        run_id=run.run_id,
        outcome="coached",
        created_at="2026-11-09T12:00:00Z",
        known=KnownValues(),
        staging=run,
    )
    staged = [item for item in prepared.files if item.staged]
    assert staged and all(item.source.parent == run.path for item in staged)


def test_manifest_without_tokens_fails_closed(analysis: Path) -> None:
    identity = read_identity((analysis / "vision_manifest.json").read_bytes())
    with pytest.raises(TransformError, match="video_identity_not_found"):
        transform_json("vision_manifest.json", b"{}", identity, "b" * 64)


def test_inconsistent_reference_is_rejected(analysis: Path) -> None:
    identity = read_identity((analysis / "vision_manifest.json").read_bytes())
    prepared = _prepare(analysis)
    files = dict(prepared.published_json)
    pseudonym = VisionManifest.model_validate_json(
        files["vision_manifest.json"]
    ).analysis.analysis_id
    files["coach_inputs/extra.coach_input.json"] = json.dumps(
        {"observation_id": f"{'e' * 64}:special:000001"}
    ).encode()
    with pytest.raises(TransformError, match="reference_inconsistent"):
        verify_published(files, identity, pseudonym)
    files.pop("coach_inputs/extra.coach_input.json")
    files["coach_inputs/coaching_index.json"] = json.dumps(
        {"analysis_id": "e" * 64}
    ).encode()
    with pytest.raises(TransformError, match="analysis_id_inconsistent"):
        verify_published(files, identity, pseudonym)


def test_unexpected_extra_original_occurrence_is_still_replaced(analysis: Path) -> None:
    identity = read_identity((analysis / "vision_manifest.json").read_bytes())
    path = analysis / "coach_prototype" / "death_episode_0.5.model.flags.json"
    payload = json.loads(path.read_text())
    payload["note"] = f"see {identity.analysis_id}:result:x:y:z:w"
    path.write_text(json.dumps(payload))
    prepared = _prepare(analysis)
    assert (
        identity.analysis_id.encode()
        not in prepared.published_json[path.relative_to(analysis).as_posix()]
    )
