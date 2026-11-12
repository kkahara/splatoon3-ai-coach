"""Customer-facing status for one submission on the coaching history list.

Presentation only: it reads committed payment, voucher, recording and analysis
states and never changes them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from public_site.models import PublicStatus

DisplayStatus = Literal[
    "payment_required",
    "email_confirmation",
    "awaiting_recording",
    "recording",
    "awaiting_analysis",
    "analyzing",
    "complete",
    "needs_attention",
    "failed",
    "expired",
    "cancelled",
]

_PAYMENT_DUE = frozenset({"unpaid", "checkout_created"})
_PAYMENT_CONFIRMED = frozenset({"paid", "not_required"})


def display_status(
    status: PublicStatus,
    payment_state: str | None,
    voucher_state: str | None,
    acquisition_state: str | None,
    contact_verified: bool | None,
    cancelled: bool,
) -> DisplayStatus:
    """One status from the supplied states. The first matching rule wins.

    1. Analysis ``complete`` -> ``complete``.
    2. Acquisition ``closed``, or ``expired`` with ``cancelled`` -> ``cancelled``.
    3. Analysis ``failed`` -> ``failed``.
    4. Analysis or acquisition ``expired`` -> ``expired``.
    5. Payment ``unpaid``/``checkout_created`` with no pending voucher ->
       ``payment_required``. A pending voucher is attached but not yet redeemed;
       checkout is refused until it is redeemed or fails, so nothing is due yet.
    6. Review code with ``contact_verified is False`` -> ``email_confirmation``.
       A pending voucher alone is not this: it waits for the upload check (video)
       or the contact confirmation (review code), never only for the email.
    7. Acquisition ``recording_failed`` -> ``needs_attention``.
    8. Acquisition ``awaiting_recording`` -> ``awaiting_recording``.
    9. Acquisition ``pending_payment`` once payment is confirmed or a voucher
       redemption is pending -> ``awaiting_recording``.
    10. Acquisition ``recording``, or ``uploading`` while analysis is still
        ``received`` -> ``recording``.
    11. Analysis ``validating``/``processing`` -> ``analyzing``.
    12. Everything else (``received``/``queued`` after payment, acquisition
        ``ready``, unknown states) -> ``awaiting_analysis``.
    """
    if status == "complete":
        return "complete"
    if acquisition_state == "closed" or (acquisition_state == "expired" and cancelled):
        return "cancelled"
    if status == "failed":
        return "failed"
    if status == "expired" or acquisition_state == "expired":
        return "expired"
    if payment_state in _PAYMENT_DUE and voucher_state != "pending":
        return "payment_required"
    if acquisition_state is not None and contact_verified is False:
        return "email_confirmation"
    return _recording_or_analysis(status, payment_state, voucher_state, acquisition_state)


def _recording_or_analysis(
    status: PublicStatus,
    payment_state: str | None,
    voucher_state: str | None,
    acquisition_state: str | None,
) -> DisplayStatus:
    if acquisition_state == "recording_failed":
        return "needs_attention"
    if acquisition_state == "awaiting_recording":
        return "awaiting_recording"
    authorized = payment_state in _PAYMENT_CONFIRMED or voucher_state == "pending"
    if acquisition_state == "pending_payment" and authorized:
        return "awaiting_recording"
    if acquisition_state == "recording":
        return "recording"
    if acquisition_state == "uploading" and status == "received":
        return "recording"
    if status in {"validating", "processing"}:
        return "analyzing"
    return "awaiting_analysis"
