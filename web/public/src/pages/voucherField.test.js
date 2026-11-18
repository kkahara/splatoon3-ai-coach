import assert from "node:assert/strict";
import test from "node:test";
import { onVoucherEnter } from "./voucherField.js";

function keyEvent(overrides = {}) {
  return {
    key: "Enter",
    keyCode: 13,
    nativeEvent: { isComposing: false },
    preventDefault() {
      this.prevented = true;
    },
    stopPropagation() {
      this.stopped = true;
    },
    prevented: false,
    stopped: false,
    ...overrides,
  };
}

test("Enter applies the voucher and never submits the parent form", () => {
  const calls = [];
  const event = keyEvent();
  onVoucherEnter(event, {
    accepted: false,
    checking: false,
    apply: () => calls.push("apply"),
  });
  assert.equal(event.prevented, true);
  assert.equal(event.stopped, true);
  assert.deepEqual(calls, ["apply"]);
});

test("Enter during IME composition does not apply", () => {
  const calls = [];
  const event = keyEvent({ nativeEvent: { isComposing: true }, keyCode: 229 });
  onVoucherEnter(event, {
    accepted: false,
    checking: false,
    apply: () => calls.push("apply"),
  });
  assert.equal(event.prevented, true);
  assert.deepEqual(calls, []);
});

test("Enter while checking or already accepted does not apply again", () => {
  for (const voucher of [
    { accepted: false, checking: true, apply: () => assert.fail("apply") },
    { accepted: true, checking: false, apply: () => assert.fail("apply") },
  ]) {
    const event = keyEvent();
    onVoucherEnter(event, voucher);
    assert.equal(event.prevented, true);
  }
});

test("non-Enter keys are ignored", () => {
  const event = keyEvent({ key: "a" });
  onVoucherEnter(event, {
    accepted: false,
    checking: false,
    apply: () => assert.fail("apply"),
  });
  assert.equal(event.prevented, false);
});
