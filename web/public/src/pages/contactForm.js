/** Contact form rules. The server repeats these checks. */

export const CONTACT_REASONS = [
  "technical",
  "account_payment",
  "feedback",
  "collaboration",
  "other",
];

const SERVER_ERRORS = {
  "Choose a reason for contacting.": "contact.error_reason",
  "Enter your name.": "contact.error_name",
  "That name is too long.": "contact.error_name_long",
  "Enter a valid email address.": "contact.error_email",
  "Enter a message.": "contact.error_message",
  "That message is too long.": "contact.error_message_long",
  "That review link is too long.": "contact.error_review",
  "Complete the check before sending.": "contact.error_check",
  "Too many messages from this network. Try again later.": "contact.error_rate",
  "The contact form is unavailable right now.": "contact.error_unavailable",
  "The message could not be sent. Try again later.": "contact.error_send",
};

export function showReviewLink(reason) {
  return reason === "technical";
}

export function showPaymentWarning(reason) {
  return reason === "account_payment";
}

/** A problem key, or null when the form can be sent. */
export function contactProblem({ reason, name, email, message, reference, token, devMode }) {
  if (!CONTACT_REASONS.includes(reason)) {
    return "contact.error_reason";
  }
  if (!String(name || "").trim()) {
    return "contact.error_name";
  }
  if (!String(email || "").trim() || !String(email).includes("@")) {
    return "contact.error_email";
  }
  const text = String(message || "").trim();
  if (!text) {
    return "contact.error_message";
  }
  if (text.length > 4000) {
    return "contact.error_message_long";
  }
  if (showReviewLink(reason) && String(reference || "").trim().length > 300) {
    return "contact.error_review";
  }
  if (!devMode && !token) {
    return "contact.error_check";
  }
  return null;
}

export function contactBody({ token, reason, name, email, message, reference, trap }) {
  return {
    turnstile_token: token,
    reason,
    name: String(name || "").trim(),
    email: String(email || "").trim(),
    message: String(message || "").trim(),
    reference: showReviewLink(reason) ? String(reference || "").trim() : "",
    leave_blank: trap || "",
  };
}

/** Map a server detail to a translation key. Unknown details stay as returned. */
export function contactErrorText(detail, t) {
  const key = SERVER_ERRORS[detail];
  return key ? t(key) : detail;
}
