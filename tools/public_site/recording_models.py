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
    target_player_name: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=254)

    @field_validator("review_code")
    @classmethod
    def code_format(cls, value: str) -> str:
        """Apply syntax validation only."""
        return normalize_code(value)

    @field_validator("target_player_name")
    @classmethod
    def player_hint(cls, value: str) -> str:
        """Keep the submitted in-game name without inferring a player slot."""
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("Enter the player's in-game name.")
        return value


class ReviewCodeCreated(BaseModel):
    """One-time access token and payment information; no upload grant."""

    token: str
    submission_id: str
    review_path: str
    payment_required: bool = True
    price_cents: int
    notice_pending: bool = False


class AcquisitionView(BaseModel):
    """Customer-safe acquisition status, independent of analysis status."""

    job_reference: str
    price_cents: int
    state: AcquisitionState
    contact_verified: bool
    target_player_name: str | None
    target_confirmed: bool
    customer_message: str | None = None


class RecordingAction(BaseModel):
    """Versioned operator action, never an upload or analysis authorization."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["confirm_target", "recording_failed", "correct_code", "record_refund"]
    expected_version: int = Field(ge=0)
    note: str = Field(min_length=1, max_length=2000)
    confirmed_player_name: str | None = Field(default=None, max_length=100)
    review_code: str | None = Field(default=None, max_length=100)
    customer_message: str | None = Field(default=None, max_length=1000)
    refund_reference: str | None = Field(default=None, max_length=200)


class RecordingJob(BaseModel):
    """Private database boundary; exposed only to authenticated operators."""

    submission_id: str
    review_code: str | None
    target_player_name: str | None
    state: AcquisitionState
    created_at: datetime
    queued_at: datetime | None
    contact_verified_at: datetime | None
    target_confirmed_at: datetime | None
    confirmed_player_name: str | None
    customer_message: str | None
    version: int
