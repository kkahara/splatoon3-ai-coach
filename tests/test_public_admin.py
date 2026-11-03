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
    for path in (
        "/api/admin/overview",
        "/api/admin/pipeline",
        "/api/admin/failures",
        "/api/admin/coaching",
        "/api/admin/versions",
        "/api/admin/llm",
        "/api/admin/product",
    ):
        assert client.get(path).status_code == 401

    user_session = _session(accounts, "user@example.test", "user")
    client.cookies.set(SESSION_COOKIE, user_session)
    assert client.get("/api/admin/overview").status_code == 403
    assert client.get("/api/admin/coaching").status_code == 403
    assert client.get("/api/admin/product").status_code == 403

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
    telemetry.open_initial_run("a" * 32, user_id=None)

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
    for path in ("/api/admin/coaching", "/api/admin/versions", "/api/admin/llm"):
        rejected = client.get(path, params={"pipeline_version": "%' OR TRUE--"})
        assert rejected.status_code == 422
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


def _insert_run(
    conn,
    submission_id: str,
    status: str,
    *,
    created_ago: float,
    started_ago: float | None,
    finished_ago: float | None,
    error_code: str | None = None,
) -> str:
    def ago(seconds: float | None):
        return None if seconds is None else f"{seconds} seconds"

    return str(
        conn.execute(
            """
            INSERT INTO job_runs (run_id, submission_id, attempt, is_guest, status,
                                  created_at, started_at, finished_at, error_code,
                                  game_language)
            VALUES (gen_random_uuid(), %s, 1, true, %s,
                    now() - %s::interval,
                    now() - %s::interval,
                    now() - %s::interval,
                    %s,
                    CASE WHEN %s = 'completed' THEN 'en' END)
            RETURNING run_id
            """,
            (
                submission_id,
                status,
                ago(created_ago),
                ago(started_ago),
                ago(finished_ago),
                error_code,
                status,
            ),
        ).fetchone()[0]
    )


def test_overview_tolerates_missing_run_timestamps(
    admin_client: tuple[TestClient, AccountStore, TelemetryStore],
) -> None:
    client, accounts, telemetry = admin_client
    client.cookies.set(SESSION_COOKIE, _session(accounts, "ops@example.test", "admin"))
    week = 7 * 24 * 3600
    with psycopg.connect(telemetry.database_url) as conn:
        conn.execute("TRUNCATE admin_audit_log, llm_call_metrics, job_events, job_runs")
        _insert_run(conn, "a" * 32, "failed", created_ago=60, started_ago=None,
                    finished_ago=50, error_code="probe_rejected")
        _insert_run(conn, "b" * 32, "completed", created_ago=week, started_ago=None,
                    finished_ago=60)
        _insert_run(conn, "c" * 32, "running", created_ago=60, started_ago=50,
                    finished_ago=None)
        _insert_run(conn, "d" * 32, "completed", created_ago=60, started_ago=None,
                    finished_ago=None)
        _insert_run(conn, "e" * 32, "interrupted", created_ago=60, started_ago=None,
                    finished_ago=None, error_code="interrupted")
        _insert_run(conn, "f" * 32, "failed", created_ago=60, started_ago=50,
                    finished_ago=40, error_code="analysis_failed")
        skewed = _insert_run(conn, "g" * 32, "completed", created_ago=60, started_ago=50,
                             finished_ago=49)
        conn.execute(
            """
            INSERT INTO job_events (run_id, kind, stage, detail)
            VALUES (%s, 'stage_finished', 'analyze', '{"exit_code": 0, "seconds": 5}')
            """,
            (skewed,),
        )

    response = client.get("/api/admin/overview")
    assert response.status_code == 200, response.text
    problems = {p["submission_id"]: p for p in response.json()["recent_problems"]}
    assert set(problems) == {"a" * 32, "b" * 32, "f" * 32, "g" * 32}
    no_start = problems["a" * 32]
    assert no_start["kind"] == "failed"
    assert no_start["processing_seconds"] is None and no_start["other_seconds"] is None
    assert problems["b" * 32]["kind"] == "slow"
    assert problems["b" * 32]["other_seconds"] is None
    assert problems["f" * 32]["kind"] == "failed"
    assert problems["f" * 32]["processing_seconds"] == pytest.approx(10, abs=0.5)
    assert problems["g" * 32]["kind"] == "timing_inconsistent"


@pytest.mark.parametrize(
    ("status", "other", "kind"),
    [
        ("failed", None, "failed"),
        ("interrupted", None, "failed"),
        ("completed", None, "slow"),
        ("failed", -1.0, "timing_inconsistent"),
        ("completed", 0.0, "slow"),
    ],
)
def test_problem_without_processing_time_has_no_timing_verdict(
    status: str, other: float | None, kind: str
) -> None:
    from public_site.admin import _problem

    row = {
        "submission_id": "s",
        "status": status,
        "failed_stage": None,
        "error_code": None,
        "job_elapsed": 100.0,
        "processing": None if other is None else 10.0,
        "other_seconds": other,
        "finished_at": datetime.now(UTC),
    }
    problem = _problem(row, 60)
    assert problem["kind"] == kind
    assert problem["other_seconds"] == other
