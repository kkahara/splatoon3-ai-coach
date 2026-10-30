import assert from "node:assert/strict";
import test from "node:test";
import { awaitingPayment, voucherFallbackKey } from "./reviewPayment.js";

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

test("pending and redeemed vouchers never show the fallback", () => {
  for (const voucherState of ["pending", "redeemed"]) {
    const view = { voucher_state: voucherState, payment_state: "unpaid" };
    assert.equal(voucherFallbackKey(view), null);
  }
});
