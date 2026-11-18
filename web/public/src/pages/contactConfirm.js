/**
 * Presentation for guest confirmation mail: only claim "sent" when the server
 * says a challenge was dispatched or the action outcome is `sent`.
 */

/** True when an unused confirmation challenge exists for this review-code request. */
export function confirmMailDispatched(acquisition) {
  return Boolean(acquisition?.confirm_mail_dispatched);
}

/**
 * Status-page lead copy while contact is unverified.
 * Cooldown and "not yet sent" must not reuse the "check your email" wording.
 */
export function contactVerifyLeadKey({ pendingVoucher, dispatched, coolingDown }) {
  if (dispatched) {
    return pendingVoucher ? "review_code.verify_wait_voucher" : "review_code.verify_wait";
  }
  if (coolingDown) {
    return "review_code.verify_cooldown";
  }
  return "review_code.verify_not_sent";
}

/** Message after a resend/change action, from the server outcome. */
export function confirmMailActionMessageKey(outcome, { changed = false } = {}) {
  if (outcome === "sent") {
    return changed ? "review_code.email_changed" : "review_code.email_sent";
  }
  if (outcome === "cooldown") {
    return changed ? "review_code.email_changed_later" : "review_code.verify_cooldown";
  }
  if (outcome === "failed") {
    return "review_code.email_failed";
  }
  return null;
}
