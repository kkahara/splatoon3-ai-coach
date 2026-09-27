"""Public monitor output stays free of review secrets."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from public_site.models import Submission, VideoInput
from public_site.monitor import render_monitor
from public_site.settings import PublicSettings
from public_site.store import PublicStore
from typer.testing import CliRunner

from splatoon3_ai_coach.cli.app import app as cli_app
from splatoon3_ai_coach.config.paths import default_config_path

_TOKEN = "review-token-must-not-appear"
_EMAIL = "player@example.com"


def test_monitor_omits_secrets_and_idle_elapsed(
    tmp_path,
    monkeypatch,
) -> None:
    root = tmp_path / "public"
    root.mkdir()
    now = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    started = (now - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    created = "2026-09-27T11:00:00Z"
    store = PublicStore(root)
    processing = "a" * 32
    queued = "b" * 32
    store.save_submission(
        Submission(
            id=processing,
            access_token_hash="ab" * 32,
            email=_EMAIL,
            notify=True,
            status="processing",
            step="analyzing",
            created_at=created,
            updated_at=created,
            expires_at="2026-10-27T11:00:00Z",
            processing_started_at=started,
        )
    )
    store.save_video(
        VideoInput(
            submission_id=processing,
            object_key=f"submissions/{processing}/source.mp4",
            original_filename="match.mp4",
            size_bytes=2048,
            content_type="video/mp4",
            duration_seconds=125,
        )
    )
    (root / "objects" / "submissions" / processing).mkdir(parents=True)
    (root / "objects" / f"submissions/{processing}/source.mp4").write_bytes(b"1234")
    store.save_submission(
        Submission(
            id=queued,
            access_token_hash="cd" * 32,
            status="queued",
            step="queued",
            created_at=created,
            updated_at=created,
            expires_at="2026-10-27T11:00:00Z",
        )
    )
    (root / "notice_secrets").mkdir()
    (root / "notice_secrets" / processing).write_text(_TOKEN, encoding="utf-8")
    settings = PublicSettings(
        root=root,
        config_path=default_config_path(),
        static_dir=tmp_path / "dist",
        dev_mode=True,
    )
    report = render_monitor(settings, now=now)
    assert _TOKEN not in report
    assert _EMAIL not in report
    assert "/review/" not in report
    assert "1m 30s" in report
    queued_line = next(line for line in report.splitlines() if line.startswith(queued))
    assert "—" in queued_line
    assert "1m 30s" not in queued_line
    assert "Videos:" in report
    assert "Analysis:" in report
    assert "Free:" in report
    assert "%" in report.split("Free:", 1)[1].splitlines()[0]
    monkeypatch.setenv("PUBLIC_ROOT", str(root))
    monkeypatch.setenv("PUBLIC_DEV_MODE", "1")
    result = CliRunner().invoke(cli_app, ["public-monitor"])
    assert result.exit_code == 0
    assert _EMAIL not in result.stdout
    assert "counts" in result.stdout
