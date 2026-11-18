"""Customer submission and operator exception routes for manual acquisition."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from public_site.accounts import Account
from public_site.errors import RequestRejected
from public_site.recording_intake import RecordingUploadConfirm, RecordingUploadRequest
from public_site.recording_models import (
    ContactCorrection,
    ExpectedVersion,
    RecordingAction,
    ReviewCodeDraft,
    ReviewVoucherCheck,
    UnpaidClose,
)
from public_site.recording_service import RecordingService
from public_site.vouchers import preview_payload


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

    @app.post("/api/review-code-submissions/validate-voucher")
    def validate_voucher(body: ReviewVoucherCheck, request: Request) -> JSONResponse:
        try:
            preview = recordings.service.preview_voucher(
                body.code, client_ip(request), "review_code"
            )
        except RequestRejected as exc:
            return _rejected(exc)
        return JSONResponse(preview_payload(preview))


def _register_verification(app, recordings, notice_cookie):
    @app.post("/api/submissions/{token}/verify-contact")
    def resend(token: str, request: Request) -> JSONResponse:
        try:
            arranged = recordings.resend_contact(
                token, request.cookies.get("notice_browser", "")
            )
        except RequestRejected as exc:
            return _rejected(exc)
        # Browser cooldown is explicit (429 + Retry-After). Other suppressions
        # stay 200 without claiming a send, so the per-address limit stays silent.
        if (
            arranged.mail_outcome == "cooldown"
            and arranged.retry_after_seconds is not None
            and arranged.retry_after_seconds > 0
        ):
            return _cooldown(arranged.retry_after_seconds)
        response = JSONResponse(
            {
                "notice_pending": arranged.pending,
                "confirm_mail_outcome": arranged.mail_outcome,
                "retry_after_seconds": arranged.retry_after_seconds,
            }
        )
        if arranged.cookie:
            notice_cookie(response, arranged.cookie)
        return response

    @app.post("/api/submissions/{token}/contact-email")
    def change(token: str, body: ContactCorrection, request: Request) -> JSONResponse:
        try:
            arranged = recordings.change_contact(
                token, body.email, request.cookies.get("notice_browser", "")
            )
        except RequestRejected as exc:
            return _rejected(exc)
        response = JSONResponse(
            {
                "notice_pending": arranged.pending,
                "confirm_mail_outcome": arranged.mail_outcome,
                "retry_after_seconds": arranged.retry_after_seconds,
            }
        )
        if arranged.cookie:
            notice_cookie(response, arranged.cookie)
        return response


def _cooldown(seconds: int) -> JSONResponse:
    """This browser asked for a confirmation email too recently. Nothing was sent."""
    return JSONResponse(
        {
            "detail": "Another confirmation email cannot be sent yet.",
            "reason": "confirm_cooldown",
            "confirm_mail_outcome": "cooldown",
            "retry_after_seconds": seconds,
        },
        status_code=429,
        headers={"Retry-After": str(seconds)},
    )


def _register_operator(app, recordings, required_admin):
    @app.get("/api/admin/recordings")
    def queue(request: Request) -> dict:
        required_admin(request)
        recordings.database.reconcile()
        return {
            "jobs": recordings.queue_jobs(),
            "unpaid": recordings.database.list_unpaid(),
        }

    @app.get("/api/admin/recordings/{submission_id}")
    def detail(submission_id: str, request: Request) -> JSONResponse:
        required_admin(request)
        try:
            return JSONResponse(
                jsonable_encoder(recordings.detail(submission_id))
            )
        except RequestRejected as exc:
            return _rejected(exc)

    @app.post("/api/admin/recordings/{submission_id}/actions")
    def action(
        submission_id: str, body: RecordingAction, request: Request
    ) -> JSONResponse:
        actor: Account = required_admin(request)
        try:
            recordings.act(submission_id, actor.id, body)
            recordings.refresh_projection(submission_id)
            return JSONResponse(
                jsonable_encoder(recordings.detail(submission_id))
            )
        except RequestRejected as exc:
            return _rejected(exc)

    @app.post("/api/admin/recordings/{submission_id}/close-unpaid")
    def close_unpaid(
        submission_id: str, body: UnpaidClose, request: Request
    ) -> JSONResponse:
        actor: Account = required_admin(request)
        try:
            closed = recordings.close_unpaid(
                submission_id, actor.id, body.expected_version, body.reason
            )
        except RequestRejected as exc:
            return _rejected(exc)
        return JSONResponse(
            {
                "state": "expired",
                "cancellation_email": closed["cancellation_email"],
                "email_sent": closed["email_sent"],
            }
        )

    _register_intake(app, recordings, required_admin)


def _register_intake(app, recordings, required_admin):
    @app.post("/api/admin/recordings/{submission_id}/upload")
    def upload(
        submission_id: str, body: RecordingUploadRequest, request: Request
    ) -> JSONResponse:
        actor: Account = required_admin(request)
        try:
            grant = recordings.intake.request_upload(submission_id, actor.id, body)
            return JSONResponse(jsonable_encoder(grant))
        except RequestRejected as exc:
            return _rejected(exc)

    @app.post("/api/admin/recordings/{submission_id}/upload/confirm")
    def confirm_upload(
        submission_id: str, body: RecordingUploadConfirm, request: Request
    ) -> JSONResponse:
        actor: Account = required_admin(request)
        try:
            recordings.intake.confirm_upload(submission_id, actor.id, body)
            return JSONResponse(
                jsonable_encoder(recordings.detail(submission_id))
            )
        except RequestRejected as exc:
            return _rejected(exc)

    @app.post("/api/admin/recordings/{submission_id}/upload/cancel")
    def cancel_upload(
        submission_id: str, body: ExpectedVersion, request: Request
    ) -> JSONResponse:
        actor: Account = required_admin(request)
        try:
            recordings.intake.abandon_upload(submission_id, actor.id, body.expected_version)
            return JSONResponse(
                jsonable_encoder(recordings.detail(submission_id))
            )
        except RequestRejected as exc:
            return _rejected(exc)


def _rejected(exc: RequestRejected) -> JSONResponse:
    body = {"detail": exc.detail}
    if exc.reason:
        body["reason"] = exc.reason
    return JSONResponse(body, status_code=exc.status_code)
