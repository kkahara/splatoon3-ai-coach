"""FastAPI application. Domain routes are independent of the React UI."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from vision_manifest_viewer.loader import load_manifest_view
from vision_manifest_viewer.server import parse_byte_range, review_video_content_type
from vision_manifest_viewer.special_review import (
    load_review_queue_for_run,
    resolve_review_queue_path,
)

from vmv_site.catalog import (
    RunBusy,
    list_runs,
    remove_run,
    resolve_media,
    resolve_run_video,
    run_dir,
    summarize_run,
)
from vmv_site.coach_layers import coach_layers
from vmv_site.experiments import list_experiments
from vmv_site.ground_truth import (
    GroundTruthFile,
    read_ground_truth,
    write_ground_truth,
)
from vmv_site.jobs import JobRunner
from vmv_site.models import RunSummary
from vmv_site.scenario_review import (
    ScenarioReviewFile,
    read_scenario_review,
    write_scenario_review,
)
from vmv_site.settings import PlatformSettings
from vmv_site.store import PlatformStore


class JobCreate(BaseModel):
    """Request to analyze a previously uploaded video."""

    video_id: str
    language: str = Field(pattern="^(en|ja)$")


class ExperimentCreate(BaseModel):
    """Request to send one saved CoachLlmView to the LLM."""

    scenario_id: str = Field(min_length=1)


def create_app(
    settings: PlatformSettings | None = None,
    *,
    store: PlatformStore | None = None,
    runner: JobRunner | None = None,
    start_worker: bool = True,
) -> FastAPI:
    """Build the API and, when a build exists, serve the React app."""
    settings = settings or PlatformSettings.from_env()
    store = store or PlatformStore(settings)
    if runner is None:
        runner = JobRunner(store, start_thread=start_worker)
    app = FastAPI(title="Local analysis platform")
    app.state.settings = settings
    app.state.store = store
    app.state.runner = runner

    @app.get("/api/videos")
    def get_videos() -> list[dict]:
        return [item.model_dump() for item in store.list_videos()]

    @app.post("/api/videos")
    async def post_video(file: UploadFile = File(...)) -> dict:
        data = await file.read()
        try:
            record = store.save_upload(file.filename or "upload", data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return record.model_dump()

    @app.get("/api/videos/{video_id}")
    def get_video(video_id: str) -> dict:
        record = store.get_video(video_id)
        if record is None:
            raise HTTPException(status_code=404, detail="video not found")
        return record.model_dump()

    @app.get("/api/jobs")
    def get_jobs() -> list[dict]:
        return [item.model_dump() for item in store.list_jobs()]

    @app.post("/api/jobs")
    def post_job(body: JobCreate) -> dict:
        try:
            job = store.create_job(body.video_id, body.language)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="video not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        runner.wake()
        runner.pump_available()
        current = store.get_job(job.id) or job
        return {"job_id": current.id, "status": current.status, "video_id": current.video_id}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job.model_dump()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict:
        try:
            job = runner.cancel(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc
        return job.model_dump()

    @app.get("/api/runs")
    def get_runs() -> list[dict]:
        return [item.model_dump() for item in list_runs(store)]

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        summary = _require_run(store, run_id)
        view = _manifest_view(settings, store, run_id)
        return {"run": summary.model_dump(), "view": view}

    @app.delete("/api/runs/{run_id}")
    def delete_run(run_id: str) -> dict:
        try:
            remove_run(store, run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except RunBusy as exc:
            raise HTTPException(
                status_code=409,
                detail="A job for this run is still queued or running.",
            ) from exc
        return {"deleted": run_id}

    @app.get("/api/runs/{run_id}/media/{relpath:path}")
    def get_media(run_id: str, relpath: str) -> FileResponse:
        _require_run(store, run_id)
        try:
            path = resolve_media(settings, run_id, relpath)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="media not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return FileResponse(path)

    @app.get("/api/runs/{run_id}/video")
    def get_run_video(run_id: str, request: Request) -> Response:
        summary = _require_run(store, run_id)
        if not summary.video_available:
            raise HTTPException(status_code=404, detail="video unavailable")
        path = resolve_run_video(store, run_id, summary.video_id)
        if path is None:
            raise HTTPException(status_code=404, detail="video unavailable")
        return _ranged_file(path, request.headers.get("range"))

    @app.get("/api/runs/{run_id}/ground-truth")
    def get_gt(run_id: str) -> dict:
        _require_run(store, run_id)
        try:
            return read_ground_truth(settings, run_id).model_dump()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc

    @app.put("/api/runs/{run_id}/ground-truth")
    def put_gt(run_id: str, body: GroundTruthFile) -> dict:
        _require_run(store, run_id)
        try:
            saved = write_ground_truth(settings, run_id, body)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return saved.model_dump()

    @app.get("/api/runs/{run_id}/scenario-review")
    def get_review(run_id: str) -> dict:
        _require_run(store, run_id)
        return read_scenario_review(settings, run_id).model_dump()

    @app.put("/api/runs/{run_id}/scenario-review")
    def put_review(run_id: str, body: ScenarioReviewFile) -> dict:
        _require_run(store, run_id)
        try:
            saved = write_scenario_review(settings, run_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return saved.model_dump()

    @app.post("/api/runs/{run_id}/coach")
    def post_coach(run_id: str) -> dict:
        _require_run(store, run_id)
        job = store.create_run_job(run_id, "coach")
        return _queued_job(store, runner, job.id, run_id)

    @app.post("/api/runs/{run_id}/coach-llm")
    def post_coach_llm(run_id: str) -> dict:
        _require_run(store, run_id)
        job = store.create_run_job(run_id, "coach-llm")
        return _queued_job(store, runner, job.id, run_id)

    @app.get("/api/runs/{run_id}/coach-layers")
    def get_layers(run_id: str, scenario_id: str) -> dict:
        _require_run(store, run_id)
        if not scenario_id:
            raise HTTPException(status_code=422, detail="scenario_id is required")
        return coach_layers(settings, run_id, scenario_id)

    @app.get("/api/runs/{run_id}/coach-experiments")
    def get_experiments(run_id: str, scenario_id: str) -> list[dict]:
        _require_run(store, run_id)
        if not scenario_id:
            raise HTTPException(status_code=422, detail="scenario_id is required")
        return [item.model_dump() for item in list_experiments(settings, run_id, scenario_id)]

    @app.post("/api/runs/{run_id}/coach-experiment")
    def post_experiment(run_id: str, body: ExperimentCreate) -> dict:
        _require_run(store, run_id)
        job = store.create_run_job(run_id, "coach-experiment", scenario_id=body.scenario_id)
        return _queued_job(store, runner, job.id, run_id)

    _mount_ui(app, settings.static_dir)
    return app


def _queued_job(store: PlatformStore, runner: JobRunner, job_id: str, run_id: str) -> dict:
    """Start the job if a slot is free and return the current record."""
    runner.wake()
    runner.pump_available()
    current = store.get_job(job_id)
    status = current.status if current is not None else "queued"
    return {"job_id": job_id, "status": status, "run_id": run_id}


def _require_run(store: PlatformStore, run_id: str) -> RunSummary:
    """Return run metadata, or 404 when the analysis folder has no manifest."""
    try:
        folder = run_dir(store.settings, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    if not (folder / "vision_manifest.json").is_file():
        raise HTTPException(status_code=404, detail="run not found")
    return summarize_run(store, run_id)


def _manifest_view(settings: PlatformSettings, store: PlatformStore, run_id: str) -> dict:
    folder = run_dir(settings, run_id)
    view = load_manifest_view(folder / "vision_manifest.json", config_path=settings.config_path)
    view.summary.video_label = run_id
    view.analysis_dir = ""
    view.manifest_path = run_id
    view.review_queue_path = ""
    queue = resolve_review_queue_path(None)
    if queue is not None:
        view.review_queue = load_review_queue_for_run(queue, run=run_id)
    summary = summarize_run(store, run_id)
    video_url = f"/api/runs/{quote(run_id)}/video"
    view.review_video_url = video_url if summary.video_available else ""
    _hide_server_paths(view)
    return view.model_dump(mode="json")


def _hide_server_paths(view) -> None:
    """Drop absolute filesystem paths from the viewer payload."""
    for obs in view.observations:
        if obs.frame_path and Path(obs.frame_path).is_absolute():
            obs.frame_path = None
    for item in view.review_queue:
        if item.strip_path and Path(item.strip_path).is_absolute():
            item.strip_path = None


def _mount_ui(app: FastAPI, static_dir: Path) -> None:
    """Serve the built React app. API routes registered earlier keep priority."""
    index = static_dir / "index.html"
    if not index.is_file():
        return
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    def ui_index() -> FileResponse:
        return FileResponse(index)

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        root = static_dir.resolve()
        candidate = (static_dir / full_path).resolve()
        inside = candidate == root or root in candidate.parents
        if candidate.is_file() and inside:
            return FileResponse(candidate)
        return FileResponse(index)


def _ranged_file(path: Path, range_header: str | None) -> Response:
    size = path.stat().st_size
    content_type = review_video_content_type(path)
    headers = {"Accept-Ranges": "bytes", "Content-Type": content_type}
    if not range_header:
        return StreamingResponse(_file_chunks(path, 0, size - 1), headers={**headers, "Content-Length": str(size)})
    requested = parse_byte_range(range_header, size)
    if requested is None:
        return Response(
            status_code=416,
            headers={**headers, "Content-Range": f"bytes */{size}", "Content-Length": "0"},
        )
    start, end = requested
    length = end - start + 1
    return StreamingResponse(
        _file_chunks(path, start, end),
        status_code=206,
        headers={
            **headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(length),
        },
    )


def _file_chunks(path: Path, start: int, end: int):
    remaining = end - start + 1
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(64 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
