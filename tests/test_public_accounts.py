"""Account registration, reset, and owned coaching history."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from fastapi.testclient import TestClient
from public_site.accounts import AccountStore
from public_site.app import create_app
from public_site.errors import BAD_LOGIN, BAD_PASSWORD, EMAIL_TAKEN, UNVERIFIED
from public_site.media_probe import ProbeFacts
from public_site.settings import PublicSettings
from public_site.store import parse_utc

from splatoon3_ai_coach.config.paths import default_config_path

_PASSWORD = "correct-horse"
_NEW_PASSWORD = "fresh-secret"


def _database_url() -> str:
    url = os.environ.get("PUBLIC_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("PUBLIC_DATABASE_URL is not set")
    return _ensure_test_db(url)


def _ensure_test_db(url: str) -> str:
    parts = urlsplit(url)
    admin = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
    test_url = urlunsplit((parts.scheme, parts.netloc, "/coach_test", "", ""))
    with psycopg.connect(admin, autocommit=True) as conn:
        found = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            ("coach_test",),
        ).fetchone()
        if found is None:
            conn.execute("CREATE DATABASE coach_test")
    return test_url


@pytest.fixture
def accounts() -> AccountStore:
    store = AccountStore(_database_url())
    store.ensure_schema()
    store.clear()
    yield store
    store.clear()


def _probe_ok(_path: Path) -> ProbeFacts:
    return ProbeFacts(12.0, 1920, 1080, "mp4")


def _client(tmp_path: Path, accounts: AccountStore) -> TestClient:
    root = tmp_path / "public"
    root.mkdir(parents=True, exist_ok=True)
    settings = PublicSettings(
        root=root,
        config_path=default_config_path(),
        static_dir=tmp_path / "no-dist",
        dev_mode=True,
        worker_id="worker-test",
        public_base_url="http://coach.example",
        max_video_bytes=1000,
    )
    app = create_app(
        settings,
        accounts=accounts,
        verifier=lambda token: token == "ok",
        probe=_probe_ok,
        run_command=lambda _argv: 0,
        start_worker=False,
    )
    return TestClient(app)


def _register(client: TestClient, email: str, *, name: str = "Rin") -> None:
    response = client.post(
        "/api/auth/register",
        json={"name": name, "email": email, "password": _PASSWORD},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"sent": True}
    assert _PASSWORD not in response.text


def _mail_link(root: Path, kind: str) -> str:
    paths = sorted(
        (root / "outbox").glob(f"account.{kind}.*.txt"),
        key=lambda path: path.stat().st_mtime,
    )
    assert paths, kind
    lines = paths[-1].read_text().splitlines()
    subject = next(line for line in lines if line.startswith("Subject:"))
    urls = [line.strip() for line in lines if line.startswith("http")]
    assert len(urls) == 1
    token = urls[0].rstrip("/").split("/")[-1]
    assert token not in subject
    return urls[0]


def _token_from(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def _verify(client: TestClient) -> None:
    url = _mail_link(client.app.state.settings.root, "verify")
    response = client.post("/api/auth/verify", json={"token": _token_from(url)})
    assert response.status_code == 200, response.text
    assert response.json()["email"]


def test_login_waits_for_the_verify_link(tmp_path: Path, accounts: AccountStore) -> None:
    client = _client(tmp_path, accounts)
    short = client.post(
        "/api/auth/register",
        json={"name": "Rin", "email": "rin@example.com", "password": "short"},
    )
    assert short.status_code == 400
    assert short.json()["detail"] == BAD_PASSWORD
    _register(client, "rin@example.com")
    early = client.post(
        "/api/auth/login",
        json={"email": "rin@example.com", "password": _PASSWORD},
    )
    assert early.status_code == 403
    assert early.json()["detail"] == UNVERIFIED
    url = _mail_link(client.app.state.settings.root, "verify")
    token = _token_from(url)
    with psycopg.connect(accounts.database_url) as conn:
        stored = conn.execute("SELECT token_hash FROM email_tokens").fetchall()
    assert stored
    assert all(token not in row[0] for row in stored)
    verified = client.post("/api/auth/verify", json={"token": token})
    assert verified.status_code == 200, verified.text
    assert client.get("/api/me").json()["name"] == "Rin"
    again = client.post(
        "/api/auth/register",
        json={"name": "Rin", "email": "rin@example.com", "password": _PASSWORD},
    )
    assert again.status_code == 400
    assert again.json()["detail"] == EMAIL_TAKEN


def test_reset_replaces_the_password_and_old_sessions(
    tmp_path: Path, accounts: AccountStore
) -> None:
    client = _client(tmp_path, accounts)
    _register(client, "rin@example.com")
    _verify(client)
    previous = client.cookies.get("s3_session")
    assert previous
    missing = client.post("/api/auth/forgot", json={"email": "other@example.com"})
    assert missing.status_code == 200
    outbox = client.app.state.settings.root / "outbox"
    assert list(outbox.glob("account.reset.*.txt")) == []
    sent = client.post("/api/auth/forgot", json={"email": "rin@example.com"})
    assert sent.status_code == 200
    url = _mail_link(client.app.state.settings.root, "reset")
    reset = client.post(
        "/api/auth/reset",
        json={"token": _token_from(url), "password": _NEW_PASSWORD},
    )
    assert reset.status_code == 200, reset.text
    stale = TestClient(client.app)
    stale.cookies.set("s3_session", previous)
    assert stale.get("/api/me").status_code == 401
    client.cookies.clear()
    wrong = client.post(
        "/api/auth/login",
        json={"email": "rin@example.com", "password": _PASSWORD},
    )
    assert wrong.status_code == 401
    assert wrong.json()["detail"] == BAD_LOGIN
    good = client.post(
        "/api/auth/login",
        json={"email": "rin@example.com", "password": _NEW_PASSWORD},
    )
    assert good.status_code == 200, good.text


def test_logged_in_submit_hides_guest_email_and_keeps_a_year(
    tmp_path: Path, accounts: AccountStore
) -> None:
    client = _client(tmp_path, accounts)
    _register(client, "rin@example.com")
    _verify(client)
    created = client.post(
        "/api/submissions",
        json={
            "turnstile_token": "ok",
            "filename": "match.mp4",
            "size_bytes": 8,
            "content_type": "video/mp4",
            "language": "en",
            "email": "other@example.com",
            "notify": False,
            "display_name": "",
        },
    )
    assert created.status_code == 200, created.text
    stored = client.app.state.store.list_submissions()[0]
    assert stored.email == "rin@example.com"
    assert stored.notify is True
    assert stored.display_name == "Rin"
    assert stored.user_id
    created_at = parse_utc(stored.created_at)
    assert parse_utc(stored.expires_at) - created_at == timedelta(days=365)
    listed = client.get("/api/me/submissions")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == stored.id
    assert "other@example.com" not in client.app.state.settings.root.joinpath(
        "submissions", f"{stored.id}.json"
    ).read_text()


def test_owner_share_link_opens_without_logging_in(
    tmp_path: Path, accounts: AccountStore
) -> None:
    client = _client(tmp_path, accounts)
    _register(client, "rin@example.com")
    _verify(client)
    created = client.post(
        "/api/submissions",
        json={
            "turnstile_token": "ok",
            "filename": "match.mp4",
            "size_bytes": 8,
            "content_type": "video/mp4",
            "language": "en",
        },
    )
    assert created.status_code == 200, created.text
    stored = client.app.state.store.list_submissions()[0]
    anon = TestClient(client.app)
    assert anon.post(f"/api/me/submissions/{stored.id}/share").status_code == 401
    shared = client.post(f"/api/me/submissions/{stored.id}/share")
    assert shared.status_code == 200, shared.text
    path = shared.json()["review_path"]
    token = path.rsplit("/", 1)[-1]
    record = (
        client.app.state.settings.root / "submissions" / f"{stored.id}.json"
    ).read_text()
    assert token not in record
    guest = TestClient(client.app)
    opened = guest.get(f"/api/submissions/{token}")
    assert opened.status_code == 200
    assert opened.json()["status"] == "received"


def test_history_is_private_to_the_owner(tmp_path: Path, accounts: AccountStore) -> None:
    owner = _client(tmp_path, accounts)
    _register(owner, "rin@example.com")
    _verify(owner)
    created = owner.post(
        "/api/submissions",
        json={
            "turnstile_token": "ok",
            "filename": "match.mp4",
            "size_bytes": 8,
            "content_type": "video/mp4",
        },
    )
    assert created.status_code == 200, created.text
    submission_id = owner.app.state.store.list_submissions()[0].id
    other = TestClient(owner.app)
    _register(other, "bea@example.com", name="Bea")
    _verify(other)
    assert other.get("/api/me/submissions").json() == []
    hidden = other.get(f"/api/me/submissions/{submission_id}")
    assert hidden.status_code == 404
    visible = owner.get(f"/api/me/submissions/{submission_id}")
    assert visible.status_code == 200
    assert visible.json()["display_name"] == "Rin"


def _feedback_rows(accounts: AccountStore) -> list:
    with psycopg.connect(accounts.database_url) as conn:
        return conn.execute(
            "SELECT submission_id, user_id::text, body FROM feedback ORDER BY created_at"
        ).fetchall()


def _submission(client: TestClient) -> tuple[str, str]:
    created = client.post(
        "/api/submissions",
        json={
            "turnstile_token": "ok",
            "filename": "match.mp4",
            "size_bytes": 8,
            "content_type": "video/mp4",
        },
    )
    assert created.status_code == 200, created.text
    stored = client.app.state.store.list_submissions()[0]
    return created.json()["token"], stored.id


def test_guest_feedback_is_stored_without_an_account(
    tmp_path: Path, accounts: AccountStore
) -> None:
    client = _client(tmp_path, accounts)
    assert client.get("/api/config").json()["feedback"] is True
    token, submission_id = _submission(client)
    empty = client.post(f"/api/submissions/{token}/feedback", json={"body": "  "})
    assert empty.status_code == 400
    sent = client.post(
        f"/api/submissions/{token}/feedback",
        json={"body": "The death note helped."},
    )
    assert sent.status_code == 200, sent.text
    rows = _feedback_rows(accounts)
    assert len(rows) == 1
    assert rows[0][0] == submission_id
    assert rows[0][1] is None
    assert rows[0][2] == "The death note helped."
    assert token not in rows[0][2]
    missing = client.post("/api/submissions/missing/feedback", json={"body": "no"})
    assert missing.status_code == 404


def test_owner_feedback_is_private(tmp_path: Path, accounts: AccountStore) -> None:
    owner = _client(tmp_path, accounts)
    _register(owner, "rin@example.com")
    _verify(owner)
    submission_id = _submission(owner)[1]
    sent = owner.post(
        f"/api/me/submissions/{submission_id}/feedback",
        json={"body": "More map context next time."},
    )
    assert sent.status_code == 200, sent.text
    rows = _feedback_rows(accounts)
    assert rows[0][0] == submission_id
    assert rows[0][1]
    assert rows[0][2] == "More map context next time."
    other = TestClient(owner.app)
    _register(other, "bea@example.com", name="Bea")
    _verify(other)
    hidden = other.post(
        f"/api/me/submissions/{submission_id}/feedback",
        json={"body": "Not mine."},
    )
    assert hidden.status_code == 404
    assert len(_feedback_rows(accounts)) == 1


def test_expiry_drops_account_history(tmp_path: Path, accounts: AccountStore) -> None:
    client = _client(tmp_path, accounts)
    _register(client, "rin@example.com")
    _verify(client)
    created = client.post(
        "/api/submissions",
        json={
            "turnstile_token": "ok",
            "filename": "match.mp4",
            "size_bytes": 8,
            "content_type": "video/mp4",
        },
    )
    assert created.status_code == 200, created.text
    token = created.json()["token"]
    upload = created.json()["upload"]
    stored_bytes = client.put(
        upload["url"], content=b"12345678", headers=upload["headers"]
    )
    assert stored_bytes.status_code == 200, stored_bytes.text
    queued = client.post(f"/api/submissions/{token}/uploaded")
    assert queued.status_code == 200, queued.text
    client.app.state.service.maintain(datetime.now(UTC) + timedelta(days=31))
    assert client.get("/api/me/submissions").json()
    client.app.state.service.maintain(datetime.now(UTC) + timedelta(days=366))
    assert client.get("/api/me/submissions").json() == []
    assert client.get(f"/api/submissions/{token}").json()["status"] == "expired"
    stored = client.app.state.store.list_submissions()[0]
    assert stored.user_id is None
    assert stored.email is None
