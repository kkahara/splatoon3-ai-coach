/**
 * Submit-form rules kept free of React so they can be tested directly.
 *
 * `language` is the game's display language in the recording. It selects
 * language-specific vision templates and is never derived from the site locale.
 */

export const GAME_LANGUAGES = ["en", "ja"];

const GAME_LANGUAGE_KEY = "s3-coach-game-language";

/** Saved game-video language, or "" when nothing valid is stored. */
export function rememberedGameLanguage(storage = globalThis.localStorage) {
  try {
    const value = storage?.getItem(GAME_LANGUAGE_KEY);
    return GAME_LANGUAGES.includes(value) ? value : "";
  } catch {
    return "";
  }
}

/** Keep the last chosen game-video language. Site locale is not stored here. */
export function rememberGameLanguage(language, storage = globalThis.localStorage) {
  if (!GAME_LANGUAGES.includes(language)) {
    return;
  }
  try {
    storage?.setItem(GAME_LANGUAGE_KEY, language);
  } catch {
    // Private browsing can reject storage. The current page still has the choice.
  }
}

const REVIEW_CODE_LENGTH = 16;

/** Letters and digits only, grouped as ``XXXX-XXXX-XXXX-XXXX``. */
export function formatReviewCode(raw) {
  const chars = String(raw || "")
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "")
    .slice(0, REVIEW_CODE_LENGTH);
  return chars.replace(/(.{4})(?=.)/g, "$1-");
}

/** True when the masked code has all 16 characters. */
export function reviewCodeComplete(value) {
  return /^[A-Z0-9]{4}(?:-[A-Z0-9]{4}){3}$/.test(String(value || ""));
}

const VIDEO_EXTENSIONS = [".mp4", ".mov"];
const VIDEO_TYPES = ["video/mp4", "video/quicktime"];

/** MP4 or MOV by MIME type or extension. Dropped files bypass the input's `accept`. */
export function isAcceptedVideo(file) {
  if (!file) {
    return false;
  }
  if (VIDEO_TYPES.includes(file.type)) {
    return true;
  }
  const name = String(file.name || "").toLowerCase();
  return VIDEO_EXTENSIONS.some((extension) => name.endsWith(extension));
}

/** i18n key for a chosen file that cannot be sent, or null when it is usable. */
export function videoProblem(file, maxBytes) {
  if (!isAcceptedVideo(file)) {
    return "submit.error_file";
  }
  if (maxBytes && file.size > maxBytes) {
    return "submit.error_size";
  }
  return null;
}

/** Human file size in MB (or KB below 1 MB), such as 512.3 MB. */
export function formatFileSize(bytes) {
  const size = Number(bytes) || 0;
  if (size < 1024 * 1024) {
    return `${Math.max(1, Math.round(size / 1024))} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/** Language plus a video. A review code is optional and does not enable submit. */
export function submitReady({ file, language }) {
  if (!GAME_LANGUAGES.includes(language)) {
    return false;
  }
  return Boolean(file);
}

/** First blocking problem as an i18n key, or null when the form may be sent. */
export function submitError({ file, language, maxBytes, token }) {
  if (!file) {
    return "submit.error_file";
  }
  if (maxBytes && file.size > maxBytes) {
    return "submit.error_size";
  }
  if (!GAME_LANGUAGES.includes(language)) {
    return "submit.error_language";
  }
  if (!token) {
    return "submit.error_check";
  }
  return null;
}

/** USD label for a snapshotted cent amount, such as 199 → "$1.99". */
export function formatUsd(cents) {
  if (typeof cents !== "number" || !Number.isInteger(cents) || cents < 0) {
    return null;
  }
  return `$${(cents / 100).toFixed(2)}`;
}

/** True while the operator can apply or re-apply a code that is not yet accepted. */
export function showVoucherApplyButton(voucherPreview) {
  return !voucherPreview?.accepted;
}

/** i18n keys shown after a successful voucher preview. */
export function voucherPreviewSuccessKeys() {
  return ["submit.voucher_confirmed", "submit.voucher_no_payment"];
}

/** Reset preview validation when the code field is edited. */
export function voucherStateAfterCodeChange() {
  return { voucherPreview: null, voucherError: "" };
}

/** Button label. A priced analysis shows the amount before any upload starts. */
export function submitButtonLabel(t, config, voucher = null) {
  if (voucher?.accepted) {
    return t("submit.submit");
  }
  if (!config?.payment_required) {
    return t("submit.submit");
  }
  const price = formatUsd(config.price_cents);
  if (!price) {
    return t("submit.price_unavailable");
  }
  return t("submit.price_button", { price });
}

/** Whole cents from an admin dollar field. More than two decimal places is rejected. */
export function centsFromDollars(text) {
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(String(text).trim());
  if (!match) {
    return null;
  }
  return Number(match[1]) * 100 + Number((match[2] || "").padEnd(2, "0"));
}

/** POST /api/submissions body. Takes no site locale on purpose. */
export function submissionBody({ token, file, language, account, notify, email, voucherCode }) {
  const body = {
    turnstile_token: token,
    filename: file.name,
    size_bytes: file.size,
    content_type: file.type || "video/mp4",
    language,
    email: account ? null : notify ? email : null,
    notify: Boolean(notify),
    display_name: null,
  };
  const code = String(voucherCode || "").trim();
  if (code) {
    body.voucher_code = code;
  }
  return body;
}
