"""``capture review-import``: gate-pass required, identity verified, provenance passed."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from splatoon3_ai_coach.capture import review_import
from splatoon3_ai_coach.capture.gate_runner import RESULT_FILENAME, gate_dir
from splatoon3_ai_coach.capture.models import (
    TIMELINE_CAPTURE_V1,
    AcquisitionMethod,
    ArtifactKind,
    CheckLayer,
    CheckMethod,
    CheckStatus,
    ContractStatus,
    GateCheck,
    GateSummary,
    QualityGateResult,
    SourceType,
    Verdict,
)
from splatoon3_ai_coach.capture.package_io import (
    CapturePackageError,
    add_artifact,
    create_package,
    store_gate_summary,
)
from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.media.video_identity import video_identity
from splatoon3_ai_coach.review.evidence_pack_models import PACK_FILENAME
from splatoon3_ai_coach.review.models import CoverageReport, ReviewTimelineDataset

CONFIG = load_config(default_config_path())


def _package(tmp_path: Path, video: Path) -> tuple[Path, str]:
    package_dir = tmp_path / "pkg"
    create_package(
        package_dir,
        source_type=SourceType.REVIEW_CODE,
        acquisition_method=AcquisitionMethod.MANUAL,
        language="en",
        review_code="ABCD-EFGH-1234-5678",
    )
    artifact = add_artifact(package_dir, video, ArtifactKind.TIMELINE)
    return package_dir, artifact.video_identity


def _gate(
    package_dir: Path,
    video_id: str,
    *,
    status: CheckStatus = CheckStatus.PASS,
    contract_status: ContractStatus = ContractStatus.FROZEN,
) -> None:
    checks = [
        GateCheck(
            check_id="intro_identity_observed",
            layer=CheckLayer.CONTENT,
            method=CheckMethod.AUTOMATIC,
            status=status,
            values={"battle_mode_id": "splat_zones", "stage_id": "scorch_gorge"},
        )
    ]
    verdict = Verdict.PASS if status is CheckStatus.PASS else Verdict.INCOMPLETE
    result = QualityGateResult(
        contract=TIMELINE_CAPTURE_V1,
        contract_status=contract_status,
        artifact_id="timeline",
        video_identity=video_id,
        checked_at="2026-01-01T00:00:00+00:00",
        package_version="test",
        checks=checks,
        automatic_verdict=verdict,
    )
    out = gate_dir(package_dir, TIMELINE_CAPTURE_V1)
    out.mkdir(parents=True)
    (out / RESULT_FILENAME).write_text(result.model_dump_json(), encoding="utf-8")
    store_gate_summary(
        package_dir,
        GateSummary(
            artifact_id="timeline",
            contract=TIMELINE_CAPTURE_V1,
            contract_status=contract_status,
            result_path=f"quality/{TIMELINE_CAPTURE_V1}/{RESULT_FILENAME}",
            automatic_verdict=verdict,
            verdict=verdict,
            checked_at=result.checked_at,
        ),
    )


@pytest.fixture
def fake_adapter(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Stand in for Review's import: write a minimal import, record the call."""
    calls: dict[str, object] = {}

    def adapt(video: Path, out: Path, **kwargs: object) -> SimpleNamespace:
        video_id = calls.get("video_id") or video_identity(video)
        calls.update(kwargs, video=video)
        out.mkdir(parents=True)
        ds = ReviewTimelineDataset(
            source_recording_id=kwargs.get("source_recording_id"),
            battle_mode_id=kwargs.get("battle_mode_id"),
            coverage=CoverageReport(sample_count=0, clocked_sample_count=0),
        )
        (out / "review_timeline.json").write_text(ds.model_dump_json(), encoding="utf-8")
        (out / "timeline_samples_manifest.yaml").write_text(
            yaml.safe_dump({"source_video_id": video_id}), encoding="utf-8"
        )
        return SimpleNamespace(video_id=video_id, dataset=ds)

    monkeypatch.setattr(review_import, "adapt_video", adapt)
    return calls


def test_import_writes_a_pack_with_acquisition_provenance(
    tmp_path: Path, sample_video: Path, fake_adapter: dict[str, object]
) -> None:
    package_dir, video_id = _package(tmp_path, sample_video)
    _gate(package_dir, video_id)
    run = review_import.run_review_import(package_dir, CONFIG)
    assert run.import_dir == package_dir / "derived" / "review_timeline_import"
    assert fake_adapter["battle_mode_id"] == "splat_zones"
    acquisition = run.pack.acquisition
    assert acquisition.artifact_video_identity == video_id
    assert acquisition.gate_verdict == "pass"
    assert acquisition.battle_mode_source == "capture_gate_intro_identity"
    assert acquisition.limitations == []
    payload = json.loads((run.import_dir / PACK_FILENAME).read_text(encoding="utf-8"))
    assert "ABCD-EFGH-1234-5678" not in json.dumps(payload)


def test_gate_run_before_the_freeze_is_recorded(
    tmp_path: Path, sample_video: Path, fake_adapter: dict[str, object]
) -> None:
    package_dir, video_id = _package(tmp_path, sample_video)
    _gate(package_dir, video_id, contract_status=ContractStatus.DRAFT)
    run = review_import.run_review_import(package_dir, CONFIG)
    codes = [item.code for item in run.pack.acquisition.limitations]
    assert codes == ["gate_run_before_freeze"]


def test_refuses_without_a_passing_verdict(
    tmp_path: Path, sample_video: Path, fake_adapter: dict[str, object]
) -> None:
    package_dir, video_id = _package(tmp_path, sample_video)
    with pytest.raises(CapturePackageError, match="no gate run"):
        review_import.run_review_import(package_dir, CONFIG)
    _gate(package_dir, video_id, status=CheckStatus.UNKNOWN)
    with pytest.raises(CapturePackageError, match="verdict is incomplete"):
        review_import.run_review_import(package_dir, CONFIG)
    assert not (package_dir / "derived").exists()


def test_refuses_an_import_of_a_different_video(
    tmp_path: Path, sample_video: Path, fake_adapter: dict[str, object]
) -> None:
    package_dir, video_id = _package(tmp_path, sample_video)
    _gate(package_dir, video_id)
    fake_adapter["video_id"] = "x" * 64
    with pytest.raises(CapturePackageError, match="identity differs"):
        review_import.run_review_import(package_dir, CONFIG)


def test_existing_import_needs_replace(
    tmp_path: Path, sample_video: Path, fake_adapter: dict[str, object]
) -> None:
    package_dir, video_id = _package(tmp_path, sample_video)
    _gate(package_dir, video_id)
    review_import.run_review_import(package_dir, CONFIG)
    with pytest.raises(CapturePackageError, match="exists"):
        review_import.run_review_import(package_dir, CONFIG)
    run = review_import.run_review_import(package_dir, CONFIG, replace=True)
    assert run.pack_path.is_file()
