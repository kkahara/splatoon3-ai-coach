import assert from "node:assert/strict";
import test from "node:test";
import {
  confirmMailActionMessageKey,
  confirmMailDispatched,
  contactVerifyLeadKey,
} from "./contactConfirm.js";

test("dispatched challenges keep the check-your-email lead", () => {
  assert.equal(
    contactVerifyLeadKey({ pendingVoucher: false, dispatched: true, coolingDown: false }),
    "review_code.verify_wait",
  );
  assert.equal(
    contactVerifyLeadKey({ pendingVoucher: true, dispatched: true, coolingDown: true }),
    "review_code.verify_wait_voucher",
  );
});

test("cooldown without a dispatched mail does not claim an email was sent", () => {
  assert.equal(
    contactVerifyLeadKey({ pendingVoucher: false, dispatched: false, coolingDown: true }),
    "review_code.verify_cooldown",
  );
  assert.equal(
    contactVerifyLeadKey({ pendingVoucher: true, dispatched: false, coolingDown: false }),
    "review_code.verify_not_sent",
  );
});

test("action messages distinguish sent, cooldown, and failure", () => {
  assert.equal(confirmMailActionMessageKey("sent"), "review_code.email_sent");
  assert.equal(confirmMailActionMessageKey("sent", { changed: true }), "review_code.email_changed");
  assert.equal(confirmMailActionMessageKey("cooldown"), "review_code.verify_cooldown");
  assert.equal(
    confirmMailActionMessageKey("cooldown", { changed: true }),
    "review_code.email_changed_later",
  );
  assert.equal(confirmMailActionMessageKey("failed"), "review_code.email_failed");
  assert.equal(confirmMailActionMessageKey("not_required"), null);
});

test("confirm_mail_dispatched is read from the acquisition", () => {
  assert.equal(confirmMailDispatched({ confirm_mail_dispatched: true }), true);
  assert.equal(confirmMailDispatched({ confirm_mail_dispatched: false }), false);
  assert.equal(confirmMailDispatched(null), false);
});
