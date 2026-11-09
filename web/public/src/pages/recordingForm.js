/** Validate syntax without claiming that Nintendo can retrieve a replay. */
export function normalizedReviewCode(raw) {
  if (!/^[A-Za-z0-9\s-]+$/.test(raw) || /[^\x00-\x7F]/.test(raw)) return null;
  const compact = raw.replace(/[\s-]/g, "").toUpperCase();
  return /^[A-Z0-9]{16}$/.test(compact) ? compact.match(/.{4}/g).join("-") : null;
}

/** Acquisition is customer-visible only after verified payment and contact. */
export function recordingQueued(view) {
  return view?.payment_state === "paid" && view?.acquisition?.contact_verified
    && ["awaiting_recording", "recording", "uploading"].includes(view.acquisition.state);
}
