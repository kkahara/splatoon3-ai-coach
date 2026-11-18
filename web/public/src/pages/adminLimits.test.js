import assert from "node:assert/strict";
import test from "node:test";
import { limitChanges, limitInputs, limitSourceLabel, mergeLimits } from "./adminLimits.js";

const payload = {
  price_cents: 500,
  account_uploads_per_hour: 5,
  account_uploads_per_hour_source: "saved",
  guest_uploads_per_hour: 2,
  guest_uploads_per_hour_source: "environment",
  network_uploads_per_hour: 6,
  network_uploads_per_hour_source: "environment",
};

test("only edited limits are sent", () => {
  const inputs = { ...limitInputs(payload), guest_uploads_per_hour: "10" };
  assert.deepEqual(limitChanges(payload, inputs), { changes: { guest_uploads_per_hour: 10 } });
});

test("an unchanged form sends nothing", () => {
  assert.deepEqual(limitChanges(payload, limitInputs(payload)), { error: "Nothing changed." });
});

test("out-of-range, decimal and blank values are refused", () => {
  for (const text of ["0", "101", "2.5", "", "abc", "-1"]) {
    const inputs = { ...limitInputs(payload), network_uploads_per_hour: text };
    assert.ok(limitChanges(payload, inputs).error, text);
  }
});

test("source labels distinguish saved settings from environment defaults", () => {
  assert.equal(limitSourceLabel(payload, "account_uploads_per_hour"), "saved setting");
  assert.equal(limitSourceLabel(payload, "guest_uploads_per_hour"), "environment default");
});

test("a limit response replaces limits and keeps the price", () => {
  const next = {
    ...payload,
    guest_uploads_per_hour: 10,
    guest_uploads_per_hour_source: "saved",
  };
  delete next.price_cents;
  const merged = mergeLimits(payload, next);
  assert.equal(merged.price_cents, 500);
  assert.equal(merged.guest_uploads_per_hour, 10);
  assert.equal(merged.guest_uploads_per_hour_source, "saved");
});
