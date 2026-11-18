"""Operator email when a review-code job is ready for its recording.

The message names the job and its payment route only: no review code,
customer address or token.
"""

from __future__ import annotations

import smtplib
import uuid
from email.message import EmailMessage

from loguru import logger

from public_site.settings import PublicSettings

SUBJECT = "New review-code submission – Video upload required"


def payment_label(row: dict) -> str:
    """How the job was authorized, from its gate row."""
    if row.get("payment_state") == "paid":
        return "Paid"
    if row.get("voucher_state") == "redeemed":
        return "Paid by voucher"
    return "Not required (administrator request)"


def alert_body(row: dict, base_url: str) -> str:
    """Plain-text alert for one job."""
    return (
        "New S3-coach review request\n"
        "Admin alert\n\n"
        "A customer has submitted a new Splatoon 3 review code.\n\n"
        f"- Job ID: {row['submission_id']}\n"
        "- Submission type: Review code\n"
        "- Status: Awaiting video upload\n"
        f"- Payment: {payment_label(row)}\n\n"
        "Action required\n\n"
        "Please visit Admin Recordings to review the request and upload the "
        "corresponding gameplay recording.\n"
        f"{base_url}/admin/recordings\n\n"
        "This is an administrative notification. No action has been taken on "
        "the recording.\n"
    )


class AdminAlertSender:
    """Send one alert to every configured admin address, or write it to the outbox."""

    def __init__(self, settings: PublicSettings) -> None:
        self.settings = settings
        self.outbox = settings.root / "outbox"

    @property
    def enabled(self) -> bool:
        """Alerts go out only when at least one admin address is configured."""
        return bool(self.settings.admin_alert_to)

    def send(self, row: dict) -> None:
        """Deliver one alert. Raises on a delivery failure so the caller can retry."""
        body = alert_body(row, self.settings.public_base_url)
        recipients = ", ".join(self.settings.admin_alert_to)
        if self.settings.smtp_host:
            self._smtp(recipients, body)
        else:
            self.outbox.mkdir(parents=True, exist_ok=True)
            path = self.outbox / f"admin_alert.{uuid.uuid4().hex}.txt"
            path.write_text(
                f"To: {recipients}\nSubject: {SUBJECT}\n\n{body}", encoding="utf-8"
            )
            path.chmod(0o600)
        logger.info("admin alert sent for submission {}", row["submission_id"])

    def _smtp(self, recipients: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"] = SUBJECT
        message["From"] = self.settings.smtp_from or self.settings.smtp_user
        message["To"] = recipients
        message.set_content(body)
        with smtplib.SMTP(
            self.settings.smtp_host, self.settings.smtp_port, timeout=30
        ) as smtp:
            smtp.starttls()
            if self.settings.smtp_user:
                smtp.login(self.settings.smtp_user, self.settings.smtp_password)
            smtp.send_message(message)
