import assert from "node:assert/strict";
import { test } from "node:test";
import { canUploadRecording, normalizedReviewCode, recordingQueued } from "./recordingForm.js";

test("code normalization accepts harmless formatting without silently repairing input", () => {
  assert.equal(normalizedReviewCode("abcd 1234 efgh 5678"), "ABCD-1234-EFGH-5678");
  for (const raw of ["ABCD/1234/EFGH/5678", "ABCD1234EFGH5678!", "ＡBCD1234EFGH5678", "ABCD1234EFGH56789", ""]) {
    assert.equal(normalizedReviewCode(raw), null);
  }
});

test("recording confirmation requires both verified contact and paid state", () => {
  const acquisition = { state: "awaiting_recording", contact_verified: true };
  assert.equal(recordingQueued({ payment_state: "paid", acquisition }), true);
  assert.equal(recordingQueued({ payment_state: "unpaid", acquisition }), false);
  assert.equal(recordingQueued({ payment_state: "paid", acquisition: { ...acquisition, contact_verified: false } }), false);
  assert.equal(recordingQueued({ payment_state: "paid", acquisition: { ...acquisition, state: "closed" } }), false);
});

test("operator upload needs a confirmed target and an unverified acquisition", () => {
  const confirmed = "2026-11-10T00:00:00Z";
  assert.equal(canUploadRecording({ state: "awaiting_recording", target_confirmed_at: confirmed }), true);
  assert.equal(canUploadRecording({ state: "recording", target_confirmed_at: confirmed }), true);
  assert.equal(canUploadRecording({ state: "awaiting_recording", target_confirmed_at: null }), false);
  for (const state of ["uploading", "ready", "recording_failed", "closed", "expired"]) {
    assert.equal(canUploadRecording({ state, target_confirmed_at: confirmed }), false);
  }
});
