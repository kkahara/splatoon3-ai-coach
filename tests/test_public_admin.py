"""Administrator authorization and bounded query behavior."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from fastapi.testclient import TestClient
from public_site.accounts import AccountStore
from public_site.app import SESSION_COOKIE, create_app
from public_site.settings import PublicSettings
from public_site.telemetry import TelemetryStore

from splatoon3_ai_coach.config.paths import default_config_path

_PASSWORD = "correct-horse"


def _database_url() -> str:
    url = os.environ.get("PUBLIC_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("PUBLIC_DATABASE_URL is not set")
    parts = urlsplit(url)
    admin = urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, ""))
    test_url = urlunsplit((parts.scheme, parts.netloc, "/coach_test", parts.query, ""))
    with psycopg.connect(admin, autocommit=True) as conn:
        if conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", ("coach_test",)
        ).fetchone() is None:
            conn.execute("CREATE DATABASE coach_test")
    return test_url


@pytest.fixture
def admin_client(tmp_path: Path) -> tuple[TestClient, AccountStore, TelemetryStore]:
    url = _database_url()
    accounts = AccountStore(url)
    accounts.ensure_schema()
    telemetry = TelemetryStore(url)
    telemetry.ensure_schema()
    accounts.clear()
    root = tmp_path / "public"
    settings = PublicSettings(
        root=root,
        config_path=default_config_path(),
        static_dir=tmp_path / "missing",
        database_url=url,
        dev_mode=True,
    )
    app = create_app(
        settings,
        accounts=accounts,
        telemetry=telemetry,
        verifier=lambda _token: True,
        start_worker=False,
    )
    client = TestClient(app)
    yield client, accounts, telemetry
    accounts.clear()


def _session(accounts: AccountStore, email: str, role: str) -> str:
    now = datetime.now(UTC)
    verify_token = accounts.register(
        name="Admin Test",
        email=email,
        password=_PASSWORD,
        now=now,
    )
    account, session = accounts.verify(verify_token, now)
    if role != account.role:
        accounts.set_role(email, role)
    return session


def test_admin_endpoints_require_server_side_role(
    admin_client: tuple[TestClient, AccountStore, TelemetryStore],
) -> None:
    client, accounts, _telemetry = admin_client
    assert client.get("/api/admin/overview").status_code == 401
    assert client.get("/api/admin/pipeline").status_code == 401
    assert client.get("/api/admin/failures").status_code == 401

    user_session = _session(accounts, "user@example.test", "user")
    client.cookies.set(SESSION_COOKIE, user_session)
    assert client.get("/api/admin/overview").status_code == 403

    admin_session = _session(accounts, "admin@example.test", "admin")
    client.cookies.set(SESSION_COOKIE, admin_session)
    response = client.get("/api/admin/overview")
    assert response.status_code == 200, response.text
    assert response.json()["requests"] == 0


def test_admin_filters_are_bounded_and_parameterized(
    admin_client: tuple[TestClient, AccountStore, TelemetryStore],
) -> None:
    client, accounts, telemetry = admin_client
    session = _session(accounts, "admin2@example.test", "admin")
    client.cookies.set(SESSION_COOKIE, session)
    telemetry.open_initial_run("a" * 32, user_id=None, language="en")

    assert client.get("/api/admin/requests?limit=201").status_code == 422
    assert client.get("/api/admin/requests?status=surprising").status_code == 422
    assert client.get("/api/admin/requests?cursor=not-base64").status_code == 422
    response = client.get("/api/admin/requests", params={"q": "%' OR TRUE--"})
    assert response.status_code == 200
    assert response.json()["items"] == []
    injected = client.get(
        "/api/admin/pipeline", params={"pipeline_version": "%' OR TRUE--"}
    )
    assert injected.status_code == 422
    pipeline = client.get("/api/admin/pipeline")
    assert pipeline.status_code == 200
    assert pipeline.json()["population"] == "executions_in_window"


def test_role_audit_records_before_and_after(
    admin_client: tuple[TestClient, AccountStore, TelemetryStore],
) -> None:
    _client, accounts, telemetry = admin_client
    _session(accounts, "promote@example.test", "user")
    user_id, before, after = accounts.set_role("promote@example.test", "admin")
    telemetry.audit_role(target_id=user_id, before=before, after=after)

    with psycopg.connect(telemetry.database_url) as conn:
        row = conn.execute(
            """
            SELECT target_id, metadata
            FROM admin_audit_log
            WHERE target_id = %s
            """,
            (user_id,),
        ).fetchone()
    assert str(row[0]) == user_id
    assert row[1] == {
        "via": "cli",
        "role_before": "user",
        "role_after": "admin",
    }
