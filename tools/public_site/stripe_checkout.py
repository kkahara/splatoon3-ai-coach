"""Stripe Checkout for one analysis. Card payments only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

_PRODUCT = "Splatoon 3 match analysis"


class CheckoutSignatureError(Exception):
    """The webhook signature does not match the configured secret."""


@dataclass(frozen=True)
class CheckoutSession:
    """The Checkout fields this site stores or checks. Nothing else is kept."""

    id: str
    url: str | None
    status: str
    payment_status: str
    amount_total: int | None
    currency: str | None
    payment_intent_id: str | None
    client_reference_id: str | None


@dataclass(frozen=True)
class CheckoutEvent:
    """A verified webhook reduced to the session it carries."""

    type: str
    session: CheckoutSession


class CheckoutGateway(Protocol):
    """Create, reuse, and verify Checkout sessions."""

    def create_session(
        self,
        *,
        submission_id: str,
        price_cents: int,
        currency: str,
        expires_at: datetime,
        success_url: str,
        cancel_url: str,
        idempotency_key: str,
    ) -> CheckoutSession:
        """Open one card Checkout session for the snapshotted price."""

    def retrieve_session(self, session_id: str) -> CheckoutSession:
        """Read one session back, including whether it is still open."""

    def construct_event(self, payload: bytes, signature: str, secret: str) -> CheckoutEvent:
        """Verify a webhook and return its session."""


class StripeCheckout:
    """The real Stripe SDK, imported only when a call is made."""

    def __init__(self, secret_key: str) -> None:
        self.secret_key = secret_key

    def create_session(
        self,
        *,
        submission_id: str,
        price_cents: int,
        currency: str,
        expires_at: datetime,
        success_url: str,
        cancel_url: str,
        idempotency_key: str,
    ) -> CheckoutSession:
        """Open one card session that expires with the site's payment lifetime."""
        stripe = _stripe()
        stripe.api_key = self.secret_key
        created = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": currency,
                        "unit_amount": price_cents,
                        "product_data": {"name": _PRODUCT},
                    },
                }
            ],
            success_url=success_url,
            cancel_url=cancel_url,
            expires_at=int(expires_at.timestamp()),
            client_reference_id=submission_id,
            metadata={"submission_id": submission_id},
            idempotency_key=idempotency_key,
        )
        return _session(created)

    def retrieve_session(self, session_id: str) -> CheckoutSession:
        """Read one existing session."""
        stripe = _stripe()
        stripe.api_key = self.secret_key
        return _session(stripe.checkout.Session.retrieve(session_id))

    def construct_event(self, payload: bytes, signature: str, secret: str) -> CheckoutEvent:
        """Verify the signature, then keep only a completed session's fields."""
        stripe = _stripe()
        try:
            event = stripe.Webhook.construct_event(payload, signature, secret)
        except Exception as exc:
            raise CheckoutSignatureError(str(exc)) from exc
        data = event["data"]["object"]
        return CheckoutEvent(type=str(event["type"]), session=_session(data))


def _stripe():
    import stripe

    return stripe


def _session(data: object) -> CheckoutSession:
    raw = _mapping(data)
    intent = raw.get("payment_intent")
    intent_id = intent if isinstance(intent, str) else None
    amount = raw.get("amount_total")
    return CheckoutSession(
        id=str(raw.get("id") or ""),
        url=_text(raw.get("url")),
        status=str(raw.get("status") or ""),
        payment_status=str(raw.get("payment_status") or ""),
        amount_total=amount if isinstance(amount, int) else None,
        currency=_text(raw.get("currency")),
        payment_intent_id=intent_id,
        client_reference_id=_text(raw.get("client_reference_id")),
    )


def _mapping(data: object) -> dict:
    if isinstance(data, dict):
        return data
    to_dict = getattr(data, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        if isinstance(converted, dict):
            return converted
    return {}


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
