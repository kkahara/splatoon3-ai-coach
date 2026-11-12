/** Validate syntax without claiming that Nintendo can retrieve a replay. */
export function normalizedReviewCode(raw) {
  if (!/^[A-Za-z0-9\s-]+$/.test(raw) || /[^\x00-\x7F]/.test(raw)) return null;
  const compact = raw.replace(/[\s-]/g, "").toUpperCase();
  return /^[A-Z0-9]{16}$/.test(compact) ? compact.match(/.{4}/g).join("-") : null;
}

const CODE_CHARS = 16;

function codeChars(text) {
  return text.replace(/[^A-Za-z0-9]/g, "").toUpperCase();
}

/** Group up to 16 letters/digits as XXXX-XXXX-XXXX-XXXX; everything else is dropped. */
export function formatReviewCode(raw) {
  return (codeChars(raw).slice(0, CODE_CHARS).match(/.{1,4}/g) || []).join("-");
}

/** Caret index in a formatted code just after its first `count` letters/digits. */
function caretAfter(formatted, count) {
  if (count <= 0) return 0;
  let seen = 0;
  for (let index = 0; index < formatted.length; index += 1) {
    if (formatted[index] !== "-") seen += 1;
    if (seen === count) return index + 1;
  }
  return formatted.length;
}

/**
 * Apply one edit to the masked review-code input.
 *
 * Typed or pasted hyphens and spaces are ignored because the mask inserts them.
 * Deleting next to an inserted hyphen removes the neighbouring character instead.
 */
export function maskReviewCodeEdit({ previous, raw, caret, inputType }) {
  let chars = codeChars(raw);
  let before = codeChars(raw.slice(0, caret)).length;
  if (codeChars(previous) === chars) {
    if (inputType === "deleteContentBackward" && before > 0) {
      chars = chars.slice(0, before - 1) + chars.slice(before);
      before -= 1;
    } else if (inputType === "deleteContentForward" && before < chars.length) {
      chars = chars.slice(0, before) + chars.slice(before + 1);
    }
  }
  const value = formatReviewCode(chars);
  return { value, caret: caretAfter(value, Math.min(before, CODE_CHARS)) };
}

/** Paid by card, or no payment needed because a voucher was redeemed. */
export function recordingAuthorized(view) {
  return view?.payment_state === "paid" || view?.payment_state === "not_required";
}

/** Acquisition is customer-visible only after authorization and verified contact. */
export function recordingQueued(view) {
  return recordingAuthorized(view) && view?.acquisition?.contact_verified
    && ["awaiting_recording", "recording", "uploading"].includes(view.acquisition.state);
}

/** The operator may upload a file while the job is still waiting to be verified. */
export function canUploadRecording(job) {
  return ["awaiting_recording", "recording"].includes(job?.state);
}

/** A verified review-code recording whose analysis failed. The server checks this again. */
export function canRetryAnalysis(detail) {
  return detail?.job?.state === "uploading"
    && detail?.analysis?.status === "failed"
    && detail?.analysis?.input_type === "review_code";
}

/** One audit history line; a retry names the failed run it replaced. */
export function historyLine(event) {
  const details = event.details || {};
  const parts = [event.created_at, event.kind];
  if (event.kind === "retry_analysis") {
    const worker = details.worker_id ? ` on worker ${details.worker_id}` : "";
    parts.push(
      `previous run started ${details.processing_started_at}${worker}, `
      + `failed at ${details.failed_step} (${details.failed_error_code})`,
    );
  }
  if (details.note) parts.push(details.note);
  return parts.join(" · ");
}

const RECORDING_TYPES = { mp4: "video/mp4", mov: "video/quicktime" };

/** File picker filter for operator recordings. */
export const RECORDING_ACCEPT = "video/mp4,video/quicktime,.mp4,.mov";

/** The browser's type, or the one implied by the extension when the browser gives none. */
export function recordingContentType(file) {
  if (file?.type) return file.type;
  const extension = (file?.name || "").split(".").pop().toLowerCase();
  return RECORDING_TYPES[extension] || "";
}

