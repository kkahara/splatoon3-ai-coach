"""Public submissions: token privacy, upload checks, worker, and notices."""

from __future__ import annotations

import json
import math
import subprocess
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from coach_artifact_helpers import index_entry, video_durations, write_prototype_unit
from fastapi.testclient import TestClient
from loguru import logger
from public_site.app import create_app
from public_site.errors import (
    ANALYSIS_FAILED,
    CAPACITY,
    COACHING_FAILED,
    EXPIRED,
    INTERRUPTED,
    PROBE_CONTAINER,
    PROBE_DIMENSIONS,
    PROBE_DURATION,
    PROBE_STREAM,
    RATE_LIMIT,
    UPLOAD_MISSING,
    UPLOAD_UNFINISHED,
)
from public_site.frames import extract_frames, ffmpeg_argv, frame_seek, moment_seek
from public_site.media_probe import ProbeFacts, ProbeRejected, interpret_probe
from public_site.models import Submission
from public_site.results import load_result
from public_site.s3sign import presign
from public_site.settings import PublicSettings
from public_site.share_page import review_html
from public_site.storage import LocalStorage, R2Storage
from public_site.store import PublicStore, add_days, utc_now
from public_site.worker import PublicWorker
from typer.testing import CliRunner

from splatoon3_ai_coach.cli.app import app as cli_app
from splatoon3_ai_coach.cli.coach_inputs import NO_COACHING_UNITS_EXIT_CODE
from splatoon3_ai_coach.coach.artifacts import CoachingIndexEntry
from splatoon3_ai_coach.coach.llm_runs import empty_coaching_assessment
from splatoon3_ai_coach.config.paths import default_config_path

STATEMENT = "No map overlay was observed before death."
_AWS_SIGNATURE = "aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"


def _settings(tmp_path: Path, **overrides) -> PublicSettings:
    root = tmp_path / "public"
    root.mkdir(parents=True, exist_ok=True)
    values = {
        "root": root,
        "config_path": default_config_path(),
        "static_dir": tmp_path / "no-dist",
        "dev_mode": True,
        "worker_id": "worker-test",
        "public_base_url": "http://coach.example",
        "max_video_bytes": 1000,
    }
    values.update(overrides)
    return PublicSettings(**values)


def _probe_ok(_path: Path) -> ProbeFacts:
    return ProbeFacts(12.0, 1920, 1080, "mp4")


def _client(tmp_path: Path, **kwargs) -> TestClient:
    probe = kwargs.pop("probe", _probe_ok)
    run_command = kwargs.pop("run_command", lambda _argv: 0)
    storage = kwargs.pop("storage", None)
    telemetry = kwargs.pop("telemetry", None)
    settings = _settings(tmp_path, **kwargs)
    store = PublicStore(settings.root)
    app = create_app(
        settings,
        store=store,
        storage=storage,
        verifier=lambda token: token == "ok",
        probe=probe,
        run_command=run_command,
        start_worker=False,
        telemetry=telemetry,
    )
    client = TestClient(app)
    client.app = app
    return client


