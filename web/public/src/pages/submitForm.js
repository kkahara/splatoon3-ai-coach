/**
 * Submit-form rules kept free of React so they can be tested directly.
 *
 * `language` is the game's display language in the recording. It selects
 * language-specific vision templates and is never derived from the site locale.
 */

export const GAME_LANGUAGES = ["en", "ja"];

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

/** Button label. A priced analysis shows the amount before any upload starts. */
export function submitButtonLabel(t, config) {
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
export function submissionBody({ token, file, language, account, notify, email, displayName }) {
  return {
    turnstile_token: token,
    filename: file.name,
    size_bytes: file.size,
    content_type: file.type || "video/mp4",
    language,
    email: account ? null : notify ? email : null,
    notify: account ? false : notify,
    display_name: displayName || null,
  };
}
