"""Guest notice confirmation and retryable notice claims."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from public_site.accounts import AccountStore
from public_site.app import create_app
from public_site.errors import BAD_ACCOUNT_EMAIL, BAD_LINK, LOGIN_REQUIRED
from public_site.media_probe import ProbeFacts
from public_site.notices import NoticeSender
from public_site.service import SubmissionService
from public_site.settings import PublicSettings
from public_site.storage import LocalStorage
from public_site.store import PublicStore
from public_site.tokens import hash_token

from splatoon3_ai_coach.config.paths import default_config_path

_PASSWORD = "correct-horse"


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


def _app(tmp_path: Path, accounts: AccountStore):
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
        submissions_per_hour=20,
        guest_submissions_per_hour=10,
        account_submissions_per_hour=10,
    )
    return create_app(
        settings,
        accounts=accounts,
        verifier=lambda token: token == "ok",
        probe=_probe_ok,
        run_command=lambda _argv: 0,
        start_worker=False,
    )


def _body(email: str, *, notify: bool = True) -> dict:
    return {
        "turnstile_token": "ok",
        "filename": "match.mp4",
        "size_bytes": 8,
        "content_type": "video/mp4",
        "language": "en",
        "email": email,
        "notify": notify,
        "display_name": "",
    }


def _submit(client: TestClient, email: str) -> dict:
    response = client.post("/api/submissions", json=_body(email))
    assert response.status_code == 200, response.text
    return response.json()


def _confirm_files(root: Path) -> list[Path]:
    return sorted((root / "outbox").glob("confirm.*.txt"))


def _confirm_token(root: Path) -> str:
    paths = _confirm_files(root)
    assert len(paths) == 1
    text = paths[0].read_text(encoding="utf-8")
    assert "/review/" not in text
    url = next(line.strip() for line in text.splitlines() if line.startswith("http"))
    assert "/confirm-notices/" in url
    return url.rstrip("/").split("/")[-1]


def _rows(database_url: str, sql: str) -> list[dict]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        return list(conn.execute(sql).fetchall())


def test_guest_notice_stays_off_the_submission_until_confirm(
    tmp_path: Path, accounts: AccountStore
) -> None:
    app = _app(tmp_path, accounts)
    client = TestClient(app)
    created = _submit(client, "Ada@Example.com")
    assert created["notice_pending"] is True
    assert "review_path" in created
    stored = client.app.state.store.list_submissions()[0]
    record = client.app.state.settings.root / "submissions" / f"{stored.id}.json"
    raw = record.read_text()
    assert stored.email is None
    assert stored.notify is False
    assert "ada@example.com" not in raw
    requests = _rows(accounts.database_url, "SELECT * FROM guest_notice_requests")
    assert requests[0]["normalized_email"] == "ada@example.com"
    assert requests[0]["submission_id"] == stored.id
    assert requests[0]["confirmed_at"] is None
    token = _confirm_token(client.app.state.settings.root)
    other = TestClient(app)
    confirmed = other.post("/api/notices/confirm", json={"token": token})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json() == {"confirmed": True}
    assert "review" not in confirmed.text
    enabled = client.app.state.store.get(stored.id)
    assert enabled is not None
    assert enabled.email == "ada@example.com"
    assert enabled.notify is True
    again = other.post("/api/notices/confirm", json={"token": token})
    assert again.status_code == 400
    assert again.json()["detail"] == BAD_LINK


def test_confirm_enables_only_recent_same_browser_requests(
    tmp_path: Path, accounts: AccountStore
) -> None:
    app = _app(tmp_path, accounts)
    client = TestClient(app)
    other = TestClient(app)
    first = _submit(client, "ada@example.com")
    second = _submit(client, "ada@example.com")
    assert second["notice_pending"] is True
    root = client.app.state.settings.root
    assert len(_confirm_files(root)) == 1
    outsider = _submit(other, "ada@example.com")
    assert len(_confirm_files(root)) == 1
    fresh = client.app.state.store.get_by_hash(hash_token(first["token"]))
    stale = client.app.state.store.get_by_hash(hash_token(second["token"]))
    assert fresh is not None and stale is not None
    stale_id = stale.id
    paired = _rows(accounts.database_url, "SELECT * FROM guest_notice_requests")
    same_browser = {
        str(row["browser_id"])
        for row in paired
        if row["submission_id"] in {fresh.id, stale.id}
    }
    assert len(same_browser) == 1
    old = datetime.now(UTC) - timedelta(hours=25)
    with psycopg.connect(accounts.database_url) as conn:
        conn.execute(
            "UPDATE guest_notice_requests SET created_at = %s WHERE submission_id = %s",
            (old, stale_id),
        )
    token = _confirm_token(root)
    assert client.post("/api/notices/confirm", json={"token": token}).status_code == 200
    fresh = client.app.state.store.get_by_hash(hash_token(first["token"]))
    skipped = client.app.state.store.get(stale_id)
    foreign = other.app.state.store.get_by_hash(hash_token(outsider["token"]))
    assert fresh is not None and fresh.email == "ada@example.com" and fresh.notify is True
    assert skipped is not None and skipped.email is None and skipped.notify is False
    assert foreign is not None and foreign.email is None and foreign.notify is False


def test_confirming_a_finished_review_sends_ready_once(
    tmp_path: Path, accounts: AccountStore
) -> None:
    client = TestClient(_app(tmp_path, accounts))
    created = _submit(client, "ada@example.com")
    submission = client.app.state.store.get_by_hash(hash_token(created["token"]))
    assert submission is not None
    submission.status = "complete"
    submission.outcome = "coached"
    client.app.state.store.save_submission(submission)
    root = client.app.state.settings.root
    token = _confirm_token(root)
    assert client.post("/api/notices/confirm", json={"token": token}).status_code == 200
    ready = root / "outbox" / f"{submission.id}.ready.txt"
    text = ready.read_text(encoding="utf-8")
    assert "/review/" in text
    assert "confirm-notices" not in text
    sent = client.app.state.store.get(submission.id)
    assert sent is not None and sent.notices_sent == ["ready"]
    client.app.state.service.maintain(datetime.now(UTC))
    assert sent.notices_sent == ["ready"]
    assert list((root / "outbox").glob(f"{submission.id}.ready.txt")) == [ready]


def test_verified_browser_skips_the_confirmation_mail(
    tmp_path: Path, accounts: AccountStore
) -> None:
    client = TestClient(_app(tmp_path, accounts))
    _submit(client, "ada@example.com")
    root = client.app.state.settings.root
    token = _confirm_token(root)
    assert client.post("/api/notices/confirm", json={"token": token}).status_code == 200
    again = _submit(client, "ada@example.com")
    assert again["notice_pending"] is False
    assert len(_confirm_files(root)) == 1
    stored = client.app.state.store.get_by_hash(hash_token(again["token"]))
    assert stored is not None
    assert stored.email == "ada@example.com"
    assert stored.notify is True


def test_browser_rate_limit_uses_the_browser_row(
    tmp_path: Path, accounts: AccountStore
) -> None:
    client = TestClient(_app(tmp_path, accounts))
    first = _submit(client, "ada@example.com")
    second = _submit(client, "bea@example.com")
    assert first["notice_pending"] is True
    assert second["notice_pending"] is True
    root = client.app.state.settings.root
    assert len(_confirm_files(root)) == 1
    cookie = client.cookies.get("notice_browser")
    assert cookie
    browsers = _rows(accounts.database_url, "SELECT * FROM guest_notice_browsers")
    challenges = _rows(accounts.database_url, "SELECT * FROM guest_email_challenges")
    assert len(browsers) == 1
    assert len(challenges) == 1
    assert str(challenges[0]["browser_id"]) == str(browsers[0]["id"])
    assert cookie != str(browsers[0]["id"])
    assert hash_token(cookie) == browsers[0]["browser_token_hash"]
    assert challenges[0]["normalized_email"] == "ada@example.com"
    token = _confirm_token(root)
    assert client.post("/api/notices/confirm", json={"token": token}).status_code == 200
    enabled = client.app.state.store.get_by_hash(hash_token(first["token"]))
    waiting = client.app.state.store.get_by_hash(hash_token(second["token"]))
    assert enabled is not None and enabled.email == "ada@example.com"
    assert waiting is not None and waiting.email is None and waiting.notify is False


def test_bad_and_expired_confirmation_links_stay_generic(
    tmp_path: Path, accounts: AccountStore
) -> None:
    client = TestClient(_app(tmp_path, accounts))
    missing = client.post("/api/notices/confirm", json={"token": "missing"})
    assert missing.status_code == 400
    assert missing.json()["detail"] == BAD_LINK
    assert "review" not in missing.text
    created = _submit(client, "ada@example.com")
    with psycopg.connect(accounts.database_url) as conn:
        conn.execute(
            "UPDATE guest_email_challenges SET expires_at = %s",
            (datetime.now(UTC) - timedelta(minutes=1),),
        )
    token = _confirm_token(client.app.state.settings.root)
    expired = client.post("/api/notices/confirm", json={"token": token})
    assert expired.status_code == 400
    assert expired.json()["detail"] == BAD_LINK
    stored = client.app.state.store.get_by_hash(hash_token(created["token"]))
    assert stored is not None and stored.email is None
    rejected = client.post("/api/submissions", json=_body("not-an-email"))
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == BAD_ACCOUNT_EMAIL


def test_account_notice_preference_is_not_changed_by_one_submission(
    tmp_path: Path, accounts: AccountStore
) -> None:
    client = TestClient(_app(tmp_path, accounts))
    anonymous = client.put("/api/me/notice-preference", json={"enabled": False})
    assert anonymous.status_code == 401
    assert anonymous.json()["detail"] == LOGIN_REQUIRED
    registered = client.post(
        "/api/auth/register",
        json={"name": "Rin", "email": "rin@example.com", "password": _PASSWORD},
    )
    assert registered.status_code == 200, registered.text
    outbox = client.app.state.settings.root / "outbox"
    verify = sorted(outbox.glob("account.verify.*.txt"))
    lines = verify[-1].read_text().splitlines()
    link = next(line.strip() for line in lines if line.startswith("http"))
    token = link.rstrip("/").split("/")[-1]
    checked = client.post("/api/auth/verify", json={"token": token})
    assert checked.status_code == 200
    assert client.get("/api/me").json()["notify_by_email"] is True
    opted_out = client.post(
        "/api/submissions", json=_body("other@example.com", notify=False)
    )
    assert opted_out.status_code == 200, opted_out.text
    stored = client.app.state.store.list_submissions()[0]
    assert stored.email == "rin@example.com"
    assert stored.notify is False
    assert client.get("/api/me").json()["notify_by_email"] is True
    saved = client.put(
        "/api/me/notice-preference",
        json={"enabled": False, "email": "other@example.com"},
    )
    assert saved.status_code == 200
    assert saved.json() == {"notify_by_email": False}
    opted_in = client.post(
        "/api/submissions", json=_body("other@example.com", notify=True)
    )
    assert opted_in.status_code == 200, opted_in.text
    assert client.get("/api/me").json()["notify_by_email"] is False
    latest = client.app.state.store.get_by_hash(hash_token(opted_in.json()["token"]))
    assert latest is not None
    assert latest.notify is True
    assert latest.email == "rin@example.com"


class _Flaky(NoticeSender):
    """Fail the first delivery, then write the outbox."""

    def __init__(self, settings: PublicSettings) -> None:
        super().__init__(settings)
        self.failures_left = 1
        self.attempts = 0

    def deliver(self, submission, kind: str, token: str) -> None:
        self.attempts += 1
        if self.failures_left:
            self.failures_left -= 1
            raise OSError("smtp down")
        super().deliver(submission, kind, token)


def test_failed_notice_claim_can_be_retried(tmp_path: Path) -> None:
    root = tmp_path / "public"
    root.mkdir()
    settings = PublicSettings(
        root=root,
        config_path=default_config_path(),
        static_dir=tmp_path / "no-dist",
        dev_mode=True,
        worker_id="worker-test",
        public_base_url="http://coach.example",
        max_video_bytes=1000,
    )
    notices = _Flaky(settings)
    service = SubmissionService(settings, PublicStore(root), LocalStorage(root), notices)
    service.create(
        turnstile_token="ok",
        filename="match.mp4",
        size_bytes=8,
        content_type="video/mp4",
        language="en",
        email="player@example.com",
        notify=True,
        display_name=None,
        ip="127.0.0.1",
        verifier=lambda _token: True,
    )
    submission = service.store.list_submissions()[0]
    submission.status = "complete"
    submission.outcome = "coached"
    now = datetime.now(UTC)
    service.store.save_submission(submission)
    service.note_terminal(submission, "ready")
    claimed = service.store.get(submission.id)
    assert claimed is not None
    assert claimed.notices_sent == []
    assert "ready" in claimed.notice_claims
    assert notices.attempts == 1
    assert list(notices.outbox.glob("*.ready.txt")) == []
    service.maintain(now)
    assert notices.attempts == 1
    claimed.notice_claims["ready"] = "2000-01-01T00:00:00Z"
    service.store.save_submission(claimed)
    service.maintain(datetime.now(UTC))
    sent = service.store.get(submission.id)
    assert sent is not None
    assert sent.notices_sent == ["ready"]
    assert "ready" not in sent.notice_claims
    assert notices.attempts == 2
    assert (notices.outbox / f"{submission.id}.ready.txt").is_file()
    assert not (notices.secrets / submission.id).is_file()
    service.maintain(datetime.now(UTC))
    assert notices.attempts == 2
