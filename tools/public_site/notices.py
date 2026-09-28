"""Optional email notices. The review URL is only placed in the message body."""

from __future__ import annotations

import smtplib
import uuid
from email.message import EmailMessage
from pathlib import Path

from loguru import logger

from public_site.errors import MAIL_FAILED, RequestRejected
from public_site.models import Submission
from public_site.settings import PublicSettings

SUBJECTS = {
    "received": "We received your Splatoon 3 match",
    "ready": "Your Splatoon 3 coaching review is ready",
    "failed": "Your Splatoon 3 coaching review could not be completed",
}
ACCOUNT_SUBJECTS = {
    "verify": "Confirm your Splatoon 3 coaching account",
    "reset": "Reset your Splatoon 3 coaching password",
}


class NoticeSender:
    """Write or send a notice. Logs use the submission id, never the token."""

    def __init__(self, settings: PublicSettings) -> None:
        self.settings = settings
        self.outbox = settings.root / "outbox"
        self.secrets = settings.root / "notice_secrets"
        self.outbox.mkdir(parents=True, exist_ok=True)
        self.secrets.mkdir(parents=True, exist_ok=True)

    def seal(self, submission_id: str, token: str) -> None:
        """Keep the token off the submission JSON so a later notice can link it."""
        path = self._secret(submission_id)
        path.write_text(token, encoding="utf-8")
        path.chmod(0o600)

    def read_sealed(self, submission_id: str) -> str | None:
        """Return a sealed token, or None when this submission is not notified."""
        path = self._secret(submission_id)
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8").strip()
        return text or None

    def clear_sealed(self, submission_id: str) -> None:
        """Drop the sealed token after the terminal notice or on expiry."""
        self._secret(submission_id).unlink(missing_ok=True)

    def send(self, submission: Submission, kind: str, token: str) -> None:
        """Send one notice. Repeated kinds for the same submission are skipped."""
        if not submission.notify or not submission.email:
            return
        if kind in submission.notices_sent:
            return
        subject = SUBJECTS[kind]
        body = _body(kind, f"{self.settings.public_base_url}/review/{token}")
        if self.settings.smtp_host:
            _smtp(self.settings, submission.email, subject, body)
        else:
            self._write_outbox(submission.id, kind, submission.email, subject, body)
        submission.notices_sent.append(kind)
        logger.info("notice {} sent for submission {}", kind, submission.id)

    def send_account(self, email: str, kind: str, url: str) -> None:
        """Send a verify or reset link. The raw token stays in the message body."""
        subject = ACCOUNT_SUBJECTS[kind]
        body = _account_body(kind, url)
        if self.settings.smtp_host:
            try:
                _smtp(self.settings, email, subject, body)
            except smtplib.SMTPException:
                logger.exception("account notice {} failed", kind)
                raise RequestRejected(502, MAIL_FAILED) from None
        else:
            name = f"account.{kind}.{uuid.uuid4().hex}.txt"
            text = f"To: {email}\nSubject: {subject}\n\n{body}"
            (self.outbox / name).write_text(text, encoding="utf-8")
        logger.info("account notice {} sent", kind)

    def _write_outbox(
        self,
        submission_id: str,
        kind: str,
        email: str,
        subject: str,
        body: str,
    ) -> None:
        path = self.outbox / f"{submission_id}.{kind}.txt"
        path.write_text(f"To: {email}\nSubject: {subject}\n\n{body}", encoding="utf-8")

    def _secret(self, submission_id: str) -> Path:
        return self.secrets / submission_id


def _account_body(kind: str, url: str) -> str:
    if kind == "verify":
        lead = "Confirm your email address to finish registration."
    else:
        lead = "Reset your password."
    return f"{lead}\n\n{url}\n"


def _body(kind: str, review_url: str) -> str:
    if kind == "received":
        lead = "We received your match."
    elif kind == "ready":
        lead = "Your Splatoon 3 coaching review is ready."
    else:
        lead = "Your Splatoon 3 coaching review could not be completed."
    return f"{lead}\n\nView your review\n{review_url}\n"


def _smtp(settings: PublicSettings, email: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from or settings.smtp_user
    message["To"] = email
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)
