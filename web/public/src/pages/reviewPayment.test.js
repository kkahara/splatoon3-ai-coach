import assert from "node:assert/strict";
import test from "node:test";
import {
  acquisitionStateKey,
  awaitingPayment,
  paidAwaitingContact,
  paymentConfirming,
  paymentOffered,
  reviewExpired,
  reviewHeadingKey,
  showsAnalysisProgress,
  unpaidAwaitingContact,
  voucherFallbackKey,
  voucherPending,
} from "./reviewPayment.js";

const expired = { voucher_state: "failed", voucher_failure_reason: "expired" };

test("an unpaid submission with an expired voucher keeps the payment fallback", () => {
  for (const paymentState of ["unpaid", "checkout_created"]) {
    const view = { ...expired, status: "received", payment_state: paymentState };
    assert.equal(voucherFallbackKey(view), "review.voucher.expired");
    assert.equal(awaitingPayment(view), true);
  }
});

test("a failed voucher without a reason uses the generic fallback", () => {
  const view = { voucher_state: "failed", payment_state: "unpaid" };
  assert.equal(voucherFallbackKey(view), "review.voucher_failed");
});

test("after payment the voucher fallback is gone and the voucher failure is kept", () => {
  for (const status of ["received", "queued", "complete"]) {
    const view = { ...expired, status, payment_state: "paid" };
    assert.equal(voucherFallbackKey(view), null);
    assert.equal(awaitingPayment(view), false);
    assert.equal(view.voucher_state, "failed");
    assert.equal(view.voucher_failure_reason, "expired");
  }
});

test("the no-voucher payment path is unchanged", () => {
  const expected = { unpaid: true, checkout_created: true, paid: false, not_required: false };
  for (const [paymentState, awaiting] of Object.entries(expected)) {
    const view = { voucher_state: null, payment_state: paymentState };
    assert.equal(awaitingPayment(view), awaiting);
    assert.equal(voucherFallbackKey(view), null);
  }
  assert.equal(awaitingPayment(null), false);
  assert.equal(voucherFallbackKey(null), null);
});

test("the pay button stays hidden while a voucher is pending", () => {
  assert.equal(paymentOffered({ voucher_state: "pending", payment_state: "unpaid" }), false);
  assert.equal(paymentOffered({ voucher_state: "failed", payment_state: "unpaid" }), true);
  assert.equal(paymentOffered({ voucher_state: null, payment_state: "checkout_created" }), true);
  assert.equal(paymentOffered({ voucher_state: "redeemed", payment_state: "not_required" }), false);
  assert.equal(paymentOffered(null), false);
  assert.equal(voucherPending({ voucher_state: "pending", payment_state: "unpaid" }), true);
  assert.equal(voucherPending({ voucher_state: "redeemed", payment_state: "not_required" }), false);
  assert.equal(voucherPending({ voucher_state: "failed", payment_state: "unpaid" }), false);
});

test("payment confirming disables a second checkout while Stripe is settling", () => {
  assert.equal(paymentConfirming({ payment_state: "unpaid", status: "received" }, false), false);
  assert.equal(paymentConfirming({ payment_state: "checkout_created", status: "received" }, true), true);
  assert.equal(paymentConfirming({ payment_state: "paid", status: "received" }, false), true);
  assert.equal(paymentConfirming({ payment_state: "paid", status: "queued" }, false), false);
  assert.equal(paymentConfirming(null, true), true);
});

function reviewCode(contactVerified, paymentState, voucherState = null, state = "pending_payment") {
  return {
    payment_state: paymentState,
    voucher_state: voucherState,
    acquisition: { state, contact_verified: contactVerified },
  };
}

test("a verified address that only owes payment says payment only", () => {
  for (const paymentState of ["unpaid", "checkout_created"]) {
    assert.equal(
      acquisitionStateKey(reviewCode(true, paymentState)),
      "review_code.state.pending_payment_only",
    );
    assert.equal(
      acquisitionStateKey(reviewCode(true, paymentState, "failed")),
      "review_code.state.pending_payment_only",
    );
  }
});

test("an unverified address names the outstanding confirmation", () => {
  assert.equal(acquisitionStateKey(reviewCode(false, "unpaid")), "review_code.state.pending_payment");
  assert.equal(
    acquisitionStateKey(reviewCode(false, "unpaid", "pending")),
    "review_code.state.pending_voucher",
  );
  assert.equal(
    acquisitionStateKey(reviewCode(false, "not_required")),
    "review_code.state.pending_voucher",
  );
});

test("later acquisition states keep their own keys", () => {
  for (const state of ["awaiting_recording", "recording", "ready", "expired", "closed"]) {
    assert.equal(acquisitionStateKey(reviewCode(true, "paid", null, state)), `review_code.state.${state}`);
  }
  assert.equal(
    acquisitionStateKey(reviewCode(true, "paid")),
    "review_code.state.awaiting_recording",
  );
});

test("an expired review is never payable, whatever its payment state", () => {
  for (const paymentState of ["unpaid", "checkout_created"]) {
    const closed = { status: "expired", payment_state: paymentState, acquisition: { state: "expired" } };
    assert.equal(reviewExpired(closed), true);
    assert.equal(awaitingPayment(closed), false);
    assert.equal(paymentOffered(closed), false);
    assert.equal(voucherFallbackKey({ ...closed, voucher_state: "failed" }), null);
    assert.equal(paymentOffered({ status: "expired", payment_state: paymentState }), false);
  }
});

