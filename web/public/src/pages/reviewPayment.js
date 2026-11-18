/**
 * True once the review or its review-code request has expired. An expired
 * review is never payable, whatever its stored payment state. A voucher whose
 * failure reason is `expired` is a different fact and is not read here.
 */
export function reviewExpired(view) {
  return view?.status === "expired" || view?.acquisition?.state === "expired";
}

/** True while the review still needs a card payment before analysis can start. */
export function awaitingPayment(view) {
  if (reviewExpired(view)) {
    return false;
  }
  return view?.payment_state === "unpaid" || view?.payment_state === "checkout_created";
}

/** True while an attached voucher has not yet been used or refused. */
export function voucherPending(view) {
  return awaitingPayment(view) && view?.voucher_state === "pending";
}

/** True when the pay button may show: payment is due and no voucher is still pending. */
export function paymentOffered(view) {
  return awaitingPayment(view) && !voucherPending(view);
}

/**
 * True while checkout has returned and payment is still settling.
 * ``confirming`` is the local flag after Stripe answers pending/paid without a URL.
 */
export function paymentConfirming(view, confirming = false) {
  if (confirming) {
    return true;
  }
  return view?.payment_state === "paid" && view.status === "received";
}

/** True while a review-code request still needs a confirmed contact address. */
export function awaitingContact(view) {
  return Boolean(view?.acquisition) && view.acquisition.contact_verified === false && !reviewExpired(view);
}

/**
 * True when payment is already satisfied (card or not required) but contact is not.
 * Distinct from a pending voucher that has not yet redeemed.
 */
export function paidAwaitingContact(view) {
  return (
    awaitingContact(view)
    && (view.payment_state === "paid" || view.payment_state === "not_required")
    && view.voucher_state !== "pending"
  );
}

/**
 * True when a review-code request still owes card payment and contact is unverified,
 * with no pending voucher. Email confirmation is the next step before Stripe.
 */
export function unpaidAwaitingContact(view) {
  return awaitingContact(view) && awaitingPayment(view) && !voucherPending(view);
}

/** Page heading key. Expiry is checked before payment, so a closed request never asks for payment. */
export function reviewHeadingKey(view) {
  if (!view) {
    return "review.heading.default";
  }
  if (reviewExpired(view)) {
    return "review.heading.expired";
  }
  if (voucherPending(view) && awaitingContact(view)) {
    return "review.heading.email_confirmation";
  }
  if (voucherPending(view)) {
    return "review.heading.default";
  }
  if (paidAwaitingContact(view)) {
    return "review.heading.paid_email_confirmation";
  }
  if (unpaidAwaitingContact(view)) {
    return "review.heading.email_before_payment";
  }
  if (awaitingPayment(view)) {
    return "review.heading.payment";
  }
  if (view.status === "complete") {
    return view.outcome === "no_deaths" ? "review.heading.no_moments" : "review.heading.complete";
  }
  if (view.status === "failed") {
    return "review.heading.failed";
  }
  return "review.heading.processing";
}

/** A verified recording is queued for, or in, analysis but not yet handed off. */
export function analysisInProgress(view) {
  return view?.acquisition?.state === "uploading" && ["queued", "processing"].includes(view.status);
}

/** Analysis progress is shown without a review code, or once the recording is verified. */
export function showsAnalysisProgress(view) {
  return !view?.acquisition || view.acquisition.state === "ready" || analysisInProgress(view);
}

/**
 * Status key for a review-code request, naming only what is still outstanding
 * before recording: the email confirmation, the payment, or both.
 */
export function acquisitionStateKey(view) {
  const acquisition = view?.acquisition;
  if (acquisition?.state === "expired" && acquisition.cancelled) {
    return "review_code.state.cancelled";
  }
  if (analysisInProgress(view)) return "review_code.state.ready";
  if (acquisition?.state !== "pending_payment") {
    return `review_code.state.${acquisition?.state}`;
  }
  if (!acquisition.contact_verified) {
    if (view.payment_state === "paid") {
      return "review_code.state.pending_email_after_payment";
    }
    return paymentOffered(view)
      ? "review_code.state.pending_payment"
      : "review_code.state.pending_voucher";
  }
  return awaitingPayment(view)
    ? "review_code.state.pending_payment_only"
    : "review_code.state.awaiting_recording";
}

/**
 * Message key for a voucher that failed, shown only while payment is still due.
 * The voucher failure itself stays on the submission after payment.
 */
export function voucherFallbackKey(view) {
  if (view?.voucher_state !== "failed" || !awaitingPayment(view)) {
    return null;
  }
  const reason = view.voucher_failure_reason;
  return reason ? `review.voucher.${reason}` : "review.voucher_failed";
}
