"""Private acquisition records and narrow review-code API models."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AcquisitionState = Literal[
    "pending_payment",
    "awaiting_recording",
    "recording",
    "uploading",
    "ready",
    "recording_failed",
    "expired",
    "closed",
]


def normalize_code(value: str) -> str:
    """Normalize case, spaces and ASCII hyphens; reject other formatting."""
    if not re.fullmatch(r"[A-Za-z0-9\s-]+", value, flags=re.ASCII):
        raise ValueError(
            "Use 16 letters/digits, optionally separated by spaces or hyphens."
        )
    compact = re.sub(r"[\s-]", "", value, flags=re.ASCII).upper()
    if not re.fullmatch(r"[A-Z0-9]{16}", compact):
        raise ValueError("Review code must contain 16 letters/digits.")
    return "-".join(compact[i : i + 4] for i in range(0, 16, 4))


class ReviewCodeDraft(BaseModel):
    """Customer input; format acceptance does not establish replay availability."""

    model_config = ConfigDict(extra="forbid")
    turnstile_token: str = Field(min_length=1, max_length=4096)
    review_code: str = Field(max_length=100)
    email: str | None = Field(default=None, max_length=254)
    voucher_code: str | None = Field(default=None, max_length=200)

    @field_validator("review_code")
    @classmethod
    def code_format(cls, value: str) -> str:
        """Apply syntax validation only."""
        return normalize_code(value)


class ReviewCodeCreated(BaseModel):
    """One-time access token and payment information; no upload grant."""

    token: str
    submission_id: str
    review_path: str
    payment_required: bool = True
    price_cents: int
    notice_pending: bool = False
    voucher_state: Literal["pending", "redeemed", "failed"] | None = None
    confirm_mail_outcome: Literal["sent", "cooldown", "failed", "not_required"] | None = (
        None
    )
    confirm_mail_retry_after_seconds: int | None = None


class ContactCorrection(BaseModel):
    """A corrected contact address for a request that is still unconfirmed."""

    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)


class ReviewVoucherCheck(BaseModel):
    """Advisory voucher check for the review-code form. The route fixes the scope."""

    model_config = ConfigDict(extra="ignore")
    code: str = Field(default="", max_length=200)


class AcquisitionView(BaseModel):
    """Customer-safe acquisition status, independent of analysis status."""

    price_cents: int
    state: AcquisitionState
    contact_verified: bool
    customer_message: str | None = None
    # Shown only until the address is confirmed, so a typo can be recognized.
    contact_email_masked: str | None = None
    # True only when an unused confirmation challenge exists for this address.
    confirm_mail_dispatched: bool = False
    # This browser's own confirmation-email cooldown; never the per-address limit.
    resend_retry_after_seconds: int | None = None
    # An operator closed this unpaid request (rather than its payment window lapsing).
    cancelled: bool = False


class ExpectedVersion(BaseModel):
    """Version of the acquisition row the operator is acting on."""

    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)


class UnpaidClose(BaseModel):
    """Operator cancellation of an unpaid request. The reason is internal only."""

    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def _reason_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("A reason is required.")
        return text


class UploadAttempt(BaseModel):
    """One operator upload attempt, derived from the job's audit events.

    ``live``: the link can still be used. ``expired``: the link lapsed before
    the file was verified. ``abandoned`` / ``rejected`` / ``verified`` end it.
    No replacement attempt starts before ``replace_after``.
    """

    attempt: str | None
    issued_at: datetime
    link_expires_at: datetime
    replace_after: datetime
    suffix: str | None
    content_type: str | None
    size_bytes: int | None
    status: Literal["live", "expired", "abandoned", "rejected", "verified", "legacy"]

    def declared(self) -> dict:
        """The file this attempt was granted for."""
        return {
            "suffix": self.suffix,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
        }


class RecordingAction(BaseModel):
    """Versioned operator action, never an upload or analysis authorization."""

    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "recording_failed",
        "retry_recording",
        "retry_analysis",
        "correct_code",
        "record_refund",
        "close_without_refund",
    ]
    expected_version: int = Field(ge=0)
    note: str = Field(min_length=1, max_length=2000)
    review_code: str | None = Field(default=None, max_length=100)
    customer_message: str | None = Field(default=None, max_length=1000)
    refund_reference: str | None = Field(default=None, max_length=200)


class RecordingJob(BaseModel):
    """Private database boundary; exposed only to authenticated operators."""

    submission_id: str
    review_code: str | None
    state: AcquisitionState
    created_at: datetime
    queued_at: datetime | None
    contact_verified_at: datetime | None
    customer_message: str | None
    version: int
    public_reference: str | None = None
