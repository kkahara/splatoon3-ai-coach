"""Storage failures must never put credentials or request signatures in logs."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fake_s3_helpers import fake_s3
from loguru import logger
from public_site.settings import PublicSettings
from public_site.storage import R2Storage
from report_test_helpers import complete_submission, drain, report_client

from splatoon3_ai_coach.logging_config import configure_logging

SECRET = "SECRET-MARKER-9f8e7d6c5b4a"
ACCESS_KEY = "ACCESSKEYMARKER0001"
ID = "c" * 32
FORBIDDEN = (SECRET, ACCESS_KEY, "Signature=", "Credential=", "X-Amz-", "AWS4-HMAC")


@pytest.fixture
def configured(capfd: pytest.CaptureFixture[str]) -> Iterator[None]:
    """Production logging, bound to the stderr that ``capfd`` captures."""
    capfd.readouterr()
    configure_logging()
    yield
    logger.remove()
    logger.add(sys.stderr)


def _r2(endpoint: str, bucket: str = "test-bucket") -> R2Storage:
    return R2Storage(
        account_id="account",
        access_key_id=ACCESS_KEY,
        secret_access_key=SECRET,
        bucket=bucket,
        endpoint=endpoint,
    )


def _assert_clean(text: str) -> None:
    for marker in FORBIDDEN:
        assert marker not in text, marker


def test_signing_failure_traceback_hides_local_credentials(
    configured: None, capfd: pytest.CaptureFixture[str]
) -> None:
    control: list[str] = []
    sink = logger.add(control.append, diagnose=True, backtrace=True)
    r2 = _r2("http://127.0.0.1:9")
    try:
        r2._request(
            "PUT",
            r2._uri(f"reports/{ID}/r/x.json"),
            extra_headers={"x-amz-meta-bad": "\udc80"},
        )
    except UnicodeEncodeError:
        logger.exception("storage operation failed for submission {}", ID)
    finally:
        logger.remove(sink)
    assert SECRET in "".join(control), "control: diagnose=True would leak the secret"
    err = capfd.readouterr().err
    assert "storage operation failed" in err
    assert "Traceback" in err
    _assert_clean(err)


@pytest.mark.parametrize("status", [403, 500, 503])
def test_storage_http_failures_log_only_the_status(
    configured: None, capfd: pytest.CaptureFixture[str], tmp_path: Path, status: int
) -> None:
    source = tmp_path / "a.json"
    source.write_bytes(b"{}")
    with fake_s3(access_key=ACCESS_KEY, secret=SECRET) as state:
        r2 = _r2(state.endpoint)
        for operation in (
            lambda: r2.put_file(f"reports/{ID}/r/a.json", source, "application/json"),
            lambda: r2.download(f"reports/{ID}/r/a.json", tmp_path / "out.json"),
            lambda: r2.head(f"reports/{ID}/r/a.json"),
            lambda: r2.list_objects(f"reports/{ID}/"),
            lambda: r2.delete(f"reports/{ID}/r/a.json"),
            lambda: r2.read_sha256(f"reports/{ID}/r/a.json"),
        ):
            state.fail_next.append(status)
            try:
                operation()
            except OSError:
                logger.exception("storage operation failed for submission {}", ID)
            else:
                pytest.fail("the injected failure was not raised")
    err = capfd.readouterr().err
    assert err.count("storage operation failed") == 6
    assert f"({status})" in err
    _assert_clean(err)


def test_network_failure_logs_no_endpoint_details(
    configured: None, capfd: pytest.CaptureFixture[str]
) -> None:
    try:
        _r2("http://127.0.0.1:9").head(f"submissions/{ID}/source.mp4")
    except OSError:
        logger.exception("storage operation failed")
    err = capfd.readouterr().err
    assert "storage head failed (network:" in err
    _assert_clean(err)


@pytest.mark.parametrize("status", [403, 503])
def test_report_publishing_failures_log_no_credentials(
    configured: None, capfd: pytest.CaptureFixture[str], tmp_path: Path, status: int
) -> None:
    with fake_s3(access_key=ACCESS_KEY, secret=SECRET) as state:
        client = report_client(
            tmp_path, state, report_publish=True, report_expiry_delete=True
        )
        _token, submission = complete_submission(client, tmp_path)
        state.fail_always = status
        drain(client.app.state.reports)
        record = client.app.state.reports.publications.get(submission.id)
        assert record.last_error == f"status_{str(status)[0]}xx"
        client.close()
    err = capfd.readouterr().err
    assert "report publish attempt failed" in err
    _assert_clean(err)
    assert state.endpoint not in err
    assert str(tmp_path) not in err


def test_settings_and_storage_repr_hide_credentials(tmp_path: Path) -> None:
    settings = PublicSettings(
        root=tmp_path,
        config_path=tmp_path / "c.yaml",
        static_dir=tmp_path,
        database_url=f"postgresql://coach:{SECRET}@db/coach",
        turnstile_secret=SECRET,
        r2_access_key_id=ACCESS_KEY,
        r2_secret_access_key=SECRET,
        smtp_password=SECRET,
        stripe_secret_key=SECRET,
        stripe_webhook_secret=SECRET,
        voucher_hmac_secret=SECRET,
    )
    _assert_clean(repr(settings))
    _assert_clean(repr(_r2("https://example.invalid")))
