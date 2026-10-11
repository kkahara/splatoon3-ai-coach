"""Public coaching API. Submissions are not operator jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from public_site.accounts import Account, AccountStore, account_email
from public_site.errors import (
    ACCOUNTS_UNAVAILABLE,
    LOGIN_REQUIRED,
    PAYMENT_UNAVAILABLE,
    RequestRejected,
)
from public_site.localize import Translator, build_translator
from public_site.models import DisplayLocale, HistoryItem, PublicConfig, Submission
from public_site.notices import NoticeSender
from public_site.payments import PaymentStore
from public_site.service import SubmissionService
from public_site.settings import PublicSettings
from public_site.share_page import review_html, review_token
from public_site.storage import LocalStorage, R2Storage
from public_site.store import PublicStore
from public_site.stripe_checkout import (
    CheckoutGateway,
    CheckoutSignatureError,
    StripeCheckout,
)
from public_site.telemetry import TelemetryStore
from public_site.tokens import hash_ip, hash_token, new_token
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
    # Game display language (selects HUD templates); never defaulted from site locale.
    language: Literal["en", "ja"]
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


class PriceBody(BaseModel):
    """A new per-analysis price in US cents."""

    model_config = ConfigDict(extra="ignore")

    price_cents: int = Field(ge=0, le=100_000)


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
    translator: Translator | None = None,
    telemetry: TelemetryStore | None = None,
    payments: PaymentStore | None = None,
    checkout: CheckoutGateway | None = None,
) -> FastAPI:
    """Build the public API and, when a build exists, the React app."""
    settings = settings or PublicSettings.from_env()
    store = store or PublicStore(settings.root)
    storage = storage or open_storage(settings)
    notices = notices or NoticeSender(settings)
    if accounts is None and settings.database_url:
        accounts = AccountStore(settings.database_url)
        accounts.ensure_schema()
    if telemetry is None and settings.database_url:
        telemetry = TelemetryStore(settings.database_url)
        telemetry.ensure_schema()
    if payments is None and settings.database_url:
        payments = PaymentStore(settings.database_url)
        payments.ensure_schema()
    if checkout is None and settings.stripe_secret_key:
        checkout = StripeCheckout(settings.stripe_secret_key)
    if translator is None:
        translator = build_translator(settings.config_path, settings.llm_provider)
    service = SubmissionService(
        settings,
        store,
        storage,
        notices,
        probe=probe,
        accounts=accounts,
        translator=translator,
        telemetry=telemetry,
        payments=payments,
        checkout=checkout,
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
    app.state.telemetry = telemetry
    app.state.payments = payments
    _routes(app, settings, service, storage, verifier, worker, accounts, notices, payments, checkout)
    from public_site.admin import register_admin_routes

    register_admin_routes(
        app,
        settings=settings,
        accounts=accounts,
        telemetry=telemetry,
        store=store,
        require_admin=_required_admin,
    )
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


def _routes(app, settings, service, storage, verifier, worker, accounts, notices, payments, checkout) -> None:
    @app.get("/api/config")
    def get_config(request: Request) -> dict:
        return _public_config(request, settings, accounts, payments).model_dump()

    @app.post("/api/funnel/page-view")
    def page_view() -> dict:
        if payments is not None:
            payments.record_page_view()
        return {"recorded": payments is not None}

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

    @app.post("/api/submissions/{token}/checkout")
    def post_checkout(token: str) -> dict:
        try:
            started = service.start_checkout(token)
        except RequestRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return started

    @app.post("/api/stripe/webhook")
    async def stripe_webhook(request: Request) -> dict:
        return await _stripe_webhook(request, settings, service, worker, checkout)

    @app.get("/api/admin/price")
    def get_price(request: Request) -> dict:
        _required_admin(request, accounts)
        return _price_payload(payments)

    @app.put("/api/admin/price")
    def put_price(body: PriceBody, request: Request) -> dict:
        _required_admin(request, accounts)
        if payments is None:
            raise HTTPException(status_code=503, detail=PAYMENT_UNAVAILABLE)
        price = payments.set_price(body.price_cents)
        return {
            "price_cents": price.price_cents,
            "currency": price.currency,
            "pricing_version": price.pricing_version,
            "events": _event_rows(payments),
        }

    @app.get("/api/submissions/{token}")
    def get_submission(token: str, locale: DisplayLocale = "en") -> dict:
        try:
            view = service.view(token, locale)
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


def _public_config(request: Request, settings, accounts, payments) -> PublicConfig:
    """Submit-page config. Administrators are not asked to pay."""
    user = _current_user(request, accounts)
    admin = user is not None and user.role == "admin"
    price = payments.current_price() if payments is not None else None
    required = payments is not None and not admin and (price is None or price.price_cents > 0)
    return PublicConfig(
        turnstile_site_key=settings.turnstile_site_key,
        dev_mode=settings.dev_mode,
        max_video_bytes=None if admin else settings.max_video_bytes,
        max_duration_seconds=int(settings.max_duration_seconds),
        feedback=accounts is not None,
        price_cents=None if price is None else price.price_cents,
        currency=None if price is None else price.currency,
        pricing_version=None if price is None else price.pricing_version,
        payment_required=required,
    )


def _price_payload(payments: PaymentStore | None) -> dict:
    if payments is None:
        raise HTTPException(status_code=503, detail=PAYMENT_UNAVAILABLE)
    price = payments.current_price()
    return {
        "price_cents": None if price is None else price.price_cents,
        "currency": None if price is None else price.currency,
        "pricing_version": None if price is None else price.pricing_version,
        "events": _event_rows(payments),
    }


def _event_rows(payments: PaymentStore) -> list[dict]:
    return [
        {
            "pricing_version": row.pricing_version,
            "price_cents": row.price_cents,
            "kind": row.kind,
            "count": row.count,
        }
        for row in payments.event_counts()
    ]


async def _stripe_webhook(request: Request, settings, service, worker, checkout) -> dict:
    if checkout is None or not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail=PAYMENT_UNAVAILABLE)
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = checkout.construct_event(payload, signature, settings.stripe_webhook_secret)
    except CheckoutSignatureError as exc:
        raise HTTPException(status_code=400, detail="invalid signature") from exc
    result = service.apply_webhook(event)
    if result in {"amount", "association"}:
        raise HTTPException(status_code=400, detail=result)
    if result == "paid":
        worker.wake()
    return {"received": True, "result": result}


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
    def post_register(body: RegisterBody, request: Request) -> dict:
        store = _require_accounts(accounts)
        now = datetime.now(UTC)
        try:
            email = account_email(body.email)
            token = store.register(
                name=body.name,
                email=email,
                password=body.password,
                now=now,
                ip_hash=hash_ip(_client_ip(request, settings)),
                max_per_ip=settings.accounts_per_ip,
            )
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
        return {"name": user.name, "email": user.email, "role": user.role}

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
    def get_owned(submission_id: str, request: Request, locale: DisplayLocale = "en") -> dict:
        user = _required_user(request, accounts)
        submission = _owned(accounts, service.store, user, submission_id)
        return service.project(submission, locale).model_dump()

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


def _required_admin(request: Request, accounts: AccountStore | None) -> Account:
    """Require an authenticated administrator on the server."""
    user = _required_user(request, accounts)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="not authorized")
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
                outcome=submission.outcome,
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