/**
 * Where the operator upload stands, from the job, its latest attempt and its events.
 * `transferring` is this tab's own PUT in progress; the server cannot see it.
 */
export function uploadPhase(detail, transferring = false, now = new Date()) {
  const job = detail?.job;
  const attempt = detail?.upload_attempt;
  if (!job) return "none";
  if (canRetryAnalysis(detail)) return "analysis_failed";
  if (job.state === "uploading") {
    const queued = (detail.events || []).some((event) => event.kind === "recording_analysis_queued");
    return queued ? "queued" : "verified";
  }
  if (transferring) return "transferring";
  if (!attempt) return "none";
  const open = job.state === "recording";
  const linkUsable = new Date(attempt.link_expires_at) > now;
  if (open && attempt.status === "live" && linkUsable) return "permitted";
  if (new Date(attempt.replace_after) <= now) return "none";
  if (open && (attempt.status === "live" || attempt.status === "expired")) return "link_expired";
  if (open && attempt.status === "rejected") return "rejected";
  return "waiting";
}

function extension(name) {
  const dot = (name || "").lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot).toLowerCase();
}

/**
 * True when a file has the type and size declared for this attempt.
 * This is not proof that it is the same file; the server checks the stored object.
 */
export function fileMatchesAttempt(file, attempt) {
  if (!attempt) return true;
  return extension(file?.name) === attempt.suffix && file?.size === attempt.size_bytes;
}

/** The next time the attempt's phase can change on its own, in ms, or null. */
export function nextUploadDeadline(detail, now = new Date()) {
  const attempt = detail?.upload_attempt;
  if (!attempt) return null;
  const upcoming = [attempt.link_expires_at, attempt.replace_after]
    .map((value) => new Date(value).getTime())
    .filter((time) => time > now.getTime());
  return upcoming.length ? Math.min(...upcoming) : null;
}

/** An open attempt whose stored file can be checked without a new upload link. */
export function canCheckUpload(detail) {
  const status = detail?.upload_attempt?.status;
  if (detail?.job?.state === "uploading") return uploadPhase(detail) === "verified";
  return detail?.job?.state === "recording" && (status === "live" || status === "expired");
}

/** An attempt that can be cancelled so the job waits for a recording again. */
export function canCancelUpload(detail) {
  const status = detail?.upload_attempt?.status;
  return detail?.job?.state === "recording" && ["live", "expired", "rejected"].includes(status);
}

const STATION_HOLD = new Set(["capturing", "interrupted", "finalized", "upload_uncertain"]);

/** True while a station take still owns the recording and a file upload must wait. */
export function stationHoldsRecording(phase) {
  return STATION_HOLD.has(phase);
}

/** The job whose capture is running, from the queue's station summary. */
export function capturingJob(takes) {
  return Object.keys(takes || {}).find((id) => takes[id]?.phase === "capturing") || null;
}

export const ACTIVE_POLL_MS = 1000;
export const IDLE_POLL_MS = 5000;

/**
 * What the queue page polls. A running capture is followed every second whichever job is
 * selected; otherwise the queue is reloaded every five seconds so a capture started
 * elsewhere appears.
 */
export function stationPoll(takes, selectedId, selectedPhase) {
  const running = capturingJob(takes);
  if (running) return { kind: "station", id: running, ms: ACTIVE_POLL_MS };
  if (selectedId && selectedPhase === "upload_uncertain") {
    return { kind: "station", id: selectedId, ms: ACTIVE_POLL_MS };
  }
  return { kind: "queue", id: null, ms: IDLE_POLL_MS };
}

/** Only the first load may open a running capture, and never over an existing selection. */
export function hydrationSelection(takes, selectedId, initial) {
  if (!initial || selectedId) return null;
  return capturingJob(takes);
}

/** Stripe id for a card payment, or an explanation when the job has none. */
export function stripeReference(detail) {
  if (detail?.authorization === "voucher") {
    return "No Stripe payment (paid by voucher).";
  }
  if (detail?.authorization === "admin") {
    return "No Stripe payment (administrator request).";
  }
  return detail?.payment_reference || "No Stripe payment yet.";
}
