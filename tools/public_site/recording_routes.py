"""Customer submission and operator exception routes for manual acquisition."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from public_site.accounts import Account
from public_site.errors import RequestRejected
from public_site.recording_models import (
    RecordingAction,
    ReviewCodeCreated,
    ReviewCodeDraft,
)
from public_site.recording_service import RecordingService


def register_recording_routes(
    app: FastAPI,
    recordings: RecordingService,
    current_user: Callable,
    required_admin: Callable,
    client_ip: Callable,
    notice_cookie: Callable,
    verifier: Callable,
) -> None:
    """Register routes using the application's existing authorization helpers."""

    _register_creation(app, recordings, current_user, client_ip, notice_cookie, verifier)
    _register_verification(app, recordings, notice_cookie)
    _register_operator(app, recordings, required_admin)


def _register_creation(app, recordings, current_user, client_ip, notice_cookie, verifier):
    @app.post("/api/review-code-submissions")
    def create(body: ReviewCodeDraft, request: Request) -> JSONResponse:
        try:
            created, fresh = recordings.create(
                body,
                current_user(request),
                client_ip(request),
                verifier,
                request.cookies.get("notice_browser", ""),
            )
            response = JSONResponse(created.model_dump())
            if fresh:
                notice_cookie(response, fresh)
            return response
        except RequestRejected as exc:
            return _rejected(exc)


def _register_verification(app, recordings, notice_cookie):
    @app.post("/api/submissions/{token}/verify-contact")
    def resend(token: str, request: Request) -> JSONResponse:
        try:
            svc = recordings.service
            submission = svc._require(token)
            row = recordings.database.get(submission.id)
            if row is None:
                raise RequestRejected(404, "Review-code submission not found.")
            gate = svc.payments.get(submission.id)
            if gate is None or gate.status == "expired":
                raise RequestRejected(409, "Checkout has expired. Submit a new request.")
            pending, fresh = svc.arrange_guest_notice(
                ReviewCodeCreated(
                    token=token,
                    submission_id=submission.id,
                    review_path=f"/review/{token}",
                    price_cents=gate.price_cents,
                ),
                row["requested_email"],
                request.cookies.get("notice_browser", ""),
            )
            response = JSONResponse({"notice_pending": pending})
            if fresh:
                notice_cookie(response, fresh)
            return response
        except RequestRejected as exc:
            return _rejected(exc)


def _register_operator(app, recordings, required_admin):
    @app.get("/api/admin/recordings")
    def queue(request: Request) -> dict:
        required_admin(request)
        recordings.database.reconcile()
        return {"jobs": recordings.database.list_jobs()}

    @app.get("/api/admin/recordings/{submission_id}")
    def detail(submission_id: str, request: Request) -> JSONResponse:
        required_admin(request)
        try:
            return JSONResponse(
                jsonable_encoder(recordings.database.detail(submission_id))
            )
        except RequestRejected as exc:
            return _rejected(exc)

    @app.post("/api/admin/recordings/{submission_id}/actions")
    def action(
        submission_id: str, body: RecordingAction, request: Request
    ) -> JSONResponse:
        actor: Account = required_admin(request)
        try:
            recordings.database.act(submission_id, actor.id, body)
            recordings.refresh_projection(submission_id)
            return JSONResponse(
                jsonable_encoder(recordings.database.detail(submission_id))
            )
        except RequestRejected as exc:
            return _rejected(exc)


def _rejected(exc: RequestRejected) -> JSONResponse:
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
