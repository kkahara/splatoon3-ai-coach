/**
 * Past-coaching card text, kept free of React so it can be tested directly.
 *
 * Only stored facts are shown: the mode and stage ids the pipeline latched from
 * the match intro, and the number of moments the completed review lists.
 * Missing facts are omitted, never replaced with placeholders. The server's
 * customer-facing state (`display_status`) and what the review contains
 * (`meta`) stay separate.
 */

const SEPARATOR = " · ";

/** Card text and destination for one history item. `t(key, params)` is the UI translator. */
export function historyCard(item, t, locale) {
  const title = joinParts([
    knownName(t, "match.mode", item.battle_mode_id),
    knownName(t, "match.stage", item.stage_id),
  ]);
  return {
    title: title || t("coaching.match"),
    status: t(statusKey(item.display_status)),
    meta: joinParts([momentText(item, t), formatWhen(item.created_at, locale)]),
    href: `/coaching/${item.id}`,
  };
}

/** Readable local date and time without seconds; the raw stamp when it cannot be read. */
export function formatWhen(stamp, locale) {
  const date = new Date(stamp);
  if (!stamp || Number.isNaN(date.getTime())) {
    return stamp || "";
  }
  return new Intl.DateTimeFormat(locale === "ja" ? "ja-JP" : undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

/** Customer-facing statuses the server may send as `display_status`. */
export const DISPLAY_STATUSES = new Set([
  "payment_required",
  "email_confirmation",
  "awaiting_recording",
  "recording",
  "awaiting_analysis",
  "analyzing",
  "complete",
  "needs_attention",
  "failed",
  "expired",
  "cancelled",
]);

function statusKey(displayStatus) {
  if (DISPLAY_STATUSES.has(displayStatus)) {
    return `coaching.status.${displayStatus}`;
  }
  if (displayStatus) {
    console.warn("Unknown history display_status", displayStatus);
  }
  return "coaching.status.unknown";
}

function momentText(item, t) {
  if (item.status !== "complete") {
    return "";
  }
  const count = item.outcome === "no_deaths" ? 0 : item.moment_count;
  if (count === 0) {
    return t("coaching.moments.none");
  }
  if (!Number.isInteger(count) || count < 0) {
    return "";
  }
  return count === 1 ? t("coaching.moments.one") : t("coaching.moments.other", { count });
}

function knownName(t, prefix, id) {
  if (!id) {
    return "";
  }
  const key = `${prefix}.${id}`;
  const name = t(key);
  return name && name !== key ? name : "";
}

function joinParts(parts) {
  return parts.filter(Boolean).join(SEPARATOR);
}
