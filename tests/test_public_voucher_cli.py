"""Operator voucher CLI: issue, status, list. Only the coach_test database is used."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from public_site.payments import PaymentStore
from public_site.vouchers import VoucherStore, code_hash, normalize_voucher_code
from test_public_payments import _database_url
from test_public_vouchers import (
    _SECRET,
    _assert_plaintext_absent,
    _expire,
    _open_pending,
    _submit,
    _voucher_client,
)
from typer.testing import CliRunner

from splatoon3_ai_coach.cli.app import app

_EXPIRY = "2099-01-01T09:00:00+09:00"
_CODE_FORMAT = re.compile(r"^SKT-[A-Z2-9]{4}-[A-Z2-9]{4}$")
# pytest puts tools/ on sys.path, so only a fresh interpreter sees the CLI's own
# bootstrap. The first assert proves public_site is not importable beforehand.
_FRESH_CLI = """
import importlib.util
assert importlib.util.find_spec("public_site") is None, "tools/ already importable"
from splatoon3_ai_coach.cli.app import app
app()
"""
runner = CliRunner()


@pytest.fixture
def payments() -> PaymentStore:
    store = PaymentStore(_database_url())
    store.ensure_schema()
    store.clear()
    yield store
    store.clear()


@pytest.fixture
def vouchers(payments: PaymentStore, monkeypatch: pytest.MonkeyPatch) -> VoucherStore:
    assert payments.database_url.endswith("/coach_test")
    store = VoucherStore(payments.database_url, _SECRET)
    store.ensure_schema()
    store.clear()
    payments.set_price(500)
    monkeypatch.setenv("PUBLIC_DATABASE_URL", payments.database_url)
    monkeypatch.setenv("PUBLIC_VOUCHER_HMAC_SECRET", _SECRET)
    yield store
    payments.clear()
    store.clear()


def _cli(*args: str, input: str | None = None):
    return runner.invoke(app, ["public-admin", "voucher", *args], input=input)


def _issue(*extra: str, campaign: str = "cli_test") -> tuple[str, str, str]:
    """Issue through the CLI. Returns (voucher_id, code, output)."""
    args = ["issue", "--campaign", campaign, "--expires-at", _EXPIRY, *extra]
    if "--source" not in extra:
        args += ["--source", "skilltown"]
    result = _cli(*args)
    assert result.exit_code == 0, result.output
    voucher_id = re.search(r"^voucher_id=(\S+)$", result.output, re.M).group(1)
    code = re.search(r"^code=(\S+)$", result.output, re.M).group(1)
    return voucher_id, code, result.output


def _redeem(payments: PaymentStore, vouchers: VoucherStore, voucher_id: str) -> None:
    submission_id = _open_pending(payments, voucher_id)
    assert vouchers.finalize(submission_id, datetime.now(UTC)).outcome == "redeemed"


def _snapshot(vouchers: VoucherStore) -> tuple[list, list, list]:
    with vouchers._conn() as conn:
        return (
            conn.execute("SELECT * FROM vouchers ORDER BY id").fetchall(),
            conn.execute("SELECT * FROM voucher_redemptions ORDER BY id").fetchall(),
            conn.execute(
                "SELECT * FROM submission_gates ORDER BY submission_id"
            ).fetchall(),
        )


def _assert_nothing_sensitive(output: str, vouchers: VoucherStore) -> None:
    """No HMAC secret, stored hash, submission id, or email-like text."""
    vouchers_rows, redemptions, _gates = _snapshot(vouchers)
    assert _SECRET not in output
    for row in vouchers_rows:
        assert row["code_hash"] not in output
    for row in redemptions:
        assert str(row["submission_id"]) not in output
    assert "@" not in output


def _forbid_schema_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_self) -> None:
        raise AssertionError("read-only command ran schema setup")

    monkeypatch.setattr(VoucherStore, "ensure_schema", refuse)


# Create


def test_issue_defaults_to_one_use_and_prints_code_once(vouchers: VoucherStore) -> None:
    voucher_id, code, output = _issue()
    assert _CODE_FORMAT.match(code)
    assert output.count(code) == 1
    _assert_nothing_sensitive(output, vouchers)
    _assert_plaintext_absent(vouchers, code)
    found = vouchers.status(voucher_id)
    assert found is not None
    assert found.max_redemptions == 1
    assert found.campaign == "cli_test"
    assert found.expires_at == datetime(2099, 1, 1, 0, 0, tzinfo=UTC)


def test_issued_code_resolves_through_the_application_hmac(
    vouchers: VoucherStore,
) -> None:
    voucher_id, code, _output = _issue()
    with vouchers._conn() as conn:
        stored = conn.execute(
            "SELECT code_hash FROM vouchers WHERE id = %s", (voucher_id,)
        ).fetchone()["code_hash"]
    assert stored == code_hash(normalize_voucher_code(code), _SECRET)
    found = vouchers.status_for_code(f"  {code.lower()} ")
    assert found is not None and found.voucher_id == voucher_id


def test_issue_without_secret_creates_nothing(
    vouchers: VoucherStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PUBLIC_VOUCHER_HMAC_SECRET", "")
    result = _cli(
        "issue", "--source", "skilltown", "--campaign", "cli_test",
        "--expires-at", _EXPIRY,
    )
    assert result.exit_code != 0
    assert "PUBLIC_VOUCHER_HMAC_SECRET is required" in result.output
    assert "code=" not in result.output
    assert vouchers.overview() == []


# Status


def test_status_unused_then_fully_used(
    payments: PaymentStore, vouchers: VoucherStore
) -> None:
    voucher_id, code, _output = _issue()
    before = _cli("status", code)
    assert before.exit_code == 0, before.output
    assert "state=active" in before.output
    assert "uses=0/1" in before.output
    assert "redeemed_at=" not in before.output
    _redeem(payments, vouchers, voucher_id)
    after = _cli("status", code)
    assert after.exit_code == 0, after.output
    assert "state=fully_used" in after.output
    assert "uses=1/1" in after.output
    assert after.output.count("redeemed_at=") == 1
    _assert_nothing_sensitive(after.output, vouchers)
    assert code not in after.output


def test_status_multi_use_counts_every_redemption(
    payments: PaymentStore, vouchers: VoucherStore
) -> None:
    voucher_id, code, _output = _issue(
        "--source", "youtube", "--code", "CLITEST3", "--max-redemptions", "3"
    )
    _redeem(payments, vouchers, voucher_id)
    _redeem(payments, vouchers, voucher_id)
    result = _cli("status", "clitest3")
    assert result.exit_code == 0, result.output
    assert "state=active" in result.output
    assert "uses=2/3" in result.output
    assert result.output.count("redeemed_at=") == 2
    _assert_nothing_sensitive(result.output, vouchers)


def test_status_reports_expired(vouchers: VoucherStore) -> None:
    voucher_id, code, _output = _issue()
    _expire(vouchers, voucher_id)
    result = _cli("status", code)
    assert result.exit_code == 0, result.output
    assert "state=expired" in result.output
    assert "uses=0/1" in result.output


@pytest.mark.parametrize("code", ["SKT-ZZZZ-ZZZZ", "SQUID LAB"])
def test_status_unknown_code_is_not_found(vouchers: VoucherStore, code: str) -> None:
    _issue()
    result = _cli("status", code)
    assert result.exit_code == 2
    assert "voucher was not found" in result.output


def test_status_by_id_matches_and_needs_no_secret(
    vouchers: VoucherStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    voucher_id, code, _output = _issue()
    by_code = _cli("status", code)
    monkeypatch.setenv("PUBLIC_VOUCHER_HMAC_SECRET", "")
    by_id = _cli("status", "--id", voucher_id)
    assert by_id.exit_code == 0, by_id.output
    assert by_id.output == by_code.output
    missing = _cli("status", "--id", "not-a-uuid")
    assert missing.exit_code == 2
    assert "voucher was not found" in missing.output


def test_status_rejects_code_and_id_together(vouchers: VoucherStore) -> None:
    voucher_id, code, _output = _issue()
    result = _cli("status", code, "--id", voucher_id)
    assert result.exit_code == 2
    assert "not both" in result.output
    assert "state=" not in result.output


def test_interactive_status_hides_the_code(vouchers: VoucherStore) -> None:
    _voucher_id, code, _output = _issue()
    result = _cli("status", input=f"{code}\n")
    assert result.exit_code == 0, result.output
    assert "Voucher code" in result.output
    assert code not in result.output
    assert "state=active" in result.output
    assert "uses=0/1" in result.output


def test_code_lookups_need_the_secret(
    vouchers: VoucherStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _voucher_id, code, _output = _issue()
    monkeypatch.setenv("PUBLIC_VOUCHER_HMAC_SECRET", "")
    positional = _cli("status", code)
    assert positional.exit_code == 2
    assert "PUBLIC_VOUCHER_HMAC_SECRET is required" in positional.output
    prompted = _cli("status", input=f"{code}\n")
    assert prompted.exit_code == 2
    assert "PUBLIC_VOUCHER_HMAC_SECRET is required" in prompted.output
    assert "Voucher code" not in prompted.output


def test_status_does_not_change_any_row(
    payments: PaymentStore, vouchers: VoucherStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    voucher_id, code, _output = _issue()
    _redeem(payments, vouchers, voucher_id)
    _issue(campaign="cli_other")
    before = _snapshot(vouchers)
    _forbid_schema_setup(monkeypatch)
    assert _cli("status", code).exit_code == 0
    assert _cli("status", "--id", voucher_id).exit_code == 0
    assert _cli("status", input=f"{code}\n").exit_code == 0
    assert _cli("status", "SKT-ZZZZ-ZZZZ").exit_code == 2
    assert _snapshot(vouchers) == before


# List


def test_list_states_counts_and_newest_first(
    payments: PaymentStore, vouchers: VoucherStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    active_id, active_code, _ = _issue(campaign="cli_active")
    used_id, used_code, _ = _issue(campaign="cli_used")
    expired_id, expired_code, _ = _issue(campaign="cli_expired")
    revoked_id, revoked_code, _ = _issue(campaign="cli_revoked")
    _redeem(payments, vouchers, used_id)
    _expire(vouchers, expired_id)
    assert vouchers.revoke(revoked_id) is True
    before = _snapshot(vouchers)
    _forbid_schema_setup(monkeypatch)
    monkeypatch.setenv("PUBLIC_VOUCHER_HMAC_SECRET", "")
    result = _cli("list")
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert lines[0].split() == [
        "CREATED", "CAMPAIGN", "SOURCE", "SERVICE", "STATE",
        "USED", "LIMIT", "EXPIRES", "ID",
    ]
    rows = [line.split() for line in lines[1:]]
    assert [(row[1], row[4], row[5], row[6], row[8]) for row in rows] == [
        ("cli_revoked", "revoked", "0", "1", revoked_id),
        ("cli_expired", "expired", "0", "1", expired_id),
        ("cli_used", "fully_used", "1", "1", used_id),
        ("cli_active", "active", "0", "1", active_id),
    ]
    for code in (active_code, used_code, expired_code, revoked_code):
        assert code not in result.output
    _assert_nothing_sensitive(result.output, vouchers)
    assert _snapshot(vouchers) == before


def test_list_bootstraps_tools_in_a_fresh_process(
    vouchers: VoucherStore, tmp_path: Path
) -> None:
    _issue(campaign="cli_fresh_process")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PUBLIC_DATABASE_URL"] = vouchers.database_url
    env["PUBLIC_VOUCHER_HMAC_SECRET"] = ""
    proc = subprocess.run(
        [sys.executable, "-c", _FRESH_CLI, "public-admin", "voucher", "list"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "ModuleNotFoundError" not in proc.stderr
    assert proc.returncode == 0, proc.stderr
    assert "cli_fresh_process" in proc.stdout


def test_list_empty(vouchers: VoucherStore) -> None:
    result = _cli("list")
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "no vouchers"


# Compatibility


def test_issued_code_redeems_through_the_real_submission_path(
    tmp_path, payments: PaymentStore, vouchers: VoucherStore
) -> None:
    voucher_id, code, _output = _issue()
    client = _voucher_client(tmp_path, payments)
    checked = client.post("/api/vouchers/validate", json={"code": code})
    assert checked.status_code == 200, checked.text
    assert checked.json()["accepted"] is True
    view = _submit(client, code)
    assert view["voucher_state"] == "redeemed"
    assert view["payment_state"] == "not_required"
    assert view["status"] == "queued"
    assert vouchers.redemption_count(voucher_id) == 1
    result = _cli("status", input=f"{code}\n")
    assert result.exit_code == 0, result.output
    assert "state=fully_used" in result.output
    assert "uses=1/1" in result.output
    _assert_nothing_sensitive(result.output, vouchers)
