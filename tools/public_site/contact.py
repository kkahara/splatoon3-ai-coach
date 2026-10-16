"""Contact form delivery. The database and telemetry never receive these messages.

When SMTP is unset, the complete message is written to the local outbox on purpose.
"""

from __future__ import annotations

import smtplib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from loguru import logger

from public_site.accounts import account_email
from public_site.errors import (
    BAD_ACCOUNT_NAME,
    CONTACT_MAIL_FAILED,
    CONTACT_MESSAGE,
    CONTACT_MESSAGE_LONG,
    CONTACT_NAME_LONG,
    CONTACT_RATE_LIMIT,
    CONTACT_REASON,
    CONTACT_REFERENCE_LONG,
    CONTACT_UNAVAILABLE,
    CONTACT_VERIFY,
    RequestRejected,
)
from public_site.limits import RateLimiter
from public_site.settings import PublicSettings
from public_site.store import PublicStore
from public_site.tokens import hash_ip

REASONS = ("technical", "account_payment", "feedback", "collaboration", "other")
_LABELS = {
    "technical": "Technical problem",
    "account_payment": "Account or payment issue",
    "feedback": "Feedback / suggestion",
    "collaboration": "Collaboration",
    "other": "Other",
}
_NAME_LIMIT = 80
_MESSAGE_LIMIT = 4000
_REFERENCE_LIMIT = 300


@dataclass(frozen=True)
class ContactDraft:
    """Raw form fields. Validation happens after the honeypot check."""

    turnstile_token: str
    reason: str
    name: str
    email: str
    message: str
    reference: str
    leave_blank: str


@dataclass(frozen=True)
class ContactFields:
    """A message that passed validation."""

    reason: str
    name: str
    email: str
    message: str
    reference: str


def accept_contact(
    settings: PublicSettings,
    store: PublicStore,
    verifier: Callable[[str], bool],
    draft: ContactDraft,
    *,
    ip: str,
    now: datetime,
) -> None:
    """Check the form, then email it or write the outbox. A honeypot sends nothing."""
    if draft.leave_blank.strip():
        logger.info("contact honeypot discarded")
        return
    fields = _fields(draft)
    if not verifier(draft.turnstile_token):
        raise RequestRejected(400, CONTACT_VERIFY)
    _limit(settings, store, ip, now)
    deliver_contact(settings, fields)
    logger.info("contact message accepted")


def deliver_contact(settings: PublicSettings, fields: ContactFields) -> None:
    """Send one message. SMTP with no operator address fails before any connection."""
    subject = f"Splatoon 3 AI Coach contact: {_LABELS[fields.reason]}"
    body = _letter(fields)
    if settings.smtp_host:
        if not settings.contact_to:
            raise RequestRejected(503, CONTACT_UNAVAILABLE)
        _smtp(settings, fields, subject, body)
        return
    _write_outbox(settings.root / "outbox", settings, fields, subject, body)


def _fields(draft: ContactDraft) -> ContactFields:
    reason = draft.reason.strip()
    if reason not in REASONS:
        raise RequestRejected(400, CONTACT_REASON)
    return ContactFields(
        reason=reason,
        name=_name(draft.name),
        email=account_email(draft.email),
        message=_message(draft.message),
        reference=_reference(draft.reference),
    )


def _name(value: str) -> str:
    text = " ".join(value.split())
    if not text or any(ord(char) < 32 for char in text):
        raise RequestRejected(400, BAD_ACCOUNT_NAME)
    if len(text) > _NAME_LIMIT:
        raise RequestRejected(400, CONTACT_NAME_LONG)
    return text


def _message(value: str) -> str:
    text = value.strip()
    if not text or _blocked(text):
        raise RequestRejected(400, CONTACT_MESSAGE)
    if len(text) > _MESSAGE_LIMIT:
        raise RequestRejected(400, CONTACT_MESSAGE_LONG)
    return text


def _reference(value: str) -> str:
    text = value.strip()
    if _blocked(text) or len(text) > _REFERENCE_LIMIT:
        raise RequestRejected(400, CONTACT_REFERENCE_LONG)
    return text


def _blocked(text: str) -> bool:
    return any(ord(char) < 32 and char not in "\n\r\t" for char in text)


def _limit(settings: PublicSettings, store: PublicStore, ip: str, now: datetime) -> None:
    limiter = RateLimiter(
        store,
        limit=settings.contact_messages_per_hour,
        window_seconds=settings.submission_window_seconds,
    )
    if limiter.allow(f"contact:{hash_ip(ip)}", now):
        return
    logger.info("contact rate limited")
    raise RequestRejected(429, CONTACT_RATE_LIMIT)


def _letter(fields: ContactFields) -> str:
    lines = [f"Name: {fields.name}", f"Reason: {_LABELS[fields.reason]}"]
    if fields.reference:
        lines.append(f"Review link: {fields.reference}")
    lines.extend(["", fields.message])
    return "\n".join(lines)


def _write_outbox(
    outbox: Path,
    settings: PublicSettings,
    fields: ContactFields,
    subject: str,
    body: str,
) -> None:
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"contact.{uuid.uuid4().hex}.txt"
    text = f"To: {settings.contact_to}\nReply-To: {fields.email}\nSubject: {subject}\n\n{body}"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def _smtp(settings: PublicSettings, fields: ContactFields, subject: str, body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from or settings.smtp_user
    message["To"] = settings.contact_to
    message["Reply-To"] = fields.email
    message.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError):
        logger.warning("contact delivery failed")
        raise RequestRejected(502, CONTACT_MAIL_FAILED) from None
