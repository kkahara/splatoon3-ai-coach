"""Run a capture contract gate on a stored artifact and record the verdict.

Gate outputs live under ``quality/<contract>/`` and are written once per run;
re-running needs ``replace``. Operator confirmations never rewrite a result:
they are stored in the package and combined with it into the verdict.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from loguru import logger

from splatoon3_ai_coach.capture.models import (
    QUALITY_DIRNAME,
    TIMELINE_CAPTURE_V1,
    CheckStatus,
    GateSummary,
    ManualAttestation,
    QualityGateResult,
    derive_verdict,
)
from splatoon3_ai_coach.capture.package_io import (
    CapturePackageError,
    attestations_for,
    load_package,
    record_attestation,
    store_gate_summary,
    verify_artifact,
)
from splatoon3_ai_coach.capture.timeline_gate import evaluate_timeline_capture
from splatoon3_ai_coach.capture.timeline_observe import observe_timeline_video
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.vision.provenance import git_short_sha, package_version

RESULT_FILENAME = "result.json"
SAMPLES_FILENAME = "samples.json"
RETAINED_DIRNAME = "retained_samples"


def gate_dir(package_dir: Path, contract: str) -> Path:
    """Directory holding one contract's gate outputs."""
    return package_dir / QUALITY_DIRNAME / contract


def run_timeline_gate(
    package_dir: Path,
    config: AppConfig,
    *,
    artifact_id: str = "timeline",
    replace: bool = False,
) -> tuple[QualityGateResult, GateSummary]:
    """Decode the timeline artifact once, evaluate the DRAFT contract, write outputs."""
    package = load_package(package_dir)
    artifact = package.artifact(artifact_id)
    if artifact is None or artifact.contract != TIMELINE_CAPTURE_V1:
        raise CapturePackageError(f"no {TIMELINE_CAPTURE_V1} artifact {artifact_id!r}")
    out = gate_dir(package_dir, TIMELINE_CAPTURE_V1)
    _prepare_output(out, replace)
    observation = observe_timeline_video(
        package_dir / artifact.path,
        timeline_config=config.review,
        intro_config=config.vision.match_intro,
        language=package.language,
        samples_dir=out / RETAINED_DIRNAME,
    )
    result = evaluate_timeline_capture(
        observation,
        artifact_id=artifact_id,
        video_identity=artifact.video_identity,
        hash_verified=verify_artifact(package_dir, artifact),
        timeline_config=config.review,
        package_version=package_version(),
        git_sha=git_short_sha(),
    )
    _write_json(out / RESULT_FILENAME, result.model_dump(mode="json"))
    _write_json(
        out / SAMPLES_FILENAME,
        [sample.model_dump(mode="json") for sample in observation.samples],
    )
    summary = _summarize(package_dir, result, attestations_for(package, artifact_id))
    return result, summary


def confirm_check(
    package_dir: Path,
    *,
    check_id: str,
    status: CheckStatus,
    note: str,
    artifact_id: str = "timeline",
    contract: str = TIMELINE_CAPTURE_V1,
) -> tuple[ManualAttestation, GateSummary]:
    """Record an operator decision for an unknown or manual check, then re-derive."""
    result = load_result(package_dir, contract)
    check = next((c for c in result.checks if c.check_id == check_id), None)
    if check is None:
        raise CapturePackageError(f"{contract} has no check {check_id!r}")
    if check.status not in (CheckStatus.UNKNOWN, CheckStatus.NOT_RUN):
        raise CapturePackageError(
            f"{check_id} was decided automatically ({check.status.value}); "
            "attestations only decide unknown or manual checks"
        )
    attestation = record_attestation(
        package_dir, artifact_id=artifact_id, check_id=check_id, status=status, note=note
    )
    package = load_package(package_dir)
    summary = _summarize(package_dir, result, attestations_for(package, artifact_id))
    return attestation, summary


def load_result(
    package_dir: Path, contract: str = TIMELINE_CAPTURE_V1
) -> QualityGateResult:
    """Read a stored gate result."""
    path = gate_dir(package_dir, contract) / RESULT_FILENAME
    if not path.is_file():
        raise CapturePackageError(
            f"no {contract} result yet; run `s3-coach capture check`"
        )
    return QualityGateResult.model_validate_json(path.read_text(encoding="utf-8"))


def _summarize(
    package_dir: Path, result: QualityGateResult, attestations: list[ManualAttestation]
) -> GateSummary:
    summary = GateSummary(
        artifact_id=result.artifact_id,
        contract=result.contract,
        contract_status=result.contract_status,
        result_path=f"{QUALITY_DIRNAME}/{result.contract}/{RESULT_FILENAME}",
        automatic_verdict=result.automatic_verdict,
        verdict=derive_verdict(result.checks, attestations),
        checked_at=result.checked_at,
    )
    store_gate_summary(package_dir, summary)
    return summary


def _prepare_output(out: Path, replace: bool) -> None:
    if out.exists():
        if not replace:
            raise CapturePackageError(f"{out.name} gate output exists; pass --replace")
        logger.info("Replacing previous gate output in {}", out)
        shutil.rmtree(out)
    out.mkdir(parents=True)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
