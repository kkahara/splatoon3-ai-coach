import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ACTIVE_POLL_MS,
  IDLE_POLL_MS,
  RECORDING_ACCEPT,
  canCancelUpload,
  capturingJob,
  hydrationSelection,
  stationPoll,
  canCheckUpload,
  canRetryAnalysis,
  canUploadRecording,
  fileMatchesAttempt,
  formatReviewCode,
  historyLine,
  maskReviewCodeEdit,
  nextUploadDeadline,
  normalizedReviewCode,
  recordingAuthorized,
  recordingContentType,
  recordingQueued,
  stationHoldsRecording,
  stripeReference,
  uploadPhase,
} from "./recordingForm.js";

const NOW = new Date("2026-11-11T05:00:00Z");

function uploadDetail(state, status, { replaceAfter = "2026-11-11T05:20:00Z", events = [] } = {}) {
  const attempt = status ? {
    attempt: "a".repeat(32), status, suffix: ".mov", size_bytes: 10,
    link_expires_at: "2026-11-11T05:05:00Z", replace_after: replaceAfter,
  } : null;
  return { job: { state }, upload_attempt: attempt, events };
}

test("recording files are MP4 or MOV, typed from the extension when the browser gives none", () => {
  assert.equal(RECORDING_ACCEPT, "video/mp4,video/quicktime,.mp4,.mov");
  assert.equal(recordingContentType({ name: "a.mov", type: "" }), "video/quicktime");
  assert.equal(recordingContentType({ name: "a.MP4", type: "" }), "video/mp4");
  assert.equal(recordingContentType({ name: "a.mov", type: "video/quicktime" }), "video/quicktime");
  assert.equal(recordingContentType({ name: "a.mkv", type: "" }), "");
});

test("the upload phase follows the job, its attempt and this tab's transfer", () => {
  assert.equal(uploadPhase(uploadDetail("awaiting_recording", null), false, NOW), "none");
  assert.equal(uploadPhase(uploadDetail("recording", "live"), false, NOW), "permitted");
  assert.equal(uploadPhase(uploadDetail("recording", "live"), true, NOW), "transferring");
  assert.equal(uploadPhase(uploadDetail("recording", "expired"), false, NOW), "link_expired");
  assert.equal(uploadPhase(uploadDetail("recording", "rejected"), false, NOW), "rejected");
  assert.equal(uploadPhase(uploadDetail("awaiting_recording", "abandoned"), false, NOW), "waiting");
  assert.equal(uploadPhase(uploadDetail("uploading", "verified"), false, NOW), "verified");
  const queued = uploadDetail("uploading", "verified", { events: [{ kind: "recording_analysis_queued" }] });
  assert.equal(uploadPhase(queued, false, NOW), "queued");
});

test("an ended attempt stops blocking once every earlier link has expired", () => {
  const later = { replaceAfter: "2026-11-11T04:59:00Z" };
  assert.equal(uploadPhase(uploadDetail("awaiting_recording", "abandoned", later), false, NOW), "none");
  assert.equal(uploadPhase(uploadDetail("recording", "rejected", later), false, NOW), "none");
});

test("a live attempt whose link expired while the page was open is treated as expired", () => {
  const afterLink = new Date("2026-11-11T05:06:00Z");
  assert.equal(uploadPhase(uploadDetail("recording", "live"), false, afterLink), "link_expired");
  const afterReplace = new Date("2026-11-11T05:21:00Z");
  assert.equal(uploadPhase(uploadDetail("recording", "live"), false, afterReplace), "none");
  assert.equal(uploadPhase(uploadDetail("recording", "live"), true, afterLink), "transferring");
});

test("a cancelled attempt waits until its replacement time, then a new upload may start", () => {
  const cancelled = uploadDetail("awaiting_recording", "abandoned");
  assert.equal(uploadPhase(cancelled, false, NOW), "waiting");
  assert.equal(uploadPhase(cancelled, false, new Date("2026-11-11T05:06:00Z")), "waiting");
  assert.equal(uploadPhase(cancelled, false, new Date("2026-11-11T05:20:00Z")), "none");
});

test("a retry needs a file with the attempt's declared type and size", () => {
  const attempt = uploadDetail("recording", "live").upload_attempt;
  assert.equal(fileMatchesAttempt({ name: "x.mp4", size: 3 }, null), true);
  assert.equal(fileMatchesAttempt({ name: "take.mov", size: 10 }, attempt), true);
  assert.equal(fileMatchesAttempt({ name: "TAKE.MOV", size: 10 }, attempt), true);
  assert.equal(fileMatchesAttempt({ name: "other-take.mov", size: 10 }, attempt), true);
  assert.equal(fileMatchesAttempt({ name: "take.mp4", size: 10 }, attempt), false);
  assert.equal(fileMatchesAttempt({ name: "take.mov", size: 11 }, attempt), false);
  assert.equal(fileMatchesAttempt({ name: "take", size: 10 }, attempt), false);
});

