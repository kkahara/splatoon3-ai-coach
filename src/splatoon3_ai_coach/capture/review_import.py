"""Run the Review timeline import on a gated capture artifact (orchestration only).

Review owns the import and every piece of evidence construction. This module
only checks that the artifact passed ``timeline-capture-v1`` and still has its
recorded identity, calls Review's own entry points, verifies the imported
video identity, and hands acquisition provenance to the Review evidence pack.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from splatoon3_ai_coach.capture.gate_runner import RESULT_FILENAME, gate_dir, load_result
from splatoon3_ai_coach.capture.models import (
    TIMELINE_CAPTURE_V1,
    CaptureArtifact,
    CapturePackage,
    ContractStatus,
    GateSummary,
    QualityGateResult,
    Verdict,
    derive_verdict,
)
from splatoon3_ai_coach.capture.package_io import (
    CapturePackageError,
    attestations_for,
    load_package,
    verify_artifact,
)
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.review.evidence_pack import write_review_evidence_pack
from splatoon3_ai_coach.review.evidence_pack_models import (
    AcquisitionRecord,
    Limitation,
    ReviewEvidencePack,
)
from splatoon3_ai_coach.review.special_fusion import write_special_use_facts
from splatoon3_ai_coach.review.video_adapter import adapt_video

DERIVED_DIRNAME = "derived"
REVIEW_IMPORT_DIRNAME = "review_timeline_import"
_INTRO_CHECK = "intro_identity_observed"


@dataclass(frozen=True)
class ReviewImportRun:
    """Where the import went and the pack written for it."""

    import_dir: Path
    pack_path: Path
    pack: ReviewEvidencePack


def review_import_dir(package_dir: Path) -> Path:
    """Directory holding the Review import of a package's timeline artifact."""
    return package_dir / DERIVED_DIRNAME / REVIEW_IMPORT_DIRNAME


def run_review_import(
    package_dir: Path,
    config: AppConfig,
    *,
    artifact_id: str = "timeline",
    replace: bool = False,
) -> ReviewImportRun:
    """Import a gate-passing timeline artifact and write its evidence pack."""
    package = load_package(package_dir)
    artifact, summary, result = _gated_artifact(package_dir, package, artifact_id)
    out = review_import_dir(package_dir)
    _prepare_output(out, replace)
    mode = _intro_battle_mode(result)
    run = adapt_video(
        package_dir / artifact.path,
        out,
        config=config.review,
        source_recording_id=package.package_id,
        battle_mode_id=mode,
    )
    if run.video_id != artifact.video_identity:
        raise CapturePackageError("imported video identity differs from the artifact")
    _write_special_facts(out)
    record = _acquisition(package_dir, package, artifact, summary, result, mode)
    pack_path, pack = write_review_evidence_pack(out, acquisition=record)
    return ReviewImportRun(out, pack_path, pack)


def _gated_artifact(
    package_dir: Path, package: CapturePackage, artifact_id: str
) -> tuple[CaptureArtifact, GateSummary, QualityGateResult]:
    """The artifact, its gate summary and result; refuses unless the verdict is pass."""
    artifact = package.artifact(artifact_id)
    if artifact is None or artifact.contract != TIMELINE_CAPTURE_V1:
        raise CapturePackageError(f"no {TIMELINE_CAPTURE_V1} artifact {artifact_id!r}")
    summary = next(
        (
            g
            for g in package.gates
            if g.artifact_id == artifact_id and g.contract == TIMELINE_CAPTURE_V1
        ),
        None,
    )
    if summary is None:
        raise CapturePackageError("no gate run yet; run `s3-coach capture check`")
    result = load_result(package_dir, TIMELINE_CAPTURE_V1)
    verdict = derive_verdict(result.checks, attestations_for(package, artifact_id))
    if summary.verdict is not Verdict.PASS or verdict is not Verdict.PASS:
        raise CapturePackageError(
            f"{TIMELINE_CAPTURE_V1} verdict is {verdict.value}; import needs pass"
        )
    if result.video_identity != artifact.video_identity:
        raise CapturePackageError("gate result was produced for a different artifact")
    if not verify_artifact(package_dir, artifact):
        raise CapturePackageError(f"{artifact.path} no longer matches its identity")
    return artifact, summary, result


def _prepare_output(out: Path, replace: bool) -> None:
    if out.exists():
        if not replace:
            raise CapturePackageError(f"{out} exists; pass --replace")
        logger.info("Replacing previous Review import in {}", out)
        shutil.rmtree(out)


def _intro_battle_mode(result: QualityGateResult) -> str | None:
    """Battle mode observed by the gate's intro check, when that check passed."""
    check = next((c for c in result.checks if c.check_id == _INTRO_CHECK), None)
    if check is None or check.status.value != "pass":
        return None
    mode = check.values.get("battle_mode_id")
    return mode if isinstance(mode, str) else None


def _write_special_facts(out: Path) -> None:
    """Review's own facts artifact; a refusal is kept visible in the pack instead."""
    try:
        write_special_use_facts(out)
    except ValueError as exc:
        logger.warning("special_use_facts.json not written: {}", exc)


def _acquisition(
    package_dir: Path,
    package: CapturePackage,
    artifact: CaptureArtifact,
    summary: GateSummary,
    result: QualityGateResult,
    mode: str | None,
) -> AcquisitionRecord:
    result_raw = (
        gate_dir(package_dir, TIMELINE_CAPTURE_V1) / RESULT_FILENAME
    ).read_bytes()
    limitations = []
    if summary.contract_status is not ContractStatus.FROZEN:
        limitations.append(
            Limitation(
                code="gate_run_before_freeze",
                detail=f"gate ran while {TIMELINE_CAPTURE_V1} was "
                f"{summary.contract_status.value}; the freeze review found its "
                "evaluation code unchanged at the freeze",
            )
        )
    return AcquisitionRecord(
        package_id=package.package_id,
        source_type=package.source_type.value,
        acquisition_method=package.acquisition_method.value,
        language=str(package.language),
        artifact_id=artifact.artifact_id,
        artifact_path=artifact.path,
        artifact_video_identity=artifact.video_identity,
        artifact_hash_verified=True,
        import_dir_in_package=f"{DERIVED_DIRNAME}/{REVIEW_IMPORT_DIRNAME}",
        gate_contract=summary.contract,
        gate_contract_status_at_run=summary.contract_status.value,
        gate_automatic_verdict=summary.automatic_verdict.value,
        gate_verdict=summary.verdict.value,
        gate_checked_at=summary.checked_at,
        gate_result_sha256=hashlib.sha256(result_raw).hexdigest(),
        gate_git_sha=result.git_sha,
        battle_mode_id=mode,
        battle_mode_source=None if mode is None else "capture_gate_intro_identity",
        limitations=limitations,
    )