def _create(client: TestClient, **extra) -> dict:
    body = {
        "turnstile_token": "ok",
        "filename": "match.mp4",
        "size_bytes": 8,
        "content_type": "video/mp4",
        "language": "en",
    }
    body.update(extra)
    response = client.post("/api/submissions", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _put(client: TestClient, created: dict, content: bytes = b"12345678") -> str:
    upload = created["upload"]
    response = client.put(upload["url"], content=content, headers=upload["headers"])
    assert response.status_code == 200, response.text
    return created["token"]


def test_turnstile_failure_writes_nothing(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post(
        "/api/submissions",
        json={
            "turnstile_token": "nope",
            "filename": "match.mp4",
            "size_bytes": 8,
            "content_type": "video/mp4",
            "language": "en",
        },
    )
    assert response.status_code == 400
    root = client.app.state.settings.root
    assert list((root / "submissions").glob("*.json")) == []
    assert not (root / "rate_limits.json").is_file()


def test_missing_game_language_is_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post(
        "/api/submissions",
        json={
            "turnstile_token": "ok",
            "filename": "match.mp4",
            "size_bytes": 8,
            "content_type": "video/mp4",
        },
    )
    assert response.status_code == 422
    root = client.app.state.settings.root
    assert list((root / "submissions").glob("*.json")) == []


@pytest.mark.parametrize(
    ("game_language", "site_locale"),
    [("en", "ja"), ("ja", "en")],
)
def test_site_locale_never_selects_the_vision_language(
    tmp_path: Path, game_language: str, site_locale: str
) -> None:
    calls: list[list[str]] = []
    client = _client(
        tmp_path, run_command=lambda argv: calls.append(argv) or 0
    )
    created = _create(client, language=game_language)
    token = _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    assert client.get(f"/api/submissions/{token}?locale={site_locale}").status_code == 200
    client.app.state.worker.pump()
    client.app.state.worker.join()
    analyze = next(argv for argv in calls if "analyze" in argv)
    assert analyze[analyze.index("--language") + 1] == game_language


def test_token_is_hashed_and_absent_from_json(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = _create(client, notify=True, email="player@example.com", display_name="Rin")
    token = created["token"]
    assert token not in created["upload"]["url"]
    root = client.app.state.settings.root
    blob = "\n".join(path.read_text() for path in root.rglob("*.json"))
    assert token not in blob
    assert "player@example.com" in blob
    stored = client.app.state.store.list_submissions()[0]
    assert stored.access_token_hash != token
    assert stored.input_type == "video"


def test_review_code_is_not_a_route_or_a_stored_input(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = _create(client, review_code="ABCD-1234", input_type="review_code")
    assert created["review_path"].endswith(created["token"])
    stored = client.app.state.store.list_submissions()[0]
    assert stored.input_type == "video"
    assert stored.source == "upload"
    paths = client.app.openapi()["paths"]
    assert not any("review_code" in path or "review-code" in path for path in paths)
    assert client.get("/api/submissions").status_code in {404, 405}
    assert client.get(f"/api/submissions/{stored.id}").status_code == 404


def test_rate_limit_and_queue_cap(tmp_path: Path) -> None:
    client = _client(tmp_path, max_queued=2)
    _create(client)
    _create(client)
    limited = client.post(
        "/api/submissions",
        json={
            "turnstile_token": "ok",
            "filename": "match.mp4",
            "size_bytes": 8,
            "content_type": "video/mp4",
            "language": "en",
        },
    )
    assert limited.status_code == 429
    assert limited.json()["detail"] == RATE_LIMIT
    rate = (client.app.state.settings.root / "rate_limits.json").read_text()
    assert "testclient" not in rate

    capped = _client(tmp_path / "cap", max_queued=1)
    _create(capped)
    again = capped.post(
        "/api/submissions",
        json={
            "turnstile_token": "ok",
            "filename": "other.mov",
            "size_bytes": 4,
            "content_type": "video/quicktime",
            "language": "ja",
        },
    )
    assert again.status_code == 429
    assert again.json()["detail"] == CAPACITY


def test_confirm_checks_the_object_before_queueing(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = _create(client)
    missing = client.post(f"/api/submissions/{created['token']}/uploaded")
    assert missing.status_code == 400
    assert missing.json()["detail"] == UPLOAD_MISSING
    assert client.app.state.store.list_submissions()[0].status == "received"

    short = _put(client, created, content=b"1234")
    mismatch = client.post(f"/api/submissions/{short}/uploaded")
    assert mismatch.status_code == 400
    assert client.app.state.store.list_submissions()[0].status == "received"

    owned = _client(tmp_path / "owned")
    created = _create(owned)
    _put(owned, created)
    submission = owned.app.state.store.list_submissions()[0]
    video = owned.app.state.store.get_video(submission.id)
    video.object_key = "submissions/ffffffffffffffffffffffffffffffff/source.mp4"
    other = owned.app.state.settings.root / "objects" / video.object_key
    other.parent.mkdir(parents=True)
    other.write_bytes(b"12345678")
    owned.app.state.store.save_video(video)
    rejected = owned.post(f"/api/submissions/{created['token']}/uploaded")
    assert rejected.status_code == 400
    assert owned.app.state.store.list_submissions()[0].status == "received"


def test_probe_failure_does_not_take_a_slot(tmp_path: Path) -> None:
    def reject(_path: Path) -> ProbeFacts:
        raise ProbeRejected(PROBE_DURATION)

    client = _client(tmp_path, probe=reject)
    created = _create(client)
    token = _put(client, created)
    response = client.post(f"/api/submissions/{token}/uploaded")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] == PROBE_DURATION
    assert body["result"] is None
    submission = client.app.state.store.list_submissions()[0]
    video = client.app.state.store.get_video(submission.id)
    assert client.app.state.service.storage.head(video.object_key) is None


def test_public_view_hides_internal_fields(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> int:
        calls.append(argv)
        if "coach-inputs" in argv:
            _write_coaching(Path(argv[argv.index("coach-inputs") + 1]))
        if "coach-prototype" in argv:
            _write_assessment(Path(argv[argv.index("coach-prototype") + 1]))
        return 0

    client = _client(tmp_path, run_command=run)
    created = _create(client, display_name="Rin")
    token = _put(client, created)
    queued = client.post(f"/api/submissions/{token}/uploaded")
    assert queued.json()["status"] == "queued"
    assert queued.json()["step"] == "queued"
    assert client.app.state.worker.pump() is True
    client.app.state.worker.join()
    response = client.get(f"/api/submissions/{token}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "complete"
    assert body["display_name"] == "Rin"
    moment = body["result"]["moments"][0]
    assert moment["statements"] == [STATEMENT]
    assert moment["video_time"] == 72.5
    assert moment["scenario_type"] == "death_episode"
    prose = "The player did not check the map overlay before death."
    assert moment["assessment"] == prose
    text = response.text
    for hidden in (
        "analysis_dir",
        "object_key",
        "worker_id",
        "access_token",
        "email",
        "user_prompt",
        "system_prompt",
        empty_coaching_assessment().assessment,
    ):
        assert hidden not in text
    assert calls[0][calls[0].index("analyze") + 0] == "analyze"
    assert "--call-llm" in calls[2]
    stored = client.app.state.store.list_submissions()[0]
    assert stored.worker_id == "worker-test"
    assert stored.processing_started_at


def test_startup_fails_in_flight_without_running_the_cli(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = PublicStore(settings.root)
    now = utc_now()
    processing = _record(
        status="processing",
        worker_id="old-worker",
        started=now,
        now=now,
    )
    validating = _record(status="validating", now=now)
    queued = _record(status="queued", now=now)
    for item in (processing, validating, queued):
        store.save_submission(item)
    calls: list[list[str]] = []
    PublicWorker(
        settings,
        store,
        create_app(
            settings,
            store=store,
            verifier=lambda _token: False,
            start_worker=False,
            run_command=lambda argv: calls.append(argv) or 0,
        ).state.service,
        run_command=lambda argv: calls.append(argv) or 0,
        start_thread=False,
    )
    assert store.get(processing.id).status == "failed"
    assert store.get(processing.id).error == INTERRUPTED
    assert store.get(processing.id).worker_id == "old-worker"
    assert store.get(validating.id).status == "failed"
    assert store.get(queued.id).status == "queued"
    assert calls == []


def test_analyze_failure_skips_later_stages(tmp_path: Path) -> None:
    calls: list[str] = []

    def run(argv: list[str]) -> int:
        if "analyze" in argv:
            calls.append("analyze")
            return 1
        calls.append(argv[1] if len(argv) > 1 else "")
        return 0

    client = _client(tmp_path, run_command=run)
    created = _create(client)
    token = _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    assert client.app.state.worker.pump() is True
    client.app.state.worker.join()
    stored = client.app.state.store.list_submissions()[0]
    assert stored.status == "failed"
    assert stored.error == ANALYSIS_FAILED
    assert calls == ["analyze"]


def test_llm_failure_still_completes_when_inputs_exist(tmp_path: Path) -> None:
    def run(argv: list[str]) -> int:
        if "coach-inputs" in argv:
            _write_coaching(Path(argv[argv.index("coach-inputs") + 1]))
            return 0
        if "coach-prototype" in argv:
            return 1
        return 0

    client = _client(tmp_path, run_command=run)
    created = _create(client)
    token = _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    client.app.state.worker.pump()
    client.app.state.worker.join()
    body = client.get(f"/api/submissions/{token}").json()
    assert body["status"] == "complete"
    assert body["result"]["moments"][0]["statements"] == [STATEMENT]
    assert body["result"]["moments"][0]["assessment"] is None


def test_notices_keep_the_token_out_of_the_subject_and_logs(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = _create(client, notify=True, email="player@example.com")
    token = _put(client, created)
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)))
    try:
        client.post(f"/api/submissions/{token}/uploaded")
    finally:
        logger.remove(sink)
    root = client.app.state.settings.root
    outbox = list((root / "outbox").glob("*.txt"))
    assert len(outbox) == 1
    submission_id = client.app.state.store.list_submissions()[0].id
    assert outbox[0].name == f"{submission_id}.received.txt"
    mail = outbox[0].read_text().splitlines()
    assert mail[1] == "Subject: We received your Splatoon 3 match"
    assert token not in mail[1]
    assert "View your review" in outbox[0].read_text()
    assert all(token not in line for line in lines)
    assert any("notice received sent" in line for line in lines)


def test_expiry_deletes_media_and_email(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = _create(client, notify=True, email="player@example.com", display_name="Rin")
    token = _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    later = datetime.now(UTC) + timedelta(days=31)
    client.app.state.service.maintain(later)
    stored = client.app.state.store.list_submissions()[0]
    assert stored.status == "expired"
    assert stored.email is None
    assert stored.display_name is None
    assert stored.access_token_hash
    video = client.app.state.store.get_video(stored.id)
    assert video.object_key == ""
    body = client.get(f"/api/submissions/{token}").json()
    assert body["status"] == "expired"
    assert body["error"] == EXPIRED
    assert "player@example.com" not in client.get(f"/api/submissions/{token}").text


def test_abandoned_upload_releases_the_queue(tmp_path: Path) -> None:
    client = _client(tmp_path, upload_url_seconds=60)
    _create(client)
    later = datetime.now(UTC) + timedelta(minutes=5)
    client.app.state.service.maintain(later)
    stored = client.app.state.store.list_submissions()[0]
    assert stored.status == "failed"
    assert stored.error == UPLOAD_UNFINISHED


def test_r2_mode_has_no_dev_upload_route(tmp_path: Path) -> None:
    storage = R2Storage(
        account_id="account",
        access_key_id="key",
        secret_access_key="secret",
        bucket="bucket",
    )
    client = _client(tmp_path, storage=storage)
    assert "/api/dev-upload/{grant_id}" not in client.app.openapi()["paths"]
    assert client.put("/api/dev-upload/abcdefghijklmnop").status_code == 404


def test_interpret_probe_accepts_a_normal_mp4_and_rejects_limits(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    payload = {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "90.5"},
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "codec_name": "av1",
            }
        ],
    }
    facts = interpret_probe(payload, settings)
    assert facts.duration_seconds == 90.5
    payload["format"]["duration"] = "1801"
    _raises(payload, settings, PROBE_DURATION)
    payload["format"]["duration"] = "10"
    payload["streams"][0]["width"] = 100
    _raises(payload, settings, PROBE_DIMENSIONS)
    payload["streams"] = [{"codec_type": "audio"}]
    _raises(payload, settings, PROBE_STREAM)
    payload["streams"] = [{"codec_type": "video", "width": 1280, "height": 720}]
    payload["format"]["format_name"] = "matroska"
    _raises(payload, settings, PROBE_CONTAINER)


def test_complete_deletes_the_source_video_outside_dev_mode(tmp_path: Path) -> None:
    client = _client(tmp_path, dev_mode=False, run_command=_finish_coaching)
    created = _create(client)
    token = _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    assert client.app.state.worker.pump() is True
    client.app.state.worker.join()
    stored = client.app.state.store.list_submissions()[0]
    video = client.app.state.store.get_video(stored.id)
    assert stored.status == "complete"
    assert video.object_key == ""
    assert client.app.state.service.storage.head(
        f"submissions/{stored.id}/source.mp4"
    ) is None
    assert client.get(f"/api/submissions/{token}").json()["status"] == "complete"


def test_dev_mode_keeps_the_source_video(tmp_path: Path) -> None:
    client = _client(tmp_path, run_command=_finish_coaching)
    created = _create(client)
    token = _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    client.app.state.worker.pump()
    client.app.state.worker.join()
    stored = client.app.state.store.list_submissions()[0]
    video = client.app.state.store.get_video(stored.id)
    assert video.object_key.endswith("source.mp4")
    assert client.app.state.service.storage.head(video.object_key) is not None


def test_failed_analysis_keeps_the_source_video(tmp_path: Path) -> None:
    def fail_analyze(argv: list[str]) -> int:
        return 1 if "analyze" in argv else 0

    client = _client(tmp_path, dev_mode=False, run_command=fail_analyze)
    created = _create(client)
    token = _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    client.app.state.worker.pump()
    client.app.state.worker.join()
    stored = client.app.state.store.list_submissions()[0]
    video = client.app.state.store.get_video(stored.id)
    assert stored.status == "failed"
    assert video.object_key.endswith("source.mp4")
    assert client.app.state.service.storage.head(video.object_key) is not None


def test_no_deaths_completes_with_an_empty_outcome(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def no_units(argv: list[str]) -> int:
        calls.append(argv)
        return NO_COACHING_UNITS_EXIT_CODE if "coach-inputs" in argv else 0

    client = _client(tmp_path, dev_mode=False, run_command=no_units)
    created = _create(client, notify=True, email="player@example.com")
    token = _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    client.app.state.worker.pump()
    client.app.state.worker.join()
    stored = client.app.state.store.list_submissions()[0]
    assert stored.status == "complete"
    assert stored.outcome == "no_deaths"
    assert stored.error is None
    assert "no_moments" in stored.notices_sent
    assert not any("coach-prototype" in argv for argv in calls)
    view = client.get(f"/api/submissions/{token}").json()
    assert view["status"] == "complete"
    assert view["outcome"] == "no_deaths"
    assert view["error"] is None
    assert view["result"] is None


def test_coached_submission_records_coached_outcome(tmp_path: Path) -> None:
    client = _client(tmp_path, run_command=_finish_coaching)
    created = _create(client)
    token = _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    client.app.state.worker.pump()
    client.app.state.worker.join()
    stored = client.app.state.store.list_submissions()[0]
    assert stored.status == "complete"
    assert stored.outcome == "coached"


def test_coach_inputs_crash_stays_coaching_failed(tmp_path: Path) -> None:
    def crash(argv: list[str]) -> int:
        return 1 if "coach-inputs" in argv else 0

    client = _client(tmp_path, dev_mode=False, run_command=crash)
    created = _create(client)
    token = _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    client.app.state.worker.pump()
    client.app.state.worker.join()
    stored = client.app.state.store.list_submissions()[0]
    assert stored.error == COACHING_FAILED


def test_failed_video_delete_keeps_the_review(tmp_path: Path) -> None:
    settings = _settings(tmp_path, dev_mode=False)
    storage = _StickyStorage(settings.root)
    app = create_app(
        settings,
        storage=storage,
        verifier=lambda token: token == "ok",
        probe=_probe_ok,
        run_command=_finish_coaching,
        start_worker=False,
    )
    client = TestClient(app)
    created = _create(client, notify=True, email="player@example.com")
    token = _put(client, created)
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)))
    try:
        client.post(f"/api/submissions/{token}/uploaded")
        assert app.state.worker.pump() is True
        app.state.worker.join()
    finally:
        logger.remove(sink)
    stored = app.state.store.list_submissions()[0]
    video = app.state.store.get_video(stored.id)
    body = client.get(f"/api/submissions/{token}").json()
    assert body["status"] == "complete"
    assert body["result"]["moments"]
    assert video.object_key.endswith("source.mp4")
    assert storage.head(video.object_key) is not None
    assert any("source video delete failed" in line for line in lines)
    assert token not in "\n".join(lines)
    storage.fail = False
    app.state.service.maintain(datetime.now(UTC) + timedelta(days=31))
    expired = app.state.store.get_video(stored.id)
    assert expired.object_key == ""
    assert storage.head(f"submissions/{stored.id}/source.mp4") is None


class _StickyStorage(LocalStorage):
    """Delete fails until ``fail`` is cleared, as a transient object store would."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.fail = True

    def delete(self, key: str) -> None:
        if self.fail:
            raise OSError("unavailable")
        super().delete(key)


def _finish_coaching(argv: list[str]) -> int:
    if "coach-inputs" in argv:
        _write_coaching(Path(argv[argv.index("coach-inputs") + 1]))
    return 0


class _TelemetryRecorder:
    def __init__(self) -> None:
        self.events: list[str] = []

    def _record(self, name: str) -> bool:
        self.events.append(name)
        return True

    def open_initial_run(self, *_args, **_kwargs) -> str:
        self._record("submitted")
        return "run"

    def upload_confirmed(self, *_args, **_kwargs) -> bool:
        return self._record("upload_confirmed")

    def validation_started(self, *_args, **_kwargs) -> bool:
        return self._record("validation_started")

    def queued(self, *_args, **_kwargs) -> bool:
        return self._record("queued")

    def started(self, *_args, **_kwargs) -> bool:
        return self._record("started")

    def stage_started(self, _submission_id, stage) -> bool:
        return self._record(f"{stage.value}:started")

    def stage_finished(self, _submission_id, stage, **_kwargs) -> bool:
        return self._record(f"{stage.value}:finished")

    def update_artifacts(self, *_args, **_kwargs) -> bool:
        return True

    def completed(self, *_args, **_kwargs) -> bool:
        return self._record("completed")

    def failed(self, *_args, **_kwargs) -> bool:
        return self._record("failed")

    def interrupted(self, *_args, **_kwargs) -> bool:
        return self._record("interrupted")

    def unlink_user(self, *_args, **_kwargs) -> bool:
        return self._record("expired")


def test_worker_top_level_telemetry_is_serial_and_frames_finish_first(
    tmp_path: Path,
) -> None:
    telemetry = _TelemetryRecorder()
    client = _client(
        tmp_path,
        run_command=_finish_coaching,
        telemetry=telemetry,
    )
    created = _create(client)
    token = _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    assert client.app.state.worker.pump() is True
    client.app.state.worker.join()

    worker_events = telemetry.events[telemetry.events.index("started") :]
    assert worker_events == [
        "started",
        "analyze:started",
        "analyze:finished",
        "coach_inputs:started",
        "coach_inputs:finished",
        "coach_prototype:started",
        "coach_prototype:finished",
        "frames:started",
        "frames:finished",
        "completed",
    ]


def test_prototype_llm_can_select_a_provider(tmp_path: Path) -> None:
    from public_site.worker import prototype_extra

    plain = _settings(tmp_path)
    assert prototype_extra(plain) == ["--call-llm"]
    nvidia = _settings(tmp_path / "nvidia", llm_provider="nvidia")
    assert prototype_extra(nvidia) == ["--call-llm", "--provider", "nvidia"]


def test_presign_matches_the_aws_query_string_example() -> None:
    url = presign(
        method="GET",
        host="examplebucket.s3.amazonaws.com",
        canonical_uri="/test.txt",
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        service="s3",
        now=datetime(2013, 5, 24, tzinfo=UTC),
        expires=86400,
        signed_headers={},
    )
    assert _AWS_SIGNATURE in url


def test_dev_mode_keeps_regular_upload_limits(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PUBLIC_DEV_MODE", "1")
    monkeypatch.setenv("PUBLIC_ROOT", str(tmp_path))
    monkeypatch.delenv("PUBLIC_MAX_VIDEO_BYTES", raising=False)
    monkeypatch.delenv("PUBLIC_VIDEO_PER_HOUR", raising=False)
    monkeypatch.delenv("PUBLIC_VIDEO_WINDOW_SECONDS", raising=False)
    for name in (
        "PUBLIC_GUEST_VIDEO_PER_HOUR",
        "PUBLIC_ACCOUNT_VIDEO_PER_HOUR",
        "PUBLIC_ACCOUNTS_PER_IP",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = PublicSettings.from_env()
    assert settings.max_video_bytes == 750 * 1024 * 1024
    assert settings.submissions_per_hour == 6
    assert settings.guest_submissions_per_hour == 2
    assert settings.account_submissions_per_hour == 5
    assert settings.submission_window_seconds == 3600
    assert settings.accounts_per_ip == 50
    monkeypatch.delenv("PUBLIC_DEV_MODE", raising=False)
    production = PublicSettings.from_env()
    assert production.max_video_bytes == 750 * 1024 * 1024
    assert production.submissions_per_hour == 6
    assert production.guest_submissions_per_hour == 2
    assert production.account_submissions_per_hour == 5
    assert production.submission_window_seconds == 3600
    assert production.accounts_per_ip == 3


def test_public_site_refuses_to_start_without_turnstile(monkeypatch) -> None:
    monkeypatch.delenv("PUBLIC_TURNSTILE_SECRET", raising=False)
    monkeypatch.delenv("PUBLIC_DEV_MODE", raising=False)
    result = CliRunner().invoke(cli_app, ["public-site", "--no-open"])
    assert result.exit_code != 0
    assert "TURNSTILE" in result.output


def _raises(payload: dict, settings: PublicSettings, detail: str) -> None:
    try:
        interpret_probe(payload, settings)
    except ProbeRejected as exc:
        assert exc.detail == detail
        return
    raise AssertionError(detail)


def _record(
    status: str,
    now: str,
    worker_id: str | None = None,
    started: str | None = None,
) -> Submission:
    return Submission(
        id=uuid.uuid4().hex,
        access_token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        status=status,  # type: ignore[arg-type]
        created_at=now,
        updated_at=now,
        expires_at=add_days(now, 30),
        worker_id=worker_id,
        processing_started_at=started,
    )


def _write_coaching(analysis: Path) -> None:
    inputs = analysis / "coach_inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / "coaching_index.json").write_text(
        json.dumps(
            [index_entry("death_episode_72.5", video_time=72.5, rank=1)]
        ),
        encoding="utf-8",
    )
    (inputs / "death_episode_72.5.coaching.json").write_text(
        json.dumps(
            {
                "factors": [
                    {"active": True, "statement_player": STATEMENT},
                    {"active": False, "statement_player": "should not appear"},
                ]
            }
        ),
        encoding="utf-8",
    )


def _write_assessment(analysis: Path) -> None:
    write_prototype_unit(
        analysis / "coach_prototype",
        "death_episode_72.5",
        {
            "model": "The player did not check the map overlay before death.",
            "zz-baseline": empty_coaching_assessment().assessment,
        },
        primary_model="model",
    )


def test_moment_jpeg_remains_after_the_source_video_is_deleted(tmp_path: Path) -> None:
    video = _sample_video(tmp_path / "clip.mp4")
    entry = _moment_entry("death_episode_1.0", 1.0, 1)
    client, token = _finish_video(tmp_path, video, [entry], dev_mode=False)
    stored = client.app.state.store.list_submissions()[0]
    video_row = client.app.state.store.get_video(stored.id)
    jpeg = Path(stored.analysis_dir) / "public_frames" / "death_episode_1.0.jpg"
    assert video_row.object_key == ""
    assert jpeg.is_file()
    assert jpeg.read_bytes()[:2] == b"\xff\xd8"
    body = client.get(f"/api/submissions/{token}").json()
    moment = body["result"]["moments"][0]
    blob = json.dumps(moment)
    assert moment["frame"] is True
    assert moment["statements"] == [STATEMENT]
    assert "public_frames" not in blob
    assert "source.mp4" not in blob
    assert "http" not in blob
    frame = client.get(_frame_url(token, 0))
    assert frame.status_code == 200
    assert frame.headers["content-type"].startswith("image/jpeg")
    assert frame.content == jpeg.read_bytes()
    wrong = client.get(_frame_url("not-the-review-token", 0))
    assert wrong.status_code == 404


def test_frame_seek_clamps_a_moment_near_the_start(tmp_path: Path) -> None:
    assert frame_seek(0.2) == 0.0
    video = tmp_path / "clip.mp4"
    _sample_video(video)
    analysis = tmp_path / "analysis"
    _write_index(analysis, [_moment_entry("death_episode_0.2", 0.2, 1)])
    seen: list[list[str]] = []

    def run(argv: list[str]) -> int:
        seen.append(argv)
        completed = subprocess.run(argv, capture_output=True, check=False)
        return int(completed.returncode)

    extract_frames(analysis, video, run=run)
    argv = seen[0]
    seek = argv[argv.index("-ss") + 1]
    assert argv.index("-ss") > argv.index("-i")
    assert seek == "0.000"
    assert float(seek) == 0.0
    jpeg = analysis / "public_frames" / "death_episode_0.2.jpg"
    assert jpeg.read_bytes()[:2] == b"\xff\xd8"
    direct = ffmpeg_argv(video, tmp_path / "at-zero.jpg", 0.0)
    assert direct[direct.index("-ss") + 1] == seek


def test_death_frame_uses_the_latest_cue_before_the_death(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis"
    death = 80.0
    both = _indexed_death(analysis, "both", death)
    _write_cues(analysis, both, low_ink=[74.0], special=[77.0])
    assert moment_seek(analysis, both) == 77.0
    _write_cues(analysis, both, low_ink=[77.0], special=[74.0])
    assert moment_seek(analysis, both) == 77.0
    ink = _indexed_death(analysis, "ink", death)
    _write_cues(analysis, ink, low_ink=[74.0], special=[])
    assert moment_seek(analysis, ink) == 74.0
    gauge = _indexed_death(analysis, "gauge", death)
    _write_cues(analysis, gauge, low_ink=[], special=[77.0])
    assert moment_seek(analysis, gauge) == 77.0
    quiet = _indexed_death(analysis, "quiet", death)
    _write_cues(analysis, quiet, low_ink=[], special=[])
    assert moment_seek(analysis, quiet) == 79.5
    late = _indexed_death(analysis, "late", death)
    _write_cues(analysis, late, low_ink=[80.0, 81.0], special=[80.0])
    assert moment_seek(analysis, late) == 79.5
    invalid = _indexed_death(analysis, "invalid", death)
    _write_special(analysis, invalid, video_time=77.0, coaching_valid=False)
    assert moment_seek(analysis, invalid) == 79.5
    other = CoachingIndexEntry.model_validate(
        index_entry("splat_80.0", video_time=death, rank=1, candidate_type="splat")
    )
    _write_cues(analysis, other, low_ink=[77.0], special=[76.0])
    assert moment_seek(analysis, other) == 79.5


def test_death_frame_lookback_includes_the_eighth_second(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis"
    death = 80.0
    edge = death - 8.0
    older = math.nextafter(edge, 0.0)
    on_edge = _indexed_death(analysis, "edge", death)
    _write_cues(analysis, on_edge, low_ink=[edge], special=[])
    assert moment_seek(analysis, on_edge) == edge
    just_older = _indexed_death(analysis, "older", death)
    _write_cues(analysis, just_older, low_ink=[older], special=[])
    assert moment_seek(analysis, just_older) == 79.5
    gauge_edge = _indexed_death(analysis, "gauge_edge", death)
    _write_cues(analysis, gauge_edge, low_ink=[], special=[edge])
    assert moment_seek(analysis, gauge_edge) == edge
    gauge_older = _indexed_death(analysis, "gauge_older", death)
    _write_cues(analysis, gauge_older, low_ink=[], special=[older])
    assert moment_seek(analysis, gauge_older) == 79.5


def test_malformed_cue_file_keeps_the_half_second_still(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    _sample_video(video)
    analysis = tmp_path / "analysis"
    death = 2.0
    entry = _moment_entry("death_episode_2.0", death, 1)
    _write_index(analysis, [entry])
    (analysis / "vision_manifest.json").write_text("{", encoding="utf-8")
    coach = analysis / "coach_inputs" / "death_episode_2.0.coach_input.json"
    coach.write_text("{", encoding="utf-8")
    seen: list[list[str]] = []

    def run(argv: list[str]) -> int:
        seen.append(argv)
        completed = subprocess.run(argv, capture_output=True, check=False)
        return int(completed.returncode)

    extract_frames(analysis, video, run=run)
    argv = seen[0]
    assert argv[argv.index("-ss") + 1] == "1.500"
    jpeg = analysis / "public_frames" / "death_episode_2.0.jpg"
    assert jpeg.read_bytes()[:2] == b"\xff\xd8"


def test_frame_urls_follow_result_moment_order(tmp_path: Path) -> None:
    video = _sample_video(tmp_path / "clip.mp4")
    early = _moment_entry("early_0.5", 0.5, 2)
    late = _moment_entry("late_2.0", 2.0, 1)
    client, token = _finish_video(tmp_path, video, [early, late])
    body = client.get(f"/api/submissions/{token}").json()
    moments = body["result"]["moments"]
    assert [item["video_time"] for item in moments] == [0.5, 2.0]
    stored = client.app.state.store.list_submissions()[0]
    root = Path(stored.analysis_dir) / "public_frames"
    first = client.get(_frame_url(token, 0))
    second = client.get(_frame_url(token, 1))
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.content == (root / "early_0.5.jpg").read_bytes()
    assert second.content == (root / "late_2.0.jpg").read_bytes()
    assert first.content != second.content
    assert client.get(_frame_url(token, 2)).status_code == 404


def test_failed_grab_still_completes_without_an_image(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def boom(*_args, **_kwargs) -> None:
        raise OSError("ffmpeg")

    monkeypatch.setattr("public_site.worker.extract_frames", boom)
    client = _client(tmp_path, dev_mode=False, run_command=_finish_coaching)
    created = _create(client)
    token = _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    assert client.app.state.worker.pump() is True
    client.app.state.worker.join()
    stored = client.app.state.store.list_submissions()[0]
    video_row = client.app.state.store.get_video(stored.id)
    body = client.get(f"/api/submissions/{token}").json()
    moment = body["result"]["moments"][0]
    assert stored.status == "complete"
    assert video_row.object_key == ""
    assert moment["frame"] is False
    assert moment["statements"] == [STATEMENT]
    assert "public_frames" not in json.dumps(moment)
    assert client.get(_frame_url(token, 0)).status_code == 404


def test_stored_source_fills_a_missing_still_on_first_view(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "public_site.worker.extract_frames",
        lambda *_args, **_kwargs: None,
    )
    video = _sample_video(tmp_path / "clip.mp4")
    entry = _moment_entry("death_episode_1.0", 1.0, 1)
    client, token = _finish_video(tmp_path, video, [entry], dev_mode=True)
    body = client.get(f"/api/submissions/{token}").json()
    assert body["result"]["moments"][0]["frame"] is True
    frame = client.get(_frame_url(token, 0))
    assert frame.status_code == 200
    assert frame.content[:2] == b"\xff\xd8"


def _finish_video(
    tmp_path: Path,
    video: bytes,
    entries: list[dict],
    **kwargs,
) -> tuple[TestClient, str]:
    client = _client(
        tmp_path,
        max_video_bytes=len(video),
        run_command=_coaching_command(entries),
        **kwargs,
    )
    created = _create(client, size_bytes=len(video))
    token = _put(client, created, content=video)
    uploaded = client.post(f"/api/submissions/{token}/uploaded")
    assert uploaded.status_code == 200
    assert client.app.state.worker.pump() is True
    client.app.state.worker.join()
    return client, token


def _coaching_command(entries: list[dict]) -> Callable[[list[str]], int]:
    def run(argv: list[str]) -> int:
        if "coach-inputs" in argv:
            folder = Path(argv[argv.index("coach-inputs") + 1])
            _write_index(folder, entries)
        return 0

    return run


def _write_index(analysis: Path, entries: list[dict]) -> None:
    inputs = analysis / "coach_inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / "coaching_index.json").write_text(json.dumps(entries), encoding="utf-8")
    for entry in entries:
        name = str(entry["coaching_json"])
        (inputs / name).write_text(
            json.dumps(
                {"factors": [{"active": True, "statement_player": STATEMENT}]}
            ),
            encoding="utf-8",
        )


def _moment_entry(safe_id: str, video_time: float, rank: int) -> dict:
    return index_entry(safe_id, video_time=video_time, rank=rank)


def _indexed_death(analysis: Path, safe_id: str, video_time: float) -> CoachingIndexEntry:
    analysis.mkdir(parents=True, exist_ok=True)
    return CoachingIndexEntry.model_validate(
        index_entry(safe_id, video_time=video_time, rank=1)
    )


def _write_cues(
    analysis: Path,
    entry: CoachingIndexEntry,
    *,
    low_ink: list[float],
    special: list[float],
) -> None:
    events = [
        {"event_type": "low_ink", "start_time": start, "confidence": 1.0}
        for start in low_ink
    ]
    (analysis / "vision_manifest.json").write_text(
        json.dumps({"game_events": events}),
        encoding="utf-8",
    )
    observations = [_ready_reading(time) for time in special]
    _write_observations(analysis, entry, observations)


def _write_special(
    analysis: Path,
    entry: CoachingIndexEntry,
    *,
    video_time: float,
    coaching_valid: bool,
) -> None:
    (analysis / "vision_manifest.json").write_text(
        json.dumps({"game_events": []}),
        encoding="utf-8",
    )
    reading = _ready_reading(video_time)
    reading["coaching_valid"] = coaching_valid
    _write_observations(analysis, entry, [reading])


def _ready_reading(video_time: float) -> dict:
    return {
        "video_time": video_time,
        "visible": True,
        "ready": True,
        "coaching_valid": True,
    }


def _write_observations(
    analysis: Path,
    entry: CoachingIndexEntry,
    observations: list[dict],
) -> None:
    folder = analysis / "coach_inputs"
    folder.mkdir(parents=True, exist_ok=True)
    document = {"primary_context": {"special": {"observations": observations}}}
    (folder / entry.coach_input_json).write_text(
        json.dumps(document),
        encoding="utf-8",
    )


def _sample_video(path: Path) -> bytes:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x180:d=1:r=10",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:d=2:r=10",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path.read_bytes()


def _frame_url(token: str, index: int) -> str:
    return f"/api/submissions/{token}/moments/{index}/frame"


def test_review_html_describes_the_finished_coaching(tmp_path: Path) -> None:
    video = _sample_video(tmp_path / "clip.mp4")
    entry = _moment_entry("death_episode_1.0", 1.0, 1)
    client, token = _finish_video(tmp_path, video, [entry], dev_mode=True)
    body = client.get(f"/api/submissions/{token}").json()
    assert body["match_seconds"] == 12
    html = review_html(
        client.app.state.service,
        client.app.state.settings,
        "<html><head><title>old</title></head><body></body></html>",
        token,
    )
    assert html is not None
    assert "Splatoon 3 Coaching Review" in html
    assert "1 coaching moment · 0:12 match" in html
    assert f"http://coach.example/review/{token}" in html
    assert f"/api/submissions/{token}/moments/0/frame" in html
    assert 'name="twitter:card" content="summary_large_image"' in html
    assert review_html(
        client.app.state.service,
        client.app.state.settings,
        "<head></head>",
        "missing-token",
    ) is None


def test_death_episode_review_projects_existing_lifecycle(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis"
    inputs = analysis / "coach_inputs"
    inputs.mkdir(parents=True)
    entries = [
        _indexed("late", "death_episode", 200.0, 1),
        _indexed("engage", "engagement", 120.0, 9),
        _indexed("early", "death_episode", 100.0, 3),
        _indexed("mid", "death_episode", 160.0, 2),
    ]
    (inputs / "coaching_index.json").write_text(json.dumps(entries), encoding="utf-8")
    _write_episode(
        inputs,
        "early",
        death={
            **_clocked_death(),
            "map_check_before_death": True,
            "seconds_since_map_check": 61.5,
            "map_checked_while_dead": True,
        },
        special_ready=True,
        roster=(2, 4),
        factors=[
            _factor("death_special_ready", "Your special gauge was ready."),
            _factor("death_map_overlay_before_false", STATEMENT),
            _factor("death_final_30s", "Death occurred during the final 30 seconds."),
            _factor("hidden", "should not appear", active=False),
        ],
    )
    _write_episode(
        inputs,
        "mid",
        death={
            "death_time": 160.0,
            "map_check_before_death": False,
            "map_checked_while_dead": False,
        },
        samples=[_sample("death", 140)],
        factors=[_factor("death_map_overlay_before_false", STATEMENT)],
    )
    _write_episode(
        inputs,
        "late",
        death={
            "death_time": 50.0,
            "respawn_time": 50.0,
            "death_to_respawn": 0.0,
            "map_check_before_death": None,
        },
        samples=[_sample("death", None)],
        factors=[],
    )
    _write_coaching_file(
        inputs, "engage.coaching.json", [_factor("splat", "Observed splats.")]
    )
    _write_named_assessment(analysis, "early", "Keep the high ground.")
    _write_named_assessment(analysis, "late", "A note.")
    _write_events(
        analysis,
        [
            ("death", 50.0),
            ("death", 160.0),
            ("splat", 165.0),
            ("splat", 170.0),
            ("map_overlay", 191.5),
            ("splat", 200.0),
            ("death", 206.5),
            ("map_overlay", 207.5),
            ("splat", 208.0),
            ("map_overlay", 211.0),
            ("respawn", 214.0),
            ("active_again", 215.5),
            ("special_used", 218.0),
            ("low_ink", 218.5),
            ("splat", 219.0),
            ("death", 250.0),
            ("splat", 260.0),
        ],
    )

    moments = [item.model_dump() for item in load_result(analysis).moments]
    assert [item["video_time"] for item in moments] == [100.0, 120.0, 160.0, 200.0]

    early, engage, mid, late = moments
    assert early["heading"] == "Death — 1:24 remaining"
    assert "1:40" not in early["heading"]
    assert "3:26" not in early["heading"]
    assert [mark["title"] for mark in early["marks"]] == [
        "Map check",
        "Splat",
        "Death",
        "Splat",
        "Splat",
    ]
    assert [mark["clock"] for mark in early["marks"]] == [
        "3:11.5",
        "3:20.0",
        "3:26.5",
        "3:28.0",
        "3:39.0",
    ]
    assert "2:50.0" not in [mark["clock"] for mark in early["marks"]]
    assert [mark["offset_label"] for mark in early["marks"]] == [
        "15.0s before",
        "6.5s before",
        None,
        "1.5s after",
        "12.5s after",
    ]
    assert [mark["anchor"] for mark in early["marks"]] == [
        False,
        False,
        True,
        False,
        False,
    ]
    assert "Respawn" not in [mark["title"] for mark in early["marks"]]
    assert "2:45.0" not in [mark["clock"] for mark in early["marks"]]
    assert "3:27.5" not in [mark["clock"] for mark in early["marks"]]
    assert "3:31.0" not in [mark["clock"] for mark in early["marks"]]
    assert "4:20.0" not in [mark["clock"] for mark in early["marks"]]
    assert early["gaps"] == []
    assert early["until_active_again"] == "9.0s out of play"
    assert early["recovery_context"] == "The player checked the map during recovery."
    assert early["recording_times"] == []
    assert "Last observed map check: 61.5s before death" in early["context"]
    assert "No map check observed before death" not in early["context"]
    assert "Special was ready." in early["context"]
    assert "Your special gauge was ready." not in early["context"]
    assert STATEMENT not in early["context"]
    assert "Death occurred during the final 30 seconds." in early["context"]
    assert "Roster at death: 2v4." in early["context"]
    assert "should not appear" not in early["context"]
    assert early["assessment"] == "Keep the high ground."

    assert engage["heading"] is None
    assert engage["marks"] == []
    assert engage["statements"] == ["Observed splats."]
    assert engage["assessment"] is None

    assert mid["heading"] == "Death — 2:20 remaining"
    assert "2:40" not in mid["heading"]
    assert [mark["title"] for mark in mid["marks"]] == ["Death", "Splat", "Splat"]
    assert [mark["clock"] for mark in mid["marks"]] == ["2:40.0", "2:45.0", "2:50.0"]
    assert mid["gaps"] == []
    assert mid["until_active_again"] is None
    assert mid["recovery_context"] is None
    assert "No map check observed before death" in mid["context"]
    assert "Last observed map check" not in " ".join(mid["context"])
    assert not any(line.startswith("Special") for line in mid["context"])
    assert "No roster information was available." in mid["context"]
    assert mid["assessment"] is None
    assert mid["coaching_state"] == "not_reviewed"
    assert mid["recording_times"] == []

    assert late["heading"] == "Death"
    assert "remaining" not in late["heading"]
    assert [mark["title"] for mark in late["marks"]] == ["Death"]
    assert [mark["clock"] for mark in late["marks"]] == ["0:50.0"]
    assert late["marks"][0]["anchor"] is True
    assert late["until_active_again"] == "0.0s until respawn"
    assert late["recovery_context"] is None
    assert late["gaps"] == []
    assert "Map-check evidence was unavailable for this video." in late["context"]
    assert not any("No map check" in line for line in late["context"])
    assert late["assessment"] == "A note."


def test_timeline_clusters_nearby_map_checks(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis"
    inputs = analysis / "coach_inputs"
    inputs.mkdir(parents=True)
    entries = [
        _indexed("first", "death_episode", 170.0, 1),
        _indexed("second", "death_episode", 300.0, 2),
    ]
    (inputs / "coaching_index.json").write_text(json.dumps(entries), encoding="utf-8")
    _write_episode(
        inputs,
        "first",
        death={
            "death_time": 170.0,
            "map_check_before_death": True,
            "seconds_since_map_check": 10.5,
        },
        factors=[],
    )
    _write_episode(
        inputs,
        "second",
        death={
            "death_time": 300.0,
            "active_again_time": 305.0,
            "death_to_active_again": 5.0,
            "map_check_before_death": True,
            "seconds_since_map_check": 2.0,
            "map_checked_while_dead": True,
        },
        factors=[],
    )
    raw = [
        ("map_overlay", 130.0),
        ("map_overlay", 152.5),
        ("splat", 153.0),
        ("map_overlay", 154.5),
        ("map_overlay", 159.5),
        ("death", 170.0),
        ("map_overlay", 270.0),
        ("map_overlay", 278.0),
        ("map_overlay", 286.0),
        ("map_overlay", 298.0),
        ("death", 300.0),
        ("map_overlay", 301.0),
        ("map_overlay", 303.0),
        ("map_overlay", 307.0),
        ("map_overlay", 309.0),
        ("splat", 312.0),
    ]
    _write_events(analysis, raw)

    first, second = [item.model_dump() for item in load_result(analysis).moments]

    assert [(mark["title"], mark["clock"]) for mark in first["marks"]] == [
        ("Map check", "2:39.5"),
        ("Death", "2:50.0"),
    ]
    assert first["recovery_context"] is None

    assert [(mark["title"], mark["clock"]) for mark in second["marks"]] == [
        ("Map check", "4:46.0"),
        ("Map check", "4:58.0"),
        ("Death", "5:00.0"),
        ("Map check", "5:07.0"),
        ("Splat", "5:12.0"),
    ]
    clocks = [mark["clock"] for mark in second["marks"]]
    for hidden in ("4:30.0", "4:38.0", "5:01.0", "5:03.0", "5:09.0"):
        assert hidden not in clocks
    assert second["recovery_context"] == "The player checked the map during recovery."

    manifest = json.loads((analysis / "vision_manifest.json").read_text())
    assert [
        (row["event_type"], row["start_time"]) for row in manifest["game_events"]
    ] == raw


def test_public_context_marks_time_bounded(tmp_path: Path) -> None:
    """Context marks stay within 15 video seconds; offsets use the duration clock."""
    analysis = tmp_path / "analysis"
    inputs = analysis / "coach_inputs"
    inputs.mkdir(parents=True)
    entries = [_indexed("replay", "death_episode", 100.0, 1)]
    (inputs / "coaching_index.json").write_text(json.dumps(entries), encoding="utf-8")
    _write_episode(inputs, "replay", death={"death_time": 100.0}, factors=[])
    path = inputs / "replay.coach_input.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["playback_clock"] = {
        "status": "rate_mismatch",
        "game_seconds_per_video_second": 1.5,
        "video_seconds_per_game_second": 1 / 1.5,
        "rate_spread": 0.0,
        "reason": "test",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    _write_events(
        analysis,
        [
            ("splat", 70.0),
            ("map_overlay", 84.9),
            ("splat", 85.0),
            ("splat", 92.0),
            ("death", 100.0),
            ("splat", 115.0),
            ("splat", 115.1),
        ],
    )

    (moment,) = [item.model_dump() for item in load_result(analysis).moments]
    marks = moment["marks"]
    times = [mark["video_time"] for mark in marks]
    assert times == [85.0, 92.0, 100.0, 115.0]
    for mark in marks:
        assert abs(mark["video_time"] - 100.0) <= 15.0
    context = [mark for mark in marks if not mark["anchor"]]
    assert [mark["offset_clock"] for mark in context] == ["game"] * 3
    assert [mark["offset_seconds"] for mark in context] == [22.5, 12.0, 22.5]
    assert [mark["offset_label"] for mark in context] == [
        "22s before (game clock)",
        "12s before (game clock)",
        "22s after (game clock)",
    ]
    for mark in context:
        assert "led to" not in mark["offset_label"]
        assert "because" not in mark["offset_label"]


def _indexed(safe_id: str, candidate_type: str, video_time: float, rank: int) -> dict:
    return index_entry(
        safe_id, candidate_type=candidate_type, video_time=video_time, rank=rank
    )


def _clocked_death() -> dict:
    return {
        "death_time": 206.5,
        "respawn_time": 214.0,
        "active_again_time": 215.5,
        "death_to_respawn": 7.5,
        "respawn_to_active_again": 1.5,
        "death_to_active_again": 9.0,
    }


def _write_episode(
    inputs: Path,
    safe_id: str,
    *,
    death: dict,
    factors: list[dict],
    samples: list[dict] | None = None,
    special_ready: bool | None = None,
    roster: tuple[int, int] | None = None,
) -> None:
    skipped = {
        "map_check_before_death",
        "map_checked_while_dead",
        "seconds_since_map_check",
    }
    episode = {key: value for key, value in death.items() if key not in skipped}
    primary: dict = {"death_episode": episode}
    map_fields = {}
    if "map_check_before_death" in death:
        flag = death["map_check_before_death"]
        map_fields["map_check_before_death"] = flag
        map_fields["map_check_status"] = {
            True: "observed",
            False: "not_observed",
            None: "unknown",
        }[flag]
        map_fields["seconds_since_map_check_before_death"] = death.get(
            "seconds_since_map_check"
        )
    if "map_checked_while_dead" in death:
        map_fields["map_checked_while_dead"] = death["map_checked_while_dead"]
    if map_fields:
        primary["map"] = map_fields
    if special_ready is not None:
        primary["special"] = {
            "nearest_before_anchor": {"visible": True, "ready": special_ready}
        }
    if roster is not None:
        primary["players"] = {
            "at_death": {"ally_alive_count": roster[0], "opponent_alive_count": roster[1]}
        }
    payload = {
        "primary_context": primary,
        "game_clock_samples": samples
        if samples is not None
        else [
            _sample("death", 84),
            _sample("respawn", 76),
            _sample("active_again", 75),
        ],
        "playback_clock": {
            "status": "normal",
            "game_seconds_per_video_second": 1.0,
            "video_seconds_per_game_second": 1.0,
            "reason": "test",
        },
        "durations": video_durations(
            death_to_respawn=death.get("death_to_respawn"),
            death_to_active_again=death.get("death_to_active_again"),
        ).model_dump(mode="json"),
    }
    (inputs / f"{safe_id}.coach_input.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    _write_coaching_file(inputs, f"{safe_id}.coaching.json", factors)


def _sample(label: str, seconds: int | None) -> dict:
    observation = None if seconds is None else {"seconds_remaining": seconds}
    return {"label": label, "observation": observation}


def _factor(factor_id: str, statement: str, *, active: bool = True) -> dict:
    return {
        "factor_id": factor_id,
        "active": active,
        "statement_player": statement,
    }


def _write_coaching_file(inputs: Path, name: str, factors: list[dict]) -> None:
    (inputs / name).write_text(json.dumps({"factors": factors}), encoding="utf-8")


def _write_events(analysis: Path, events: list[tuple[str, float]]) -> None:
    rows = [
        {"event_type": kind, "start_time": when} for kind, when in events
    ]
    (analysis / "vision_manifest.json").write_text(
        json.dumps({"game_events": rows}), encoding="utf-8"
    )


def _write_named_assessment(analysis: Path, safe_id: str, assessment: str) -> None:
    prototype = analysis / "coach_prototype"
    prototype.mkdir(parents=True, exist_ok=True)
    (prototype / f"{safe_id}.model.output.json").write_text(
        json.dumps({"assessment": assessment}),
        encoding="utf-8",
    )



def test_special_line_keeps_not_observed_distinct_from_not_ready() -> None:
    from public_site.results import _special_line

    def line(special: dict, active: frozenset[str] = frozenset()) -> str | None:
        return _special_line({"primary_context": {"special": special}}, set(active))

    ready = {"nearest_before_anchor": {"visible": True, "ready": True}}
    not_ready = {"nearest_before_anchor": {"visible": True, "ready": False}}
    hidden = {"nearest_before_anchor": {"visible": False, "ready": False}}
    assert line(ready, frozenset({"death_special_ready"})) == "Special was ready."
    assert line(not_ready) == "Special was not ready just before the death."
    assert line(hidden) == "The special gauge was not visible just before the death."
    assert line({**hidden, "state_at_anchor": "not_observed"}) == line(hidden)
    assert line({}) is None


def test_special_line_requires_confirmed_ready_and_valid_reading() -> None:
    from public_site.results import _special_line

    ready = {"nearest_before_anchor": {"visible": True, "ready": True}}
    single = _special_line({"primary_context": {"special": ready}}, set())
    assert single == "Special readiness just before the death was not confirmed."
    invalid = {
        "state_at_anchor": "ready",
        "nearest_before_anchor": {
            "visible": True, "ready": True, "coaching_valid": False,
        },
    }
    confirmed = {"death_special_ready"}
    assert (
        _special_line({"primary_context": {"special": invalid}}, confirmed)
        == "The special gauge was not visible just before the death."
    )