test("the next upload deadline is the link expiry, then the replacement time, then none", () => {
  const detail = uploadDetail("recording", "live");
  assert.equal(nextUploadDeadline(detail, NOW), Date.parse("2026-11-11T05:05:00Z"));
  assert.equal(nextUploadDeadline(detail, new Date("2026-11-11T05:05:00Z")), Date.parse("2026-11-11T05:20:00Z"));
  assert.equal(nextUploadDeadline(detail, new Date("2026-11-11T05:20:00Z")), null);
  assert.equal(nextUploadDeadline(uploadDetail("awaiting_recording", null), NOW), null);
  const replaced = uploadDetail("recording", "live", { replaceAfter: "2026-11-11T05:40:00Z" });
  replaced.upload_attempt.link_expires_at = "2026-11-11T05:25:00Z";
  assert.equal(nextUploadDeadline(replaced, new Date("2026-11-11T05:21:00Z")), Date.parse("2026-11-11T05:25:00Z"));
});

test("checking needs an open attempt, and cancelling closes live, expired or rejected ones", () => {
  assert.equal(canCheckUpload(uploadDetail("recording", "live")), true);
  assert.equal(canCheckUpload(uploadDetail("recording", "expired")), true);
  assert.equal(canCheckUpload(uploadDetail("recording", "rejected")), false);
  assert.equal(canCheckUpload(uploadDetail("awaiting_recording", "abandoned")), false);
  assert.equal(canCheckUpload(uploadDetail("uploading", "verified")), true);
  assert.equal(
    canCheckUpload(uploadDetail("uploading", "verified", { events: [{ kind: "recording_analysis_queued" }] })),
    false,
  );
  assert.equal(canCancelUpload(uploadDetail("recording", "rejected")), true);
  assert.equal(canCancelUpload(uploadDetail("recording", "legacy")), false);
  assert.equal(canCancelUpload(uploadDetail("awaiting_recording", "abandoned")), false);
});

function typed(previous, raw, caret = raw.length, inputType = "insertText") {
  return maskReviewCodeEdit({ previous, raw, caret, inputType });
}

test("the review-code mask groups typed characters and inserts hyphens", () => {
  assert.deepEqual(typed("", "a"), { value: "A", caret: 1 });
  assert.deepEqual(typed("ABC", "ABCD"), { value: "ABCD", caret: 4 });
  assert.deepEqual(typed("ABCD", "ABCDE"), { value: "ABCD-E", caret: 6 });
  assert.deepEqual(typed("ABCD-1234-EFGH-567", "ABCD-1234-EFGH-5678"), {
    value: "ABCD-1234-EFGH-5678",
    caret: 19,
  });
  assert.deepEqual(typed("ABCD-1234-EFGH-5678", "ABCD-1234-EFGH-5678X"), {
    value: "ABCD-1234-EFGH-5678",
    caret: 19,
  });
});

test("typed hyphens and spaces are ignored", () => {
  assert.deepEqual(typed("ABCD", "ABCD-"), { value: "ABCD", caret: 4 });
  assert.deepEqual(typed("ABCD", "ABCD "), { value: "ABCD", caret: 4 });
  assert.deepEqual(typed("AB", "AB-", 3), { value: "AB", caret: 2 });
});

test("a pasted code is formatted whatever its separators", () => {
  for (const pasted of ["abcd1234efgh5678", "ABCD 1234 EFGH 5678", "abcd-1234-efgh-5678", " ABCD_1234/EFGH 5678 "]) {
    assert.equal(typed("", pasted, pasted.length, "insertFromPaste").value, "ABCD-1234-EFGH-5678");
  }
  assert.equal(formatReviewCode("abcd1234efgh5678extra"), "ABCD-1234-EFGH-5678");
});

test("deleting across an inserted hyphen removes the neighbouring character", () => {
  assert.deepEqual(typed("ABCD-E", "ABCDE", 4, "deleteContentBackward"), { value: "ABCE", caret: 3 });
  assert.deepEqual(typed("ABCD-EF", "ABCDEF", 4, "deleteContentForward"), { value: "ABCD-F", caret: 4 });
  assert.deepEqual(typed("ABCD-E", "ABCD-", 5, "deleteContentBackward"), { value: "ABCD", caret: 4 });
});

test("editing in the middle keeps the caret after the same character", () => {
  assert.deepEqual(typed("ABCD-EFGH", "ABXCD-EFGH", 3), { value: "ABXC-DEFG-H", caret: 3 });
  assert.deepEqual(typed("ABCD-EFGH", "ABCD-XEFGH", 6), { value: "ABCD-XEFG-H", caret: 6 });
});

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

test("a voucher-authorized review code is queued like a paid one", () => {
  const acquisition = { state: "awaiting_recording", contact_verified: true };
  assert.equal(recordingAuthorized({ payment_state: "not_required" }), true);
  assert.equal(recordingAuthorized({ payment_state: "checkout_created" }), false);
  assert.equal(recordingQueued({ payment_state: "not_required", acquisition }), true);
  assert.equal(
    recordingQueued({ payment_state: "not_required", acquisition: { ...acquisition, contact_verified: false } }),
    false,
  );
});

