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
