"""Capture package manifest rules and verdict derivation."""

import pytest
from pydantic import ValidationError

from splatoon3_ai_coach.capture.models import (
    AcquisitionMethod,
    CapturePackage,
    CheckLayer,
    CheckMethod,
    CheckStatus,
    GateCheck,
    ManualAttestation,
    SourceType,
    Verdict,
    derive_verdict,
    normalize_review_code,
)


def _package(**overrides: object) -> CapturePackage:
    fields: dict[str, object] = {
        "package_id": "p",
        "source_type": SourceType.REVIEW_CODE,
        "acquisition_method": AcquisitionMethod.MANUAL,
        "review_code": "abcd-efgh-1234-5678",
        "language": "en",
        "created_at": "2026-10-22T00:00:00+00:00",
    }
    fields.update(overrides)
    return CapturePackage.model_validate(fields)


def _check(check_id: str, status: CheckStatus, required: bool = True) -> GateCheck:
    return GateCheck(
        check_id=check_id,
        layer=CheckLayer.CONTENT,
        method=CheckMethod.AUTOMATIC,
        status=status,
        required=required,
    )


def _attest(check_id: str, status: CheckStatus) -> ManualAttestation:
    return ManualAttestation(
        artifact_id="timeline",
        check_id=check_id,
        status=status,
        note="seen",
        recorded_at="2026-10-22T00:00:00+00:00",
    )


def test_review_code_is_normalized() -> None:
    assert normalize_review_code("abcd efgh-1234 5678") == "ABCD-EFGH-1234-5678"
    assert _package().review_code == "ABCD-EFGH-1234-5678"


def test_bad_review_code_is_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_review_code("ABCD-EFGH")


def test_review_code_package_needs_a_code() -> None:
    with pytest.raises(ValidationError):
        _package(review_code=None)


def test_review_code_package_is_not_an_upload() -> None:
    with pytest.raises(ValidationError):
        _package(acquisition_method=AcquisitionMethod.UPLOAD)


def test_uploaded_video_package_uses_upload_method() -> None:
    package = _package(
        source_type=SourceType.UPLOADED_VIDEO,
        acquisition_method=AcquisitionMethod.UPLOAD,
        review_code=None,
    )
    assert package.source_type is SourceType.UPLOADED_VIDEO
    with pytest.raises(ValidationError):
        _package(source_type=SourceType.UPLOADED_VIDEO, review_code=None)


def test_sclc_and_manual_share_one_contract() -> None:
    assert _package(acquisition_method=AcquisitionMethod.SCLC).review_code


def test_verdict_passes_only_when_every_required_check_passes() -> None:
    checks = [_check("a", CheckStatus.PASS), _check("b", CheckStatus.UNKNOWN, False)]
    assert derive_verdict(checks) is Verdict.PASS


def test_any_failure_fails_even_with_unknowns() -> None:
    checks = [_check("a", CheckStatus.FAIL), _check("b", CheckStatus.UNKNOWN)]
    assert derive_verdict(checks) is Verdict.FAIL


def test_unknown_and_not_run_are_incomplete_until_attested() -> None:
    checks = [_check("a", CheckStatus.UNKNOWN), _check("b", CheckStatus.NOT_RUN)]
    assert derive_verdict(checks) is Verdict.INCOMPLETE
    attested = [_attest("a", CheckStatus.PASS), _attest("b", CheckStatus.PASS)]
    assert derive_verdict(checks, attested) is Verdict.PASS
    assert derive_verdict(checks, [_attest("a", CheckStatus.FAIL)]) is Verdict.FAIL


def test_attestation_never_overrides_an_automatic_decision() -> None:
    checks = [_check("a", CheckStatus.FAIL)]
    assert derive_verdict(checks, [_attest("a", CheckStatus.PASS)]) is Verdict.FAIL
    checks = [_check("a", CheckStatus.PASS)]
    assert derive_verdict(checks, [_attest("a", CheckStatus.FAIL)]) is Verdict.PASS


def test_latest_attestation_wins() -> None:
    checks = [_check("a", CheckStatus.UNKNOWN)]
    attested = [_attest("a", CheckStatus.FAIL), _attest("a", CheckStatus.PASS)]
    assert derive_verdict(checks, attested) is Verdict.PASS
