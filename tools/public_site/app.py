"""Public coaching API. Submissions are not operator jobs."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from public_site.errors import RequestRejected
from public_site.models import PublicConfig
from public_site.notices import NoticeSender
from public_site.service import SubmissionService
from public_site.settings import PublicSettings
from public_site.storage import LocalStorage, R2Storage
from public_site.store import PublicStore
from public_site.turnstile import build_verifier
from public_site.worker import Command, PublicWorker, subprocess_run


class CreateBody(BaseModel):
    """Fields the submit page may send. Review Code is not one of them."""

    model_config = ConfigDict(extra="ignore")

    turnstile_token: str = Field(min_length=1)
    filename: str = Field(min_length=1, max_length=200)
    size_bytes: int = Field(ge=1)
    content_type: str
    language: Literal["en", "ja"] = "en"
    email: str | None = None
    notify: bool = False
    display_name: str | None = None


def create_app(
    settings: PublicSettings | None = None,
    *,
    store: PublicStore | None = None,
    storage: LocalStorage | R2Storage | None = None,
    notices: NoticeSender | None = None,
    verifier=None,
    probe=None,
    run_command: Command | None = None,
    start_worker: bool = True,
) -> FastAPI:
    """Build the public API and, when a build exists, the React app."""
    settings = settings or PublicSettings.from_env()
    store = store or PublicStore(settings.root)
    storage = storage or open_storage(settings)
    notices = notices or NoticeSender(settings)
    service = SubmissionService(settings, store, storage, notices, probe=probe)
    if verifier is None:
        verifier = build_verifier(settings)
    worker = PublicWorker(
        settings,
        store,
        service,
        run_command=run_command or subprocess_run,
        start_thread=start_worker,
    )
    app = FastAPI(title="Splatoon 3 AI Coach")
    app.state.settings = settings
    app.state.store = store
    app.state.service = service
    app.state.worker = worker
    _routes(app, settings, service, storage, verifier, worker)
    _mount_ui(app, settings.static_dir)
    return app


def open_storage(settings: PublicSettings) -> LocalStorage | R2Storage:
    """Local disk, or R2 when configured. The dev upload route follows this choice."""
    if settings.storage == "r2":
        return R2Storage(
            account_id=settings.r2_account_id,
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key,
            bucket=settings.r2_bucket,
            url_seconds=settings.upload_url_seconds,
            cache_dir=settings.root / "cache",
        )
    return LocalStorage(settings.root, url_seconds=settings.upload_url_seconds)


def _routes(app, settings, service, storage, verifier, worker) -> None:
    @app.get("/api/config")
    def get_config() -> dict:
        payload = PublicConfig(
            turnstile_site_key=settings.turnstile_site_key,
            dev_mode=settings.dev_mode,
            max_video_bytes=settings.max_video_bytes,
            max_duration_seconds=int(settings.max_duration_seconds),
        )
        return payload.model_dump()

    @app.post("/api/submissions")
    def post_submission(body: CreateBody, request: Request) -> dict:
        try:
            created = service.create(
                turnstile_token=body.turnstile_token,
                filename=body.filename,
                size_bytes=body.size_bytes,
                content_type=body.content_type,
                language=body.language,
                email=body.email,
                notify=body.notify,
                display_name=body.display_name,
                ip=_client_ip(request, settings),
                verifier=verifier,
            )
        except RequestRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return created.model_dump()

    @app.post("/api/submissions/{token}/uploaded")
    def post_uploaded(token: str) -> dict:
        try:
            view = service.confirm(token)
        except RequestRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        worker.wake()
        return view.model_dump()

    @app.get("/api/submissions/{token}")
    def get_submission(token: str) -> dict:
        try:
            view = service.view(token)
        except RequestRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return view.model_dump()

    @app.get("/api/submissions/{token}/moments/{index}/frame")
    def get_moment_frame(token: str, index: int) -> FileResponse:
        try:
            path = service.moment_frame(token, index)
        except RequestRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return FileResponse(path, media_type="image/jpeg")

    _register_dev_upload(app, storage)


def _register_dev_upload(app: FastAPI, storage) -> None:
    """Local PUT route. Not registered when storage is R2."""
    if not storage.allows_dev_upload:
        return

    @app.put("/api/dev-upload/{grant_id}")
    async def put_dev_upload(grant_id: str, request: Request) -> dict:
        body = await request.body()
        try:
            storage.write_grant(grant_id, body)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=413, detail="That video is too large.") from exc
        return {"stored": True}


def _client_ip(request: Request, settings: PublicSettings) -> str:
    if settings.trust_proxy:
        for header in ("cf-connecting-ip", "x-real-ip"):
            forwarded = request.headers.get(header)
            if forwarded:
                return forwarded.split(",")[0].strip()
    if request.client is None:
        return "unknown"
    return request.client.host


def _mount_ui(app: FastAPI, static_dir: Path) -> None:
    index = static_dir / "index.html"
    if not index.is_file():
        return
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.api_route("/", methods=["GET", "HEAD"])
    def ui_index() -> FileResponse:
        return FileResponse(index)

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"])
    def spa(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        root = static_dir.resolve()
        candidate = (static_dir / full_path).resolve()
        inside = candidate == root or root in candidate.parents
        if candidate.is_file() and inside:
            return FileResponse(candidate)
        return FileResponse(index)
