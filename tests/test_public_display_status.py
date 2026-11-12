"""Customer-facing history status: one label per submission, first rule wins."""

from __future__ import annotations

import pytest
from public_site.display_status import DisplayStatus, display_status


def _status(
    status: str = "received",
    payment: str | None = "paid",
    voucher: str | None = None,
    acquisition: str | None = None,
    verified: bool | None = None,
    cancelled: bool = False,
) -> DisplayStatus:
    return display_status(status, payment, voucher, acquisition, verified, cancelled)


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        # one case per label
        ({"payment": "unpaid"}, "payment_required"),
        ({"payment": "checkout_created"}, "payment_required"),
        ({"acquisition": "pending_payment", "verified": False}, "email_confirmation"),
        ({"acquisition": "awaiting_recording", "verified": True}, "awaiting_recording"),
        ({"acquisition": "recording", "verified": True}, "recording"),
        ({"status": "queued"}, "awaiting_analysis"),
        ({"status": "processing"}, "analyzing"),
        ({"status": "validating"}, "analyzing"),
        ({"status": "complete"}, "complete"),
        ({"acquisition": "recording_failed", "verified": True}, "needs_attention"),
        ({"status": "failed"}, "failed"),
        ({"status": "expired", "payment": "unpaid"}, "expired"),
        ({"acquisition": "closed", "verified": True}, "cancelled"),
    ],
)
def test_each_display_status(facts: dict, expected: str) -> None:
    assert _status(**facts) == expected


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        # complete beats stale acquisition states
        ({"status": "complete", "acquisition": "uploading", "verified": True},
         "complete"),
        ({"status": "complete", "acquisition": "closed", "verified": True},
         "complete"),
        # operator cancellation is never an analysis failure or a plain expiry
        ({"acquisition": "expired", "cancelled": True, "payment": "unpaid"}, "cancelled"),
        ({"status": "expired", "acquisition": "expired", "cancelled": True}, "cancelled"),
        ({"status": "failed", "acquisition": "closed", "verified": True}, "cancelled"),
        ({"acquisition": "expired", "payment": "unpaid"}, "expired"),
        # payment and email gates are not hidden by stale analysis progress
        ({"status": "processing", "payment": "unpaid"}, "payment_required"),
        (
            {"status": "processing", "acquisition": "pending_payment", "verified": False},
            "email_confirmation",
        ),
        (
            {"payment": "unpaid", "acquisition": "pending_payment", "verified": False},
            "payment_required",
        ),
        (
            {"payment": "paid", "acquisition": "pending_payment", "verified": False},
            "email_confirmation",
        ),
        # recording_failed is not hidden by analysis progress
        ({"acquisition": "recording_failed", "verified": True}, "needs_attention"),
        (
            {"status": "processing", "acquisition": "recording_failed", "verified": True},
            "needs_attention",
        ),
        # pending_payment becomes awaiting_recording only once authorized
        (
            {"payment": "paid", "acquisition": "pending_payment", "verified": True},
            "awaiting_recording",
        ),
        (
            {"payment": "not_required", "acquisition": "pending_payment",
             "verified": True},
            "awaiting_recording",
        ),
        (
            {"payment": "unpaid", "acquisition": "pending_payment", "verified": True},
            "payment_required",
        ),
        # uploading is recording until the analysis queue accepts the job
        ({"acquisition": "uploading", "verified": True}, "recording"),
        ({"status": "queued", "acquisition": "uploading", "verified": True},
         "awaiting_analysis"),
        (
            {"status": "processing", "acquisition": "uploading", "verified": True},
            "analyzing",
        ),
        ({"acquisition": "ready", "verified": True}, "awaiting_analysis"),
        # a paid upload whose analysis is queued
        ({"status": "queued", "payment": "paid"}, "awaiting_analysis"),
        ({"status": "queued", "payment": "not_required", "voucher": "redeemed"},
         "awaiting_analysis"),
    ],
)
def test_overlapping_states_follow_the_precedence(facts: dict, expected: str) -> None:
    assert _status(**facts) == expected


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        # A pending voucher is attached but not redeemed: no payment is due yet,
        # and it does not mean the email is unconfirmed.
        ({"payment": "unpaid", "voucher": "pending"}, "awaiting_analysis"),
        (
            {"payment": "unpaid", "voucher": "pending", "acquisition": "pending_payment",
             "verified": True},
            "awaiting_recording",
        ),
        (
            {"payment": "unpaid", "voucher": "pending", "acquisition": "pending_payment",
             "verified": False},
            "email_confirmation",
        ),
        # A failed voucher leaves the card payment due.
        ({"payment": "unpaid", "voucher": "failed"}, "payment_required"),
    ],
)
def test_voucher_states(facts: dict, expected: str) -> None:
    assert _status(**facts) == expected


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        ({"payment": None}, "awaiting_analysis"),
        ({"payment": None, "status": "processing"}, "analyzing"),
        ({"payment": None, "status": "complete"}, "complete"),
        ({"acquisition": "new_state_2027", "verified": True}, "awaiting_analysis"),
        (
            {"acquisition": "new_state_2027", "verified": True, "status": "processing"},
            "analyzing",
        ),
        ({"acquisition": "pending_payment", "verified": True, "payment": None},
         "awaiting_analysis"),
    ],
)
def test_missing_or_unknown_facts_fall_back_to_the_analysis_status(
    facts: dict, expected: str
) -> None:
    assert _status(**facts) == expected
