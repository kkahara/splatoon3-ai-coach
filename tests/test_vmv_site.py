"""Local analysis platform: runs, media, jobs, and ground truth."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from fastapi.testclient import TestClient
from vmv_site.app import create_app
from vmv_site.catalog import resolve_media
from vmv_site.jobs import JobRunner, analyze_command
from vmv_site.models import JobRecord
from vmv_site.settings import PlatformSettings
from vmv_site.store import PlatformStore, utc_now

from splatoon3_ai_coach.cli.app import app as cli_app

MANIFEST = {
    "schema_version": 1,
    "video_identity": "viz1",
    "extraction_manifest_path": "frames",
    "analysis": {
        "analysis_id": "a",
        "package_version": "0.2.0",
        "pipeline_version": "3.0.0",
        "detector_versions": {},
        "extraction_manifest_sha256": "x",
        "vision_config_sha256": "y",
        "video_identity": "viz1",
        "language": "ja",
    },
    "timing": {"video_duration_seconds": 12.0, "cadence_frame_count": 4},
    "frame_results": [],
    "state_snapshots": [],
    "game_events": [],
}


def _settings(tmp_path: Path, *, concurrent: int = 1) -> PlatformSettings:
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    (tmp_path / "movies").mkdir()
    config = tmp_path / "config.yaml"
    config.write_text("vision:\n  hud_cadence_fps: 2.0\n", encoding="utf-8")
    return PlatformSettings(
        analysis_root=analysis,
        platform_root=tmp_path / "platform",
        movies_dir=tmp_path / "movies",
        config_path=config,
        static_dir=tmp_path / "dist",
        max_concurrent=concurrent,
    )


def _write_run(settings: PlatformSettings, run_id: str) -> Path:
    folder = settings.analysis_root / run_id
    folder.mkdir()
    (folder / "vision_manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    return folder


class _Proc:
    """Stand-in for ``subprocess.Popen``."""

    def __init__(self, hold: threading.Event, command: list[str], stdout) -> None:
        self.hold = hold
        self.command = command
        self.stdout = stdout
        self.returncode: int | None = None
        self.terminated = False
        self.code = 0
        if "--out" in command and "--fail" not in command:
            out = Path(command[command.index("--out") + 1])
            manifest_path = out / "vision_manifest.json"
            manifest_path.write_text(json.dumps(MANIFEST), encoding="utf-8")

    def wait(self) -> int:
        self.hold.wait(timeout=5)
        if self.stdout is not None and self.code != 0:
            self.stdout.write("analysis failed\n")
        self.returncode = -15 if self.terminated else self.code
        return self.returncode

    def poll(self) -> int | None:
        if self.terminated:
            return -15
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.hold.set()


class _Gate:
    def __init__(self, *, code: int = 0, fail_subcommand: str | None = None) -> None:
        self.hold = threading.Event()
        self.procs: list[_Proc] = []
        self.code = code
        self.fail_subcommand = fail_subcommand

    def popen(self, command, **kwargs):
        proc = _Proc(self.hold, list(command), kwargs.get("stdout"))
        proc.code = self.code
        if self.fail_subcommand and self.fail_subcommand in command:
            proc.code = 1
        if proc.code != 0 and "--out" in command:
            manifest = Path(command[command.index("--out") + 1]) / "vision_manifest.json"
            if manifest.is_file():
                manifest.unlink()
        self.procs.append(proc)
        return proc


def _client(settings: PlatformSettings, gate: _Gate | None = None, **runner_kwargs):
    store = PlatformStore(settings)
    runner = JobRunner(
        store,
        popen=(gate or _Gate()).popen,
        start_thread=False,
        **runner_kwargs,
    )
    application = create_app(settings, store=store, runner=runner, start_worker=False)
    return TestClient(application), store, runner


def test_lists_run_without_a_video(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_run(settings, "legacy run")
    client, _, _ = _client(settings)
    listed = client.get("/api/runs")
    assert listed.status_code == 200
    body = listed.json()
    assert body[0]["id"] == "legacy run"
    assert body[0]["video_available"] is False
    assert body[0]["language"] == "ja"
    detail = client.get("/api/runs/legacy run")
    assert detail.status_code == 200
    assert detail.json()["view"]["summary"]["language"] == "ja"
    assert detail.json()["view"]["analysis_dir"] == ""
    assert client.get("/api/runs/legacy run/video").status_code == 404


def test_media_traversal_is_rejected(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    folder = _write_run(settings, "run1")
    (folder / "frame.jpg").write_bytes(b"\xff\xd8\xff")
    outside = settings.analysis_root / "secret.jpg"
    outside.write_bytes(b"\xff\xd8\xff")
    try:
        resolve_media(settings, "run1", "../secret.jpg")
    except ValueError as exc:
        assert "traversal" in str(exc)
    else:
        raise AssertionError("traversal was accepted")
    client, _, _ = _client(settings)
    ok = client.get("/api/runs/run1/media/frame.jpg")
    assert ok.status_code == 200
    denied = client.get("/api/runs/run1/media/..%2Fsecret.jpg")
    assert denied.status_code == 400


def test_video_range_and_missing_video(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_run(settings, "clip-run")
    client, store, _ = _client(settings)
    uploaded = client.post(
        "/api/videos",
        files={"file": ("match.mp4", b"0123456789abcdef", "video/mp4")},
    )
    assert uploaded.status_code == 200
    video_id = uploaded.json()["id"]
    store.write_link("clip-run", video_id, "job", "match.mp4")
    partial = client.get("/api/runs/clip-run/video", headers={"Range": "bytes=0-3"})
    assert partial.status_code == 206
    assert partial.content == b"0123"
    assert partial.headers["content-range"] == "bytes 0-3/16"
    whole = client.get("/api/runs/clip-run/video")
    assert whole.status_code == 200
    assert whole.content == b"0123456789abcdef"
    bad = client.get("/api/runs/clip-run/video", headers={"Range": "bytes=99-100"})
    assert bad.status_code == 416
    assert client.get("/api/runs/missing/video").status_code == 404


def test_upload_rejects_bad_extension(tmp_path: Path) -> None:
    client, _, _ = _client(_settings(tmp_path))
    rejected = client.post(
        "/api/videos",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert rejected.status_code == 400
    accepted = client.post(
        "/api/videos",
        files={"file": ("notes.mov", b"abcd", "video/quicktime")},
    )
    assert accepted.status_code == 200
    assert accepted.json()["suffix"] == ".mov"


def test_ground_truth_round_trip_and_reject(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    folder = _write_run(settings, "gt-run")
    manifest = (folder / "vision_manifest.json").read_text(encoding="utf-8")
    client, _, _ = _client(settings)
    empty = client.get("/api/runs/gt-run/ground-truth")
    assert empty.status_code == 200
    assert empty.json()["intervals"] == []
    payload = {
        "intervals": [
            {"t0": 1.0, "t1": 2.0, "detector": "special_gauge", "label": "special_used"}
        ],
        "video_label": "gt-run",
    }
    saved = client.put("/api/runs/gt-run/ground-truth", json=payload)
    assert saved.status_code == 200
    assert not (folder / "ground_truth.json.tmp").exists()
    assert (folder / "vision_manifest.json").read_text(encoding="utf-8") == manifest
    again = client.get("/api/runs/gt-run/ground-truth")
    assert again.json()["intervals"][0]["label"] == "special_used"
    malformed = client.put(
        "/api/runs/gt-run/ground-truth",
        json={
            "intervals": [
                {"t0": 3, "t1": 1, "detector": "death", "label": "real_death"}
            ]
        },
    )
    assert malformed.status_code == 422
    unknown = client.put(
        "/api/runs/gt-run/ground-truth",
        json={"intervals": [{"t0": 1, "t1": 1, "detector": "death", "label": "nope"}]},
    )
    assert unknown.status_code == 422
    kept = client.get("/api/runs/gt-run/ground-truth").json()
    assert kept["intervals"][0]["label"] == "special_used"


def test_job_success_failure_cancel_and_concurrency(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    gate = _Gate()
    client, store, runner = _client(settings, gate)
    video = client.post(
        "/api/videos",
        files={"file": ("match.mp4", b"0123456789", "video/mp4")},
    ).json()
    first = client.post("/api/jobs", json={"video_id": video["id"], "language": "ja"})
    assert first.status_code == 200
    assert first.json()["status"] == "running"
    assert "--debug-persist-cadence-frames" in gate.procs[0].command
    assert "ja" in gate.procs[0].command
    second = client.post("/api/jobs", json={"video_id": video["id"], "language": "en"})
    assert second.json()["status"] == "queued"
    assert len(gate.procs) == 1
    queued_id = second.json()["job_id"]
    cancelled = client.post(f"/api/jobs/{queued_id}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    gate.hold.set()
    runner.join_workers()
    done = client.get(f"/api/jobs/{first.json()['job_id']}")
    assert done.json()["status"] == "completed"
    assert done.json()["phase"] == "completed"
    assert done.json()["run_id"]
    assert str(tmp_path) not in done.text

    fail_gate = _Gate(code=1)
    fail_client, _, fail_runner = _client(settings, fail_gate)
    video2 = store.list_videos()[0]
    failed = fail_client.post("/api/jobs", json={"video_id": video2.id, "language": "en"})
    fail_gate.hold.set()
    fail_runner.join_workers()
    body = fail_client.get(f"/api/jobs/{failed.json()['job_id']}").json()
    assert body["status"] == "failed"
    assert body["error"]["exit_code"] == 1
    assert "analysis failed" in body["error"]["log_tail"]
    assert body["error"]["phase"] == "running: vision"
    assert "coaching" not in body["phase"]


def test_running_job_cancel_terminates_process(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    gate = _Gate()
    client, _, runner = _client(settings, gate)
    video = client.post(
        "/api/videos",
        files={"file": ("match.mp4", b"0123456789", "video/mp4")},
    ).json()
    created = client.post("/api/jobs", json={"video_id": video["id"], "language": "en"})
    stopped = client.post(f"/api/jobs/{created.json()['job_id']}/cancel")
    assert stopped.json()["status"] == "cancelled"
    assert gate.procs[0].terminated is True
    runner.join_workers()
    final = client.get(f"/api/jobs/{created.json()['job_id']}")
    assert final.json()["status"] == "cancelled"


def test_analyze_command_uses_the_cli_entrypoint(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    command = analyze_command(settings, Path("clip.mp4"), Path("out"), "en")
    assert "-m" not in command
    assert command[command.index("analyze") + 1].endswith("clip.mp4")
    assert "--language" in command
    assert "--debug-persist-cadence-frames" in command


def test_react_routes_serve_the_built_index(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.static_dir.mkdir()
    index = settings.static_dir / "index.html"
    index.write_text("<!doctype html>vmv", encoding="utf-8")
    client, _, _ = _client(settings)
    assert "vmv" in client.get("/").text
    assert "vmv" in client.get("/analyze").text
    assert client.get("/api/missing").status_code == 404


def test_scenario_review_round_trip_and_reject(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    folder = _write_run(settings, "review-run")
    (folder / "scenarios.json").write_text(
        json.dumps([{"scenario_id": "death_episode:1.000"}]),
        encoding="utf-8",
    )
    manifest = (folder / "vision_manifest.json").read_bytes()
    client, _, _ = _client(settings)
    empty = client.get("/api/runs/review-run/scenario-review")
    assert empty.status_code == 200
    assert empty.json()["marks"] == []
    saved = client.put(
        "/api/runs/review-run/scenario-review",
        json={
            "marks": [
                {"scenario_id": "death_episode:1.000", "status": "evidence_problem"}
            ]
        },
    )
    assert saved.status_code == 200
    assert (folder / "vision_manifest.json").read_bytes() == manifest
    kept_status = client.get("/api/runs/review-run/scenario-review").json()["marks"][0]
    assert kept_status["status"] == "evidence_problem"
    unknown_status = client.put(
        "/api/runs/review-run/scenario-review",
        json={"marks": [{"scenario_id": "death_episode:1.000", "status": "confident"}]},
    )
    assert unknown_status.status_code == 422
    unknown_scenario = client.put(
        "/api/runs/review-run/scenario-review",
        json={
            "marks": [
                {"scenario_id": "death_episode:9.000", "status": "needs_review"}
            ]
        },
    )
    assert unknown_scenario.status_code == 422
    kept = client.get("/api/runs/review-run/scenario-review").json()
    assert kept["marks"][0]["status"] == "evidence_problem"


def test_startup_interrupts_orphan_without_retrying_it(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = PlatformStore(settings)
    submitted = utc_now()
    store.save_job(
        JobRecord(
            id="orphan-experiment",
            kind="coach-experiment",
            scenario_id="death_episode:1.000",
            status="running",
            phase="running: llm-experiment",
            submitted_at=submitted,
            started_at=submitted,
            run_id="lab-run",
        )
    )
    store.save_job(
        JobRecord(
            id="next-coach",
            kind="coach",
            status="queued",
            phase="queued",
            submitted_at=submitted,
            run_id="lab-run",
        )
    )
    calls: list[str] = []
    runner = JobRunner(
        store,
        popen=_Gate().popen,
        start_thread=False,
        llm_complete=lambda _system, _user: calls.append("called") or "{}",
    )
    orphan = store.get_job("orphan-experiment")
    assert orphan is not None
    assert orphan.status == "failed"
    assert orphan.phase == "interrupted"
    assert orphan.error is not None
    assert orphan.error.phase == "running: llm-experiment"
    assert orphan.error.message == (
        "analysis site restarted while LLM experiment was running"
    )
    runner.pump_available()
    runner.join_workers()
    assert calls == []
    nxt = store.get_job("next-coach")
    assert nxt is not None
    assert nxt.status == "failed"
    assert nxt.error is not None
    assert nxt.error.message == "run not found"
    store.save_job(
        JobRecord(
            id="owned-analysis",
            kind="analyze",
            video_id="vid",
            status="running",
            phase="running: vision",
            submitted_at=submitted,
            started_at=submitted,
            run_id="live-run",
        )
    )
    runner._own("owned-analysis")
    runner.recover_orphans()
    owned = store.get_job("owned-analysis")
    assert owned is not None
    assert owned.status == "running"


def test_coach_job_order_failure_and_shared_queue(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_run(settings, "coach-run")
    gate = _Gate()
    gate.hold.set()
    client, _, runner = _client(settings, gate)
    created = client.post("/api/runs/coach-run/coach")
    assert created.status_code == 200
    runner.join_workers()
    body = client.get(f"/api/jobs/{created.json()['job_id']}").json()
    assert body["status"] == "completed"
    assert body["kind"] == "coach"
    joined = [" ".join(proc.command) for proc in gate.procs]
    assert len(joined) == 1
    assert "coach-inputs" in joined[0]
    assert "coach-prototype" not in joined[0]

    fail_gate = _Gate(fail_subcommand="coach-inputs")
    fail_gate.hold.set()
    fail_client, _, fail_runner = _client(settings, fail_gate)
    failed = fail_client.post("/api/runs/coach-run/coach")
    fail_runner.join_workers()
    failed_body = fail_client.get(f"/api/jobs/{failed.json()['job_id']}").json()
    assert failed_body["status"] == "failed"
    assert failed_body["error"]["phase"] == "running: coach-inputs"
    prototype_cmds = [
        proc for proc in fail_gate.procs if "coach-prototype" in proc.command
    ]
    assert prototype_cmds == []

    busy = _Gate()
    busy_client, _, busy_runner = _client(settings, busy)
    video = busy_client.post(
        "/api/videos",
        files={"file": ("match.mp4", b"0123456789", "video/mp4")},
    ).json()
    busy_client.post("/api/jobs", json={"video_id": video["id"], "language": "en"})
    queued = busy_client.post("/api/runs/coach-run/coach")
    assert queued.json()["status"] == "queued"
    assert len(busy.procs) == 1
    busy.hold.set()
    busy_runner.join_workers()


def test_prototype_llm_job_requires_inputs_and_passes_call_llm(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_run(settings, "coach-run")
    missing = _Gate()
    missing.hold.set()
    client, _, runner = _client(settings, missing)
    created = client.post("/api/runs/coach-run/coach-llm")
    assert created.status_code == 200
    runner.join_workers()
    body = client.get(f"/api/jobs/{created.json()['job_id']}").json()
    assert body["status"] == "failed"
    assert body["kind"] == "coach-llm"
    assert "coach-inputs" in body["error"]["message"]
    assert missing.procs == []

    folder = settings.analysis_root / "coach-run" / "coach_inputs"
    folder.mkdir()
    (folder / "coaching_index.json").write_text("[]\n", encoding="utf-8")
    gate = _Gate()
    gate.hold.set()
    ready, _, ready_runner = _client(settings, gate)
    started = ready.post("/api/runs/coach-run/coach-llm")
    ready_runner.join_workers()
    finished = ready.get(f"/api/jobs/{started.json()['job_id']}").json()
    assert finished["status"] == "completed"
    assert finished["phase"] == "completed"
    assert len(gate.procs) == 1
    command = " ".join(gate.procs[0].command)
    assert "coach-prototype" in command
    assert "--call-llm" in command


def _coaching_fixture(folder: Path, scenario_id: str = "death_episode:1.000") -> dict:
    safe_id = "death_episode_1.000"
    view = {
        "schema_version": 1,
        "unit": {"candidate_id": scenario_id, "marker": "rank-seven-marker"},
        "importance": {
            "importance_score": 2.5,
            "rank": 7,
            "selected_for_llm": False,
            "active_factors": [
                {"factor_id": "death_last_ally_alive", "weight": 2.5, "contribution": 2.5}
            ],
        },
    }
    inputs = folder / "coach_inputs"
    inputs.mkdir()
    (inputs / f"{safe_id}.llm_view.json").write_text(json.dumps(view), encoding="utf-8")
    (inputs / f"{safe_id}.coach_input.json").write_text(
        json.dumps({"primary": "evidence"}),
        encoding="utf-8",
    )
    (inputs / f"{safe_id}.coaching.json").write_text(
        json.dumps(
            {
                "importance_score": 2.5,
                "rank": 7,
                "selected_for_llm": False,
                "factors": [
                    {
                        "factor_id": "death_last_ally_alive",
                        "active": True,
                        "contribution": 2.5,
                        "statement_player": "You were the last ally alive.",
                        "interpretation": "Survival can matter here.",
                        "recommendation": "Stay alive until teammates return.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (inputs / "coaching_index.json").write_text(
        json.dumps(
            [
                {
                    "scenario_id": scenario_id,
                    "safe_id": safe_id,
                    "llm_view_json": f"{safe_id}.llm_view.json",
                    "coach_input_json": f"{safe_id}.coach_input.json",
                    "coaching_json": f"{safe_id}.coaching.json",
                }
            ]
        ),
        encoding="utf-8",
    )
    (inputs / "coach_inputs_meta.json").write_text(
        json.dumps({"analysis_dir": str(folder.resolve())}),
        encoding="utf-8",
    )
    proto = folder / "coach_prototype"
    proto.mkdir()
    (proto / f"{safe_id}.test-model.output.json").write_text(
        json.dumps(
            {
                "assessment": "official skip",
                "evidence_used": [],
                "limitations": ["below top-N"],
                "recommendations": [],
                "selected_claim_ids": [],
            }
        ),
        encoding="utf-8",
    )
    return view


def test_coach_layers_omit_server_paths(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    folder = _write_run(settings, "layer-run")
    _coaching_fixture(folder)
    secret = str(folder.resolve())
    client, _, _ = _client(settings)
    response = client.get(
        "/api/runs/layer-run/coach-layers",
        params={"scenario_id": "death_episode:1.000"},
    )
    assert response.status_code == 200
    assert secret not in response.text
    body = response.json()
    assert body["coach_input"]["primary"] == "evidence"
    assert body["llm_view"]["importance"]["rank"] == 7
    assert body["llm_view"]["importance"]["selected_for_llm"] is False
    assert body["system_prompt"]
    assert body["prototype_assessments"][0]["assessment"]["assessment"] == "official skip"
    assert "coach_inputs_meta" not in response.text


def test_experiment_keeps_history_and_exact_prompt(tmp_path: Path) -> None:
    import os

    from splatoon3_ai_coach.coach.prompts import load_system_prompt

    settings = _settings(tmp_path)
    folder = _write_run(settings, "lab-run")
    view = _coaching_fixture(folder)
    inputs_before = {
        path.relative_to(folder / "coach_inputs").as_posix(): path.read_bytes()
        for path in (folder / "coach_inputs").rglob("*")
        if path.is_file()
    }
    proto_file = folder / "coach_prototype" / "death_episode_1.000.test-model.output.json"
    proto_before = proto_file.read_bytes()
    calls: list[tuple[str, str]] = []

    def complete(system: str, user: str) -> str:
        calls.append((system, user))
        return json.dumps(
            {
                "assessment": "experimental",
                "evidence_used": ["unit.marker"],
                "limitations": [],
                "recommendations": ["look"],
                "selected_claim_ids": [],
            }
        )

    client, _, runner = _client(settings, llm_complete=complete)
    key = "super-secret-key-xyz"
    previous = os.environ.get("S3_COACH_NVIDIA_API_KEY")
    os.environ["S3_COACH_NVIDIA_API_KEY"] = key
    try:
        first = client.post(
            "/api/runs/lab-run/coach-experiment",
            json={"scenario_id": "death_episode:1.000"},
        )
        assert first.status_code == 200
        runner.join_workers()
        first_job = client.get(f"/api/jobs/{first.json()['job_id']}").json()
        assert first_job["status"] == "completed"
        second = client.post(
            "/api/runs/lab-run/coach-experiment",
            json={"scenario_id": "death_episode:1.000"},
        )
        runner.join_workers()
        second_job = client.get(f"/api/jobs/{second.json()['job_id']}").json()
        assert second_job["status"] == "completed"
    finally:
        if previous is None:
            os.environ.pop("S3_COACH_NVIDIA_API_KEY", None)
        else:
            os.environ["S3_COACH_NVIDIA_API_KEY"] = previous

    assert len(calls) == 2
    assert calls[0][0] == load_system_prompt()
    assert "rank-seven-marker" in calls[0][1]
    assert "false" in calls[0][1]
    listed = client.get(
        "/api/runs/lab-run/coach-experiments",
        params={"scenario_id": "death_episode:1.000"},
    ).json()
    assert len(listed) == 2
    assert listed[0]["experiment_id"] != listed[1]["experiment_id"]
    assert listed[0]["experiment"] is True
    assert listed[0]["result"]["assessment"] == "experimental"
    assert key not in json.dumps(listed)
    inputs_after = {
        path.relative_to(folder / "coach_inputs").as_posix(): path.read_bytes()
        for path in (folder / "coach_inputs").rglob("*")
        if path.is_file()
    }
    assert inputs_after == inputs_before
    assert proto_file.read_bytes() == proto_before
    saved_view = listed[-1]["coach_llm_view"]
    saved_prompt = listed[-1]["serialized_user_prompt"]
    replaced = json.loads(json.dumps(view))
    replaced["unit"]["marker"] = "changed-later"
    (folder / "coach_inputs" / "death_episode_1.000.llm_view.json").write_text(
        json.dumps(replaced),
        encoding="utf-8",
    )
    again = client.get(
        "/api/runs/lab-run/coach-experiments",
        params={"scenario_id": "death_episode:1.000"},
    ).json()
    target_id = listed[-1]["experiment_id"]
    older = next(item for item in again if item["experiment_id"] == target_id)
    assert older["coach_llm_view"] == saved_view
    assert older["serialized_user_prompt"] == saved_prompt
    assert "changed-later" not in older["serialized_user_prompt"]


def test_delete_run_removes_analysis_and_an_unshared_upload(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_run(settings, "run-a")
    _write_run(settings, "run-b")
    _write_run(settings, "legacy")
    movie = settings.movies_dir / "legacy.mov"
    movie.write_bytes(b"movie-bytes")
    same_name = settings.movies_dir / "run-a.mp4"
    same_name.write_bytes(b"not-the-upload")
    client, store, _ = _client(settings)
    video = store.save_upload("match.mp4", b"1234")
    store.write_link("run-a", video.id, "job-a", "alpha.mp4")
    store.write_link("run-b", video.id, "job-b", "beta.mp4")
    first = client.delete("/api/runs/run-a")
    assert first.status_code == 200
    assert not (settings.analysis_root / "run-a").exists()
    assert store.video_path(video.id) is not None
    assert same_name.is_file()
    second = client.delete("/api/runs/run-b")
    assert second.status_code == 200
    assert store.video_path(video.id) is None
    assert client.delete("/api/runs/legacy").status_code == 200
    assert movie.is_file()
    assert not (settings.analysis_root / "legacy").exists()


def test_delete_refuses_while_a_job_is_running(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_run(settings, "run-a")
    client, store, _ = _client(settings)
    job = store.create_run_job("run-a", "coach")
    job.status = "running"
    store.save_job(job)
    response = client.delete("/api/runs/run-a")
    assert response.status_code == 409
    assert (settings.analysis_root / "run-a" / "vision_manifest.json").is_file()


def test_vmv_site_is_a_cli_command() -> None:
    from typer.testing import CliRunner

    result = CliRunner().invoke(cli_app, ["--help"])
    assert result.exit_code == 0
    assert "vmv-site" in result.stdout
