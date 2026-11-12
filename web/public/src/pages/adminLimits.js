/** Upload-limit form helpers. The server enforces and validates; these only shape the request. */

export const UPLOAD_LIMITS = [
  { key: "account_uploads_per_hour", label: "Logged-in uploads per hour", resettable: false },
  { key: "guest_uploads_per_hour", label: "Guest uploads per hour", resettable: true },
  { key: "network_uploads_per_hour", label: "Network uploads per hour", resettable: true },
];

/** Text inputs from a price/limit payload. */
export function limitInputs(payload) {
  return Object.fromEntries(
    UPLOAD_LIMITS.map(({ key }) => [key, Number.isInteger(payload?.[key]) ? String(payload[key]) : ""]),
  );
}

/** "(environment default)" or "(saved setting)" for one limit. */
export function limitSourceLabel(payload, key) {
  return payload?.[`${key}_source`] === "saved" ? "saved setting" : "environment default";
}

/**
 * Body for PUT /api/admin/upload-limit with only the edited fields.
 * Returns { changes } or { error }. An empty change set is an error.
 */
export function limitChanges(payload, inputs) {
  const changes = {};
  for (const { key } of UPLOAD_LIMITS) {
    const text = String(inputs[key] ?? "").trim();
    if (text === String(payload?.[key] ?? "")) {
      continue;
    }
    const value = Number(text);
    if (!/^\d+$/.test(text) || !Number.isInteger(value) || value < 1 || value > 100) {
      return { error: "Enter a whole number from 1 to 100." };
    }
    changes[key] = value;
  }
  if (Object.keys(changes).length === 0) {
    return { error: "Nothing changed." };
  }
  return { changes };
}

/** Keep the price fields and replace every limit field from a limit response. */
export function mergeLimits(current, next) {
  if (!current) {
    return current;
  }
  const merged = { ...current };
  for (const { key } of UPLOAD_LIMITS) {
    merged[key] = next[key];
    merged[`${key}_source`] = next[`${key}_source`];
  }
  return merged;
}
