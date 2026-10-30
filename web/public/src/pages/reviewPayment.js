/** True while the review still needs a card payment before analysis can start. */
export function awaitingPayment(view) {
  return view?.payment_state === "unpaid" || view?.payment_state === "checkout_created";
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
