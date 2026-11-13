"""Admin email when a review-code job is ready for its recording."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from public_site.admin_alerts import SUBJECT, AdminAlertSender, alert_body, payment_label
from public_site.settings import PublicSettings
from test_public_recordings import _create, _paid, _verify_guest, site  # noqa: F401

ADMINS = ("ops@example.test", "second@example.test")


def _enable(site) -> None:  # noqa: F811
    recordings = site.app.state.service.recordings
    settings = replace(site.app.state.settings, admin_alert_to=ADMINS)
    recordings.admin_alerts = AdminAlertSender(settings)


def _alerts(site) -> list[str]:  # noqa: F811
    outbox = site.app.state.settings.root / "outbox"
    return [path.read_text(encoding="utf-8") for path in outbox.glob("admin_alert.*.txt")]


def _customer_notices(site) -> set[str]:  # noqa: F811
    outbox = site.app.state.settings.root / "outbox"
    return {
        path.name
        for path in outbox.glob("*.txt")
        if not path.name.startswith("admin_alert.")
    }


def _sent_events(site, submission_id: str) -> int:  # noqa: F811
    with site.app.state.service.recordings.database._conn() as conn:
        return conn.execute(
            "SELECT count(*) AS n FROM review_code_acquisition_events "
            "WHERE submission_id=%s AND kind='admin_alert_sent'",
            (submission_id,),
        ).fetchone()["n"]


def test_a_paid_job_alerts_every_admin_once(site) -> None:  # noqa: F811
    _enable(site)
    created = _paid(site)
    before = _customer_notices(site)
    svc = site.app.state.service
    svc.maintain(datetime.now(UTC))
    svc.maintain(datetime.now(UTC))
    svc.recordings.send_admin_alerts()
    [text] = _alerts(site)
    assert f"To: {', '.join(ADMINS)}" in text
    assert f"Subject: {SUBJECT}" in text
    assert f"- Job ID: {created['submission_id']}" in text
    assert "- Status: Awaiting video upload" in text
    assert "- Payment: Paid\n" in text
    assert "http://testserver/admin/recordings" in text
    assert "player@example.com" not in text
    assert "EFGH" not in text.upper()
    assert _sent_events(site, created["submission_id"]) == 1
    assert _customer_notices(site) == before


def test_alerts_are_off_without_an_admin_address(site) -> None:  # noqa: F811
    created = _paid(site)
    site.app.state.service.maintain(datetime.now(UTC))
    assert _alerts(site) == []
    assert _sent_events(site, created["submission_id"]) == 0


def test_an_unpaid_request_is_not_announced(site) -> None:  # noqa: F811
    _enable(site)
    created = _create(site)
    _verify_guest(site)
    site.app.state.service.maintain(datetime.now(UTC))
    assert _alerts(site) == []
    assert _sent_events(site, created["submission_id"]) == 0


def test_a_failed_alert_leaves_the_job_alone_and_retries(site, monkeypatch) -> None:  # noqa: F811
    _enable(site)
    created = _paid(site)
    recordings = site.app.state.service.recordings
    real_send = recordings.admin_alerts.send

    def broken(row: dict) -> None:
        raise OSError("mail server down")

    monkeypatch.setattr(recordings.admin_alerts, "send", broken)
    now = datetime.now(UTC)
    recordings.send_admin_alerts(now)
    job = recordings.database.get(created["submission_id"])
    assert job["state"] == "awaiting_recording"
    assert _sent_events(site, created["submission_id"]) == 0
    monkeypatch.setattr(recordings.admin_alerts, "send", real_send)
    recordings.send_admin_alerts(now + timedelta(minutes=1))
    assert _alerts(site) == []
    recordings.send_admin_alerts(now + timedelta(minutes=6))
    assert len(_alerts(site)) == 1
    assert _sent_events(site, created["submission_id"]) == 1


def test_payment_label_names_the_authorization_route() -> None:
    assert payment_label({"payment_state": "paid", "voucher_state": None}) == "Paid"
    voucher = {"payment_state": "not_required", "voucher_state": "redeemed"}
    assert payment_label(voucher) == "Paid by voucher"
    admin = {"payment_state": "not_required", "voucher_state": None}
    assert payment_label(admin) == "Not required (administrator request)"
    body = alert_body({"submission_id": "job-1", **admin}, "https://example.test")
    assert body.endswith("No action has been taken on the recording.\n")


def test_admin_addresses_are_read_from_one_variable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PUBLIC_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "PUBLIC_ADMIN_ALERT_TO", " ops@example.test, ,second@example.test "
    )
    assert PublicSettings.from_env().admin_alert_to == ADMINS
    monkeypatch.delenv("PUBLIC_ADMIN_ALERT_TO")
    assert PublicSettings.from_env().admin_alert_to == ()