test("operator upload is available before verification", () => {
  assert.equal(canUploadRecording({ state: "awaiting_recording" }), true);
  assert.equal(canUploadRecording({ state: "recording" }), true);
  for (const state of ["uploading", "ready", "recording_failed", "closed", "expired"]) {
    assert.equal(canUploadRecording({ state }), false);
  }
});

test("a station take blocks a file upload until it is gone", () => {
  for (const phase of ["capturing", "interrupted", "finalized", "upload_uncertain"]) {
    assert.equal(stationHoldsRecording(phase), true);
  }
  assert.equal(stationHoldsRecording("absent"), false);
  assert.equal(stationHoldsRecording("verified"), false);
});

const CAPTURING = { phase: "capturing", elapsed_seconds: 12, capture_mode: "1920x1080 at 60 fps", size_warning: false };

test("a running capture is followed every second whichever job is selected", () => {
  const takes = { other: CAPTURING, mine: { ...CAPTURING, phase: "finalized" } };
  assert.equal(capturingJob(takes), "other");
  for (const selected of [null, "mine", "other", "unrelated"]) {
    assert.deepEqual(stationPoll(takes, selected, "absent"), { kind: "station", id: "other", ms: ACTIVE_POLL_MS });
  }
  assert.equal(ACTIVE_POLL_MS, 1000);
});

test("with no capture running the queue is reloaded within five seconds", () => {
  for (const takes of [{}, undefined, { a: { ...CAPTURING, phase: "interrupted" } }]) {
    assert.deepEqual(stationPoll(takes, "a", "interrupted"), { kind: "queue", id: null, ms: IDLE_POLL_MS });
  }
  assert.ok(IDLE_POLL_MS <= 5000);
  assert.equal(capturingJob({}), null);
});

test("an uncertain station upload on the selected job is still followed every second", () => {
  assert.deepEqual(stationPoll({}, "mine", "upload_uncertain"), { kind: "station", id: "mine", ms: ACTIVE_POLL_MS });
  assert.equal(stationPoll({}, null, "upload_uncertain").kind, "queue");
});

test("only the first load opens a running capture, and never over a selection", () => {
  const takes = { running: CAPTURING };
  assert.equal(hydrationSelection(takes, null, true), "running");
  assert.equal(hydrationSelection(takes, "chosen", true), null);
  assert.equal(hydrationSelection(takes, null, false), null);
  assert.equal(hydrationSelection(takes, "chosen", false), null);
  assert.equal(hydrationSelection({ done: { ...CAPTURING, phase: "finalized" } }, null, true), null);
});

test("a voucher job explains the empty Stripe reference", () => {
  assert.equal(
    stripeReference({ authorization: "voucher", payment_reference: "" }),
    "No Stripe payment (paid by voucher).",
  );
  assert.equal(stripeReference({ authorization: "payment", payment_reference: "pi_1" }), "pi_1");
  assert.equal(stripeReference({ authorization: "payment", payment_reference: "" }), "No Stripe payment yet.");
});

function analysisDetail(state, status, inputType = "review_code") {
  return {
    job: { state },
    analysis: { status, step: "coaching", error: "Analysis failed.", input_type: inputType },
    events: [{ kind: "recording_analysis_queued", details: {} }],
  };
}

test("analysis retry is offered only for a failed review-code run of a verified recording", () => {
  assert.equal(canRetryAnalysis(analysisDetail("uploading", "failed")), true);
  for (const status of ["queued", "processing", "complete", "expired"]) {
    assert.equal(canRetryAnalysis(analysisDetail("uploading", status)), false);
  }
  for (const state of ["awaiting_recording", "recording", "recording_failed", "ready", "closed"]) {
    assert.equal(canRetryAnalysis(analysisDetail(state, "failed")), false);
  }
  assert.equal(canRetryAnalysis(analysisDetail("uploading", "failed", "video")), false);
  assert.equal(canRetryAnalysis({ job: { state: "uploading" } }), false);
  assert.equal(canRetryAnalysis(null), false);
});

test("a failed analysis is its own upload phase and is never checked again", () => {
  const failed = analysisDetail("uploading", "failed");
  assert.equal(uploadPhase(failed), "analysis_failed");
  assert.equal(canCheckUpload(failed), false);
  assert.equal(uploadPhase(analysisDetail("uploading", "processing")), "queued");
  assert.equal(uploadPhase(analysisDetail("uploading", "failed", "video")), "queued");
});

test("a retry history line names the failed run without raw error text", () => {
  const line = historyLine({
    created_at: "2026-11-12T00:00:00Z",
    kind: "retry_analysis",
    details: {
      note: "Restarted the site.",
      failed_error_code: "interrupted",
      failed_step: "coaching",
      processing_started_at: "2026-11-11T23:46:00Z",
      worker_id: "w1",
    },
  });
  assert.equal(
    line,
    "2026-11-12T00:00:00Z · retry_analysis · previous run started 2026-11-11T23:46:00Z on worker w1, "
      + "failed at coaching (interrupted) · Restarted the site.",
  );
  assert.equal(
    historyLine({ created_at: "t", kind: "capture_started", details: {} }),
    "t · capture_started",
  );
});
