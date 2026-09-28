"""Public coaching API. Submissions are not operator jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from public_site.accounts import Account, AccountStore, account_email
from public_site.errors import ACCOUNTS_UNAVAILABLE, LOGIN_REQUIRED, RequestRejected
from public_site.models import HistoryItem, PublicConfig, Submission
from public_site.notices import NoticeSender
from public_site.service import SubmissionService
from public_site.settings import PublicSettings
from public_site.share_page import review_html, review_token
from public_site.storage import LocalStorage, R2Storage
from public_site.store import PublicStore
from public_site.tokens import hash_token, new_token
from public_site.turnstile import build_verifier
from public_site.worker import Command, PublicWorker, subprocess_run

SESSION_COOKIE = "s3_session"
_SESSION_MAX_AGE = 30 * 24 * 60 * 60


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


class RegisterBody(BaseModel):
    """Name, email, and password for a new account."""

    model_config = ConfigDict(extra="ignore")

    name: str = ""
    email: str = ""
    password: str = ""


class LoginBody(BaseModel):
    """Email and password for an existing account."""

    model_config = ConfigDict(extra="ignore")

    email: str = ""
    password: str = ""


class ForgotBody(BaseModel):
    """Address that may receive a reset link."""

    model_config = ConfigDict(extra="ignore")

    email: str = ""


class FeedbackBody(BaseModel):
    """A note about one finished review."""

    model_config = ConfigDict(extra="ignore")

    body: str = ""


class TokenBody(BaseModel):
    """A verify or reset token from an email link."""

    model_config = ConfigDict(extra="ignore")

    token: str = ""
    password: str = ""


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
    accounts: AccountStore | None = None,
) -> FastAPI:
    """Build the public API and, when a build exists, the React app."""
    settings = settings or PublicSettings.from_env()
    store = store or PublicStore(settings.root)
    storage = storage or open_storage(settings)
    notices = notices or NoticeSender(settings)
    if accounts is None and settings.database_url:
        accounts = AccountStore(settings.database_url)
        accounts.ensure_schema()
    service = SubmissionService(
        settings, store, storage, notices, probe=probe, accounts=accounts
    )
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
    app.state.accounts = accounts
    _routes(app, settings, service, storage, verifier, worker, accounts, notices)
    _mount_ui(app, settings, service)
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


def _routes(app, settings, service, storage, verifier, worker, accounts, notices) -> None:
    @app.get("/api/config")
    def get_config() -> dict:
        payload = PublicConfig(
            turnstile_site_key=settings.turnstile_site_key,
            dev_mode=settings.dev_mode,
            max_video_bytes=settings.max_video_bytes,
            max_duration_seconds=int(settings.max_duration_seconds),
            feedback=accounts is not None,
        )
        return payload.model_dump()

    @app.post("/api/submissions")
    def post_submission(body: CreateBody, request: Request) -> dict:
        user = _current_user(request, accounts)
        email = body.email
        notify = body.notify
        display_name = body.display_name
        user_id = None
        retention = None
        if user is not None:
            email = user.email
            notify = True
            display_name = _chosen_name(body.display_name, user.name)
            user_id = user.id
            retention = settings.account_retention_days
        try:
            created = service.create(
                turnstile_token=body.turnstile_token,
                filename=body.filename,
                size_bytes=body.size_bytes,
                content_type=body.content_type,
                language=body.language,
                email=email,
                notify=notify,
                display_name=display_name,
                ip=_client_ip(request, settings),
                verifier=verifier,
                user_id=user_id,
                retention_days=retention,
            )
        except RequestRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return created.model_dump()

    _auth_routes(app, settings, service, accounts, notices)

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

    @app.post("/api/submissions/{token}/feedback")
    def post_guest_feedback(token: str, body: FeedbackBody) -> dict:
        submission = service.store.get_by_hash(hash_token(token))
        if submission is None:
            raise HTTPException(status_code=404, detail="not found")
        return _save_feedback(accounts, submission.id, None, body.body)

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


def _auth_routes(app, settings, service, accounts, notices) -> None:
    @app.post("/api/auth/register")
    def post_register(body: RegisterBody) -> dict:
        store = _require_accounts(accounts)
        now = datetime.now(UTC)
        try:
            email = account_email(body.email)
            token = store.register(name=body.name, email=email, password=body.password, now=now)
            notices.send_account(email, "verify", f"{settings.public_base_url}/verify/{token}")
        except RequestRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return {"sent": True}

    @app.post("/api/auth/verify")
    def post_verify(body: TokenBody) -> JSONResponse:
        store = _require_accounts(accounts)
        try:
            account, token = store.verify(body.token, datetime.now(UTC))
        except RequestRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return _session_response(account, token, settings)

    @app.post("/api/auth/login")
    def post_login(body: LoginBody) -> JSONResponse:
        store = _require_accounts(accounts)
        try:
            account, token = store.login(
                email=body.email, password=body.password, now=datetime.now(UTC)
            )
        except RequestRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return _session_response(account, token, settings)

    @app.post("/api/auth/forgot")
    def post_forgot(body: ForgotBody) -> dict:
        store = _require_accounts(accounts)
        now = datetime.now(UTC)
        try:
            email = account_email(body.email)
            token = store.request_reset(email, now)
            if token:
                notices.send_account(email, "reset", f"{settings.public_base_url}/reset/{token}")
        except RequestRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return {"sent": True}

    @app.post("/api/auth/reset")
    def post_reset(body: TokenBody) -> JSONResponse:
        store = _require_accounts(accounts)
        try:
            account, token = store.reset_password(body.token, body.password, datetime.now(UTC))
        except RequestRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return _session_response(account, token, settings)

    @app.post("/api/auth/logout")
    def post_logout(request: Request) -> JSONResponse:
        store = _require_accounts(accounts)
        store.logout(request.cookies.get(SESSION_COOKIE, ""))
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/api/me")
    def get_me(request: Request) -> dict:
        user = _required_user(request, accounts)
        return {"name": user.name, "email": user.email}

    @app.get("/api/me/submissions")
    def get_mine(request: Request) -> list[dict]:
        user = _required_user(request, accounts)
        return _history_items(accounts, service.store, user.id)

    @app.post("/api/me/submissions/{submission_id}/share")
    def post_share(submission_id: str, request: Request) -> dict:
        user = _required_user(request, accounts)
        submission = _owned(accounts, service.store, user, submission_id)
        token = new_token()
        service.store.remember_token(submission.id, hash_token(token))
        return {"review_path": f"/review/{token}"}

    @app.get("/api/me/submissions/{submission_id}")
    def get_owned(submission_id: str, request: Request) -> dict:
        user = _required_user(request, accounts)
        submission = _owned(accounts, service.store, user, submission_id)
        return service.project(submission).model_dump()

    @app.post("/api/me/submissions/{submission_id}/feedback")
    def post_owned_feedback(submission_id: str, body: FeedbackBody, request: Request) -> dict:
        user = _required_user(request, accounts)
        submission = _owned(accounts, service.store, user, submission_id)
        return _save_feedback(accounts, submission.id, user.id, body.body)

    @app.get("/api/me/submissions/{submission_id}/moments/{index}/frame")
    def get_owned_frame(submission_id: str, index: int, request: Request) -> FileResponse:
        user = _required_user(request, accounts)
        submission = _owned(accounts, service.store, user, submission_id)
        try:
            path = service.frame_file(submission, index)
        except RequestRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return FileResponse(path, media_type="image/jpeg")


def _save_feedback(
    accounts: AccountStore | None,
    submission_id: str,
    user_id: str | None,
    text: str,
) -> dict:
    store = _require_accounts(accounts)
    try:
        store.add_feedback(submission_id, user_id, text, datetime.now(UTC))
    except RequestRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"sent": True}


def _require_accounts(accounts: AccountStore | None) -> AccountStore:
    if accounts is None:
        raise HTTPException(status_code=503, detail=ACCOUNTS_UNAVAILABLE)
    return accounts


def _required_user(request: Request, accounts: AccountStore | None) -> Account:
    user = _current_user(request, accounts)
    if user is None:
        raise HTTPException(status_code=401, detail=LOGIN_REQUIRED)
    return user


def _current_user(request: Request, accounts: AccountStore | None) -> Account | None:
    if accounts is None:
        return None
    token = request.cookies.get(SESSION_COOKIE, "")
    return accounts.user_from_session(token, datetime.now(UTC))


def _session_response(account: Account, token: str, settings: PublicSettings) -> JSONResponse:
    response = JSONResponse({"name": account.name, "email": account.email})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.public_base_url.startswith("https://"),
        max_age=_SESSION_MAX_AGE,
        path="/",
    )
    return response


def _chosen_name(given: str | None, account_name: str) -> str:
    text = " ".join((given or "").split())
    return text or account_name


def _owned(accounts: AccountStore, store: PublicStore, user: Account, submission_id: str) -> Submission:
    if not accounts.owns(user.id, submission_id):
        raise HTTPException(status_code=404, detail="not found")
    submission = store.get(submission_id)
    if submission is None or submission.user_id != user.id:
        raise HTTPException(status_code=404, detail="not found")
    return submission


def _history_items(accounts: AccountStore, store: PublicStore, user_id: str) -> list[dict]:
    items = []
    for row in accounts.list_history(user_id):
        submission = store.get(row.submission_id)
        if submission is None or submission.user_id != user_id:
            continue
        items.append(
            HistoryItem(
                id=submission.id,
                created_at=submission.created_at,
                expires_at=submission.expires_at,
                status=submission.status,
                step=submission.step,
                display_name=submission.display_name,
            ).model_dump()
        )
    return items


def _client_ip(request: Request, settings: PublicSettings) -> str:
    if settings.trust_proxy:
        for header in ("cf-connecting-ip", "x-real-ip"):
            forwarded = request.headers.get(header)
            if forwarded:
                return forwarded.split(",")[0].strip()
    if request.client is None:
        return "unknown"
    return request.client.host


def _mount_ui(app: FastAPI, settings: PublicSettings, service: SubmissionService) -> None:
    static_dir = settings.static_dir
    index = static_dir / "index.html"
    if not index.is_file():
        return
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.api_route("/", methods=["GET", "HEAD"])
    def ui_index() -> FileResponse:
        return FileResponse(index)

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], response_model=None)
    def spa(full_path: str) -> FileResponse | HTMLResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        root = static_dir.resolve()
        candidate = (static_dir / full_path).resolve()
        inside = candidate == root or root in candidate.parents
        if candidate.is_file() and inside:
            return FileResponse(candidate)
        token = review_token(full_path)
        if token:
            page = review_html(service, settings, index.read_text(encoding="utf-8"), token)
            if page is not None:
                return HTMLResponse(page)
        return FileResponse(index)
