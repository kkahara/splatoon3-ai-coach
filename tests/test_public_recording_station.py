"""Capture station and publication identity. No capture card is opened."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from public_site.app import create_app
from public_site.payments import PaymentStore
from public_site.recording_publish import CONFLICT_REASON, MISMATCH
from public_site.errors import RequestRejected
from public_site.recording_station import (
    DEVICE_DENIED,
    CaptureMode,
    OutputTail,
    _capture_argv,
    _probe_argv,
    classify_probe,
    command_owns,
    discover_capture_modes,
    parse_avfoundation_devices,
    parse_capture_modes,
    request_graceful_stop,
    select_capture_mode,
)
from public_site.settings import PublicSettings
from public_site.storage import file_sha256
from test_public_payments import FakeCheckout
from test_public_recording_intake import Pipeline
from test_public_recordings import _login, _paid

from splatoon3_ai_coach.config.paths import default_config_path

_LIST = """
[AVFoundation indev @ 1] AVFoundation video devices:
[AVFoundation indev @ 1] [0] FaceTime HD Camera
[AVFoundation indev @ 1] [3] Capture screen 0
[AVFoundation indev @ 1] AVFoundation audio devices:
[AVFoundation indev @ 1] [0] MacBook Air Microphone
"""
_QUIT = (
    "import sys\n"
    "while True:\n"
    "    line = sys.stdin.buffer.readline()\n"
    "    if not line or line.startswith(b'q'):\n"
    "        raise SystemExit(0)\n"
)


# Probe output recorded from real devices, with the log prefixes ffmpeg prints.
_CARD_PROBE = """
[in#0 @ 0x757014000] Selected framerate (1.000000) is not supported by the device.
[in#0 @ 0x757014000] Supported modes:
[in#0 @ 0x757014000]   1920x1080@[60.000240 60.000240]fps
    Last message repeated 1 times
[in#0 @ 0x757010000] Error opening input: Input/output error
Error opening input files: Input/output error
"""
_CAMERA_PROBE = """
[in#0 @ 0x9f4c18000] Selected framerate (1.000000) is not supported by the device.
[in#0 @ 0x9f4c18000] Supported modes:
[in#0 @ 0x9f4c18000]   1920x1080@[15.000000 30.000000]fps
[in#0 @ 0x9f4c18000]   1280x720@[15.000000 30.000000]fps
[in#0 @ 0x9f4c18000]   640x480@[15.000000 30.000000]fps
Error opening input files: Input/output error
"""
_MISSING_PROBE = """
[AVFoundation indev @ 0xc15010140] Video device not found
[in#0 @ 0xc15010000] Error opening input: Input/output error
Error opening input files: Input/output error
"""


def test_a_capture_card_at_1080p60_is_opened_at_that_mode(tmp_path: Path) -> None:
    modes = parse_capture_modes(_CARD_PROBE)
    assert modes == [CaptureMode(1920, 1080, 60.00024, 60.00024)]
    mode, rate = select_capture_mode(modes)
    assert (mode.width, mode.height, rate) == (1920, 1080, "60")
    argv = _capture_argv("Capture card", tmp_path / "source.mp4", mode, rate)
    assert argv[argv.index("-framerate") + 1] == "60"
    assert argv[argv.index("-video_size") + 1] == "1920x1080"
    assert argv.index("-framerate") < argv.index("-i")
    assert "Capture card:none" in argv
    assert "-pixel_format" not in argv
    assert argv[argv.index("-pix_fmt") + 1] == "yuv420p"
    assert argv.index("-pix_fmt") > argv.index("-c:v")
    assert {"-nostats", "warning"} <= set(argv)


def test_a_15_to_30_fps_camera_records_at_30() -> None:
    mode, rate = select_capture_mode(parse_capture_modes(_CAMERA_PROBE))
    assert (mode.width, mode.height, rate) == (1920, 1080, "30")


@pytest.mark.parametrize(
    ("modes", "expected"),
    [
        ([CaptureMode(1280, 720, 30, 30), CaptureMode(640, 480, 60, 60)], (1280, 720, "30")),
        ([CaptureMode(1920, 1080, 59.94, 59.94)], (1920, 1080, "59.94")),
        ([CaptureMode(1920, 1080, 24, 24)], (1920, 1080, "24")),
        (
            [CaptureMode(1920, 1080, 30, 30), CaptureMode(1920, 1080, 60, 60)],
            (1920, 1080, "60"),
        ),
    ],
)
def test_mode_selection_prefers_1080p_then_60_then_30_then_fastest(modes, expected) -> None:
    mode, rate = select_capture_mode(modes)
    assert (mode.width, mode.height, rate) == expected


def test_probe_asks_for_an_unsupported_rate_and_records_nothing() -> None:
    argv = _probe_argv("Capture card")
    assert argv[argv.index("-framerate") + 1] == "1"
    assert argv[-3:] == ["-f", "null", "-"]


def test_probe_outcomes_are_classified() -> None:
    assert classify_probe("Capture card", _CARD_PROBE)
    with pytest.raises(RequestRejected) as missing:
        classify_probe("Capture card", _MISSING_PROBE)
    assert missing.value.status_code == 409 and "not found" in missing.value.detail
    with pytest.raises(RequestRejected) as denied:
        classify_probe("Capture card", "[x @ 0x1] Camera access not authorized\n")
    assert denied.value.detail == DEVICE_DENIED
    with pytest.raises(RequestRejected) as empty:
        classify_probe("Capture card", "Selected framerate (1.0) is not supported by the device.\nSupported modes:\n")
    assert empty.value.status_code == 409 and "no usable video mode" in empty.value.detail
    noisy = "\n".join(f"[x @ 0x1] unexpected failure {index} " + "z" * 300 for index in range(50))
    with pytest.raises(RequestRejected) as odd:
        classify_probe("Capture card", noisy)
    assert odd.value.status_code == 503 and len(odd.value.detail) <= 560
    assert "[x @ 0x1]" not in odd.value.detail


def test_discovery_reports_a_hung_probe_and_reads_both_streams() -> None:
    def timed_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("ffmpeg", 15)

    with pytest.raises(RequestRejected) as hung:
        discover_capture_modes("Capture card", run=timed_out)
    assert hung.value.status_code == 503
    completed = subprocess.CompletedProcess([], 251, stdout="", stderr=_CAMERA_PROBE)
    assert len(discover_capture_modes("Capture card", run=lambda *a, **k: completed)) == 3


def test_output_tail_drains_and_keeps_only_the_last_lines() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-c", "for i in range(5000): print(f'line {i}')"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    tail = OutputTail(proc.stdout)
    assert proc.wait(timeout=10) == 0
    lines = tail.lines()
    assert len(lines) == 40 and lines[-1] == "line 4999"


def test_device_names_ignore_indexes_and_audio() -> None:
    assert parse_avfoundation_devices(_LIST) == ["FaceTime HD Camera", "Capture screen 0"]


def test_process_ownership_requires_ffmpeg_and_the_output_path(tmp_path: Path) -> None:
    output = tmp_path / "source.mp4"
    assert command_owns(f"ffmpeg -i card:none {output}", output)
    assert not command_owns(f"python record.py {output}", output)
    assert not command_owns("ffmpeg -i other.mp4", output)


def test_ffmpeg_quit_finalizes_an_mp4_without_a_capture_card(tmp_path: Path) -> None:
    """Off-hardware: lavfi into the station's encoder, then the same quit path."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg is not installed")
    output = tmp_path / "source.mp4"
    proc = subprocess.Popen(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
            "-c:v", "h264_videotoolbox", "-b:v", "8M", "-pix_fmt", "yuv420p",
            str(output),
        ],
        stdin=subprocess.PIPE,
        start_new_session=True,
    )
    time.sleep(0.5)
    request_graceful_stop(proc, timeout=8)
    assert proc.returncode == 0
    probed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probed.returncode == 0
    assert "mp4" in probed.stdout


def test_graceful_stop_uses_quit_and_leaves_a_zero_exit() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-c", _QUIT],
        stdin=subprocess.PIPE,
        start_new_session=True,
    )
    request_graceful_stop(proc, timeout=2)
    assert proc.returncode == 0


@pytest.fixture
def site(tmp_path: Path):
    url = os.environ.get("PUBLIC_DATABASE_URL", "")
    if "coach_test" not in url:
        pytest.skip("An isolated coach_test database is required")
    from public_site.accounts import AccountStore

    accounts, payments = AccountStore(url), PaymentStore(url)
    accounts.ensure_schema()
    accounts.clear()
    payments.ensure_schema()
    payments.clear()
    payments.set_price(500)
    pipeline = Pipeline()
    settings = PublicSettings(
        root=tmp_path / "public",
        static_dir=tmp_path / "no-dist",
        config_path=default_config_path(),
        review_codes_enabled=True,
        recording_station_enabled=True,
        station_dir=tmp_path / "station",
        dev_mode=True,
        public_base_url="http://testserver",
        stripe_webhook_secret="whsec",
        guest_submissions_per_hour=100,
        account_submissions_per_hour=100,
        submissions_per_hour=100,
        max_video_bytes=1000,
    )
    checkout = FakeCheckout()
    app = create_app(
        settings,
        accounts=accounts,
        payments=payments,
        checkout=checkout,
        verifier=lambda token: token == "ok",
        probe=pipeline.probe,
        start_worker=False,
    )
    with TestClient(app) as client:
        client.fake_checkout = checkout
        client.pipeline = pipeline
        yield client
    payments.clear()
    accounts.clear()


def _station(site: TestClient):
    station = site.app.state.service.recordings.station
    station.devices = lambda: ["Test card"]
    station.discover = lambda _name: [CaptureMode(1920, 1080, 60.00024, 60.00024)]
    station.warn_bytes = 4
    station.stop_bytes = 8
    station.max_seconds = 30

    def spawn(_argv, output: Path):
        output.write_bytes(b"0123456789abcdef")
        return subprocess.Popen(
            [sys.executable, "-c", _QUIT],
            stdin=subprocess.PIPE,
            start_new_session=True,
        )

    station.spawn = spawn
    return station


def _wait_final(station, submission_id: str) -> dict:
    status = {"phase": "capturing"}
    for _ in range(50):
        status = station.status(submission_id)
        if status["phase"] in {"finalized", "interrupted"}:
            return status
        time.sleep(0.05)
    return status


def test_station_routes_require_an_admin(site) -> None:
    created = _paid(site)
    endpoint = f"/api/admin/recordings/{created['submission_id']}/station/start"
    body = {"device_name": "Test card", "expected_version": 0}
    assert site.post(endpoint, json=body).status_code == 401
    _login(site)
    assert site.post(endpoint, json=body).status_code == 403


def test_size_limit_stops_gracefully_and_publishes_that_file(site) -> None:
    created = _paid(site)
    actor = _login(site, admin=True)
    station = _station(site)
    job = site.get(f"/api/admin/recordings/{created['submission_id']}").json()["job"]
    station.start(created["submission_id"], actor, "Test card", job["version"])
    status = _wait_final(station, created["submission_id"])
    assert status["phase"] == "finalized"
    assert status["size_warning"] is True
    assert "700 MiB" in status["message"]
    media = station._media(created["submission_id"])
    digest = file_sha256(media)
    uploaded = station.upload(created["submission_id"], actor, status["version"])
    assert uploaded["phase"] == "verified"
    video = site.app.state.service.store.get_video(created["submission_id"])
    assert video.sha256 == digest
    assert video.object_key == f"submissions/{created['submission_id']}/source.mp4"
    stored = site.app.state.service.storage.read_sha256(video.object_key)
    assert stored == (video.size_bytes, digest)
    assert site.app.state.service.recordings.intake.admits(
        site.app.state.service.store.get(created["submission_id"])
    )
    assert not media.exists()


def test_conflicting_object_is_kept_and_the_worker_does_not_analyze(
    site, tmp_path: Path
) -> None:
    created = _paid(site)
    actor = _login(site, admin=True)
    station = _station(site)
    job = site.get(f"/api/admin/recordings/{created['submission_id']}").json()["job"]
    key = f"submissions/{created['submission_id']}/source.mp4"
    stale = tmp_path / "stale.mp4"
    stale.write_bytes(b"stale-recording")
    storage = site.app.state.service.storage
    storage.put_file(key, stale, "video/mp4")
    stale_hash = file_sha256(stale)
    station.start(created["submission_id"], actor, "Test card", job["version"])
    status = _wait_final(station, created["submission_id"])
    with pytest.raises(Exception) as caught:
        station.upload(created["submission_id"], actor, status["version"])
    assert caught.value.reason == CONFLICT_REASON
    assert storage.read_sha256(key) == (stale.stat().st_size, stale_hash)
    assert station.status(created["submission_id"])["phase"] == "upload_uncertain"
    assert site.app.state.store.claim_oldest_queued(
        "worker", "2026-11-11T00:00:00Z", admit=site.app.state.worker._admit_review_code
    ) is None
    kept = station.status(created["submission_id"])
    refused = site.post(
        f"/api/admin/recordings/{created['submission_id']}/station/discard",
        json={"expected_version": kept["version"]},
    )
    assert refused.status_code == 409
    assert storage.head(key) is not None


def test_worker_refuses_a_swapped_object(site, tmp_path: Path) -> None:
    created = _paid(site)
    actor = _login(site, admin=True)
    station = _station(site)
    job = site.get(f"/api/admin/recordings/{created['submission_id']}").json()["job"]
    station.start(created["submission_id"], actor, "Test card", job["version"])
    status = _wait_final(station, created["submission_id"])
    station.upload(created["submission_id"], actor, status["version"])
    video = site.app.state.service.store.get_video(created["submission_id"])
    swapped = tmp_path / "swap.mp4"
    swapped.write_bytes(b"not-the-verified-recording")
    site.app.state.service.storage.put_file(video.object_key, swapped, "video/mp4")
    submission = site.app.state.service.store.get(created["submission_id"])
    assert site.app.state.service.recordings.intake.admits(submission) is False
    site.app.state.worker._execute(submission)
    failed = site.app.state.service.store.get(created["submission_id"])
    assert failed.status == "failed"
    assert failed.error == MISMATCH
    assert site.pipeline.calls == []


def test_stop_after_ffmpeg_exits_reports_the_finished_file(site) -> None:
    created = _paid(site)
    actor = _login(site, admin=True)
    station = _station(site)
    job = site.get(f"/api/admin/recordings/{created['submission_id']}").json()["job"]
    station.start(created["submission_id"], actor, "Test card", job["version"])
    status = _wait_final(station, created["submission_id"])
    assert station.stop(created["submission_id"])["phase"] == status["phase"]


def test_file_upload_waits_while_a_station_take_is_open(site) -> None:
    created = _paid(site)
    actor = _login(site, admin=True)
    station = _station(site)
    station.stop_bytes = 10**12
    job = site.get(f"/api/admin/recordings/{created['submission_id']}").json()["job"]
    started = station.start(created["submission_id"], actor, "Test card", job["version"])
    assert started["phase"] == "capturing"
    refused = site.post(
        f"/api/admin/recordings/{created['submission_id']}/upload",
        json={
            "expected_version": started["version"],
            "filename": "obs.mp4",
            "content_type": "video/mp4",
            "size_bytes": 16,
        },
    )
    assert refused.status_code == 409
    station.stop(created["submission_id"])


def _start(site: TestClient, created: dict, version: int):
    return site.post(
        f"/api/admin/recordings/{created['submission_id']}/station/start",
        json={"device_name": "Test card", "expected_version": version},
    )


def _version(site: TestClient, created: dict) -> int:
    return site.get(f"/api/admin/recordings/{created['submission_id']}").json()["job"]["version"]


def test_capture_can_be_stopped_discarded_and_redone_at_the_chosen_mode(site) -> None:
    created = _paid(site)
    _login(site, admin=True)
    station = _station(site)
    station.stop_bytes = 10**12
    started = _start(site, created, _version(site, created)).json()
    assert started["phase"] == "capturing"
    assert started["capture_mode"] == "1920x1080 at 60 fps"
    assert station.stop(created["submission_id"])["phase"] == "finalized"
    discarded = site.post(
        f"/api/admin/recordings/{created['submission_id']}/station/discard",
        json={"expected_version": started["version"]},
    )
    assert discarded.status_code == 200
    assert station.status(created["submission_id"])["phase"] == "absent"
    again = _start(site, created, _version(site, created))
    assert again.status_code == 200 and again.json()["phase"] == "capturing"
    station.stop(created["submission_id"])


SUMMARY_FIELDS = {"phase", "elapsed_seconds", "capture_mode", "size_warning"}


def _takes(site: TestClient) -> dict:
    response = site.get("/api/admin/recordings")
    assert response.status_code == 200, response.text
    return response.json()["station_takes"]


def test_the_queue_lists_a_running_take_with_only_its_summary(site) -> None:
    created = _paid(site)
    _login(site, admin=True)
    station = _station(site)
    station.stop_bytes = 10**12
    assert _takes(site) == {}
    started = _start(site, created, _version(site, created))
    assert started.status_code == 200
    take = _takes(site)[created["submission_id"]]
    assert set(take) == SUMMARY_FIELDS
    assert take["phase"] == "capturing"
    assert take["capture_mode"] == "1920x1080 at 60 fps"
    assert take["elapsed_seconds"] >= 0 and isinstance(take["size_warning"], bool)
    body = str(_takes(site))
    assert "station" not in body and "source.mp4" not in body and "Test card" not in body
    station.stop(created["submission_id"])
    assert _takes(site)[created["submission_id"]]["phase"] == "finalized"
    discarded = site.post(
        f"/api/admin/recordings/{created['submission_id']}/station/discard",
        json={"expected_version": started.json()["version"]},
    )
    assert discarded.status_code == 200
    assert created["submission_id"] not in _takes(site)


def test_the_queue_shows_a_take_whose_process_ended_as_interrupted(site) -> None:
    created = _paid(site)
    _login(site, admin=True)
    station = _station(site)
    station.stop_bytes = 10**12
    assert _start(site, created, _version(site, created)).status_code == 200
    with station._lock:
        proc = station._procs.pop(created["submission_id"])
        station._finished.pop(created["submission_id"])
    proc.kill()
    proc.wait()
    take = _takes(site)[created["submission_id"]]
    assert take["phase"] == "interrupted"
    assert set(take) == SUMMARY_FIELDS


def test_a_missing_device_is_refused_before_the_job_changes(site) -> None:
    created = _paid(site)
    _login(site, admin=True)
    station = _station(site)

    def missing(name: str):
        return classify_probe(name, _MISSING_PROBE)

    station.discover = missing
    version = _version(site, created)
    refused = _start(site, created, version)
    assert refused.status_code == 409
    assert "Test card" in refused.json()["detail"] and "not found" in refused.json()["detail"]
    assert _version(site, created) == version
    assert station.status(created["submission_id"])["phase"] == "absent"


def test_a_device_that_disappears_before_capture_explains_the_failure(site) -> None:
    created = _paid(site)
    _login(site, admin=True)
    station = _station(site)
    script = (
        "import sys\n"
        "print('[AVFoundation indev @ 0x1] Video device not found', flush=True)\n"
        "print('Error opening input files: Input/output error', flush=True)\n"
        "sys.exit(251)\n"
    )

    def vanished(_argv, _output: Path):
        return subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    station.spawn = vanished
    assert _start(site, created, _version(site, created)).status_code == 200
    status = _wait_final(station, created["submission_id"])
    assert status["phase"] == "interrupted"
    assert "disappeared" in status["message"]
    assert "Video device not found" in status["message"]
    assert "@ 0x1" not in status["message"]