test("an expired acquisition is not payable before the top-level status catches up", () => {
  const view = { status: "received", payment_state: "unpaid", acquisition: { state: "expired" } };
  assert.equal(paymentOffered(view), false);
  assert.equal(acquisitionStateKey(view), "review_code.state.expired");
});

test("an operator-cancelled request says it was cancelled, not merely expired", () => {
  const view = { status: "expired", payment_state: "checkout_created", acquisition: { state: "expired", cancelled: true } };
  assert.equal(acquisitionStateKey(view), "review_code.state.cancelled");
  assert.equal(paymentOffered(view), false);
  assert.equal(reviewHeadingKey(view), "review.heading.expired");
});

test("an open unpaid or checkout-created review still offers payment", () => {
  for (const paymentState of ["unpaid", "checkout_created"]) {
    const view = {
      status: "received",
      payment_state: paymentState,
      acquisition: { state: "pending_payment", contact_verified: true },
    };
    assert.equal(reviewExpired(view), false);
    assert.equal(paymentOffered(view), true);
    assert.equal(reviewHeadingKey(view), "review.heading.payment");
  }
});

test("expiry takes precedence over the payment heading", () => {
  for (const view of [
    { status: "expired", payment_state: "unpaid" },
    { status: "expired", payment_state: "checkout_created", acquisition: { state: "expired" } },
    { status: "received", payment_state: "unpaid", acquisition: { state: "expired" } },
  ]) {
    assert.equal(reviewHeadingKey(view), "review.heading.expired");
  }
});

test("a voucher that failed as expired does not close an open request", () => {
  const view = { ...expired, status: "received", payment_state: "unpaid" };
  assert.equal(reviewExpired(view), false);
  assert.equal(paymentOffered(view), true);
  assert.equal(reviewHeadingKey(view), "review.heading.payment");
});

test("other headings are unchanged", () => {
  assert.equal(reviewHeadingKey(null), "review.heading.default");
  assert.equal(reviewHeadingKey({ payment_state: "unpaid", voucher_state: "pending" }), "review.heading.default");
  assert.equal(reviewHeadingKey({ status: "complete", payment_state: "paid" }), "review.heading.complete");
  assert.equal(
    reviewHeadingKey({ status: "complete", payment_state: "paid", outcome: "no_deaths" }),
    "review.heading.no_moments",
  );
  assert.equal(reviewHeadingKey({ status: "failed", payment_state: "paid" }), "review.heading.failed");
  assert.equal(reviewHeadingKey({ status: "queued", payment_state: "paid" }), "review.heading.processing");
});

test("a pending voucher with unverified contact asks for email confirmation", () => {
  const view = reviewCode(false, "unpaid", "pending");
  assert.equal(reviewHeadingKey(view), "review.heading.email_confirmation");
  assert.equal(paymentOffered(view), false);
  assert.equal(acquisitionStateKey(view), "review_code.state.pending_voucher");
});

test("a paid review-code request with unverified contact does not offer payment", () => {
  const view = reviewCode(false, "paid");
  assert.equal(paidAwaitingContact(view), true);
  assert.equal(reviewHeadingKey(view), "review.heading.paid_email_confirmation");
  assert.equal(paymentOffered(view), false);
  assert.equal(acquisitionStateKey(view), "review_code.state.pending_email_after_payment");
});

test("unpaid review-code without a voucher asks for email before payment", () => {
  for (const paymentState of ["unpaid", "checkout_created"]) {
    const view = reviewCode(false, paymentState);
    assert.equal(unpaidAwaitingContact(view), true);
    assert.equal(reviewHeadingKey(view), "review.heading.email_before_payment");
    assert.equal(paymentOffered(view), true);
    assert.equal(acquisitionStateKey(view), "review_code.state.pending_payment");
  }
  const verified = reviewCode(true, "unpaid");
  assert.equal(unpaidAwaitingContact(verified), false);
  assert.equal(reviewHeadingKey(verified), "review.heading.payment");
  assert.equal(paymentOffered(verified), true);
  assert.equal(acquisitionStateKey(verified), "review_code.state.pending_payment_only");
});

test("pending and redeemed vouchers never show the fallback", () => {
  for (const voucherState of ["pending", "redeemed"]) {
    const view = { voucher_state: voucherState, payment_state: "unpaid" };
    assert.equal(voucherFallbackKey(view), null);
  }
});

test("a verified recording in analysis says it is verified and shows progress", () => {
  for (const status of ["queued", "processing"]) {
    const view = { ...reviewCode(true, "paid", null, "uploading"), status };
    assert.equal(acquisitionStateKey(view), "review_code.state.ready");
    assert.equal(showsAnalysisProgress(view), true);
  }
});

test("a verified recording outside analysis keeps its own status and no progress", () => {
  for (const status of ["received", "failed"]) {
    const view = { ...reviewCode(true, "paid", null, "uploading"), status };
    assert.equal(acquisitionStateKey(view), "review_code.state.uploading");
    assert.equal(showsAnalysisProgress(view), false);
  }
  for (const state of ["awaiting_recording", "recording", "recording_failed"]) {
    const view = { ...reviewCode(true, "paid", null, state), status: "queued" };
    assert.equal(acquisitionStateKey(view), `review_code.state.${state}`);
    assert.equal(showsAnalysisProgress(view), false);
  }
});

test("progress shows for uploads and handed-off recordings as before", () => {
  assert.equal(showsAnalysisProgress({ status: "processing" }), true);
  assert.equal(showsAnalysisProgress(reviewCode(true, "paid", null, "ready")), true);
});
