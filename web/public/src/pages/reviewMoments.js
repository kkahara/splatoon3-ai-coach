/**
 * Review-page grouping kept free of React so it can be tested directly.
 *
 * A key coaching moment has a recommendation that passed the evidence check
 * under both the original and today's rules. Everything else is an other
 * detected moment: detecting an event is not deciding it is worth coaching.
 * Coach output that failed either check is never shown, with or without a
 * warning; the moment falls back to its observed facts.
 */

const WITHHELD_STATUSES = ["contains_unverified_wording", "blocked_unsupported_claim"];
const ATTEMPTED_STATES = ["recommendation", "no_recommendation", "blocked"];

export const REVIEW_STEPS = ["received", "checked", "queued", "analyzing", "coaching", "ready"];

/** True when neither the original nor the current validation flags the output. */
export function passedEvidenceCheck(moment) {
  return (
    !WITHHELD_STATUSES.includes(moment?.assessment_status)
    && !WITHHELD_STATUSES.includes(moment?.current_assessment_status)
  );
}

/** A supported recommendation the page may present as coaching. */
export function isKeyMoment(moment) {
  return (
    moment?.coaching_state === "recommendation"
    && Boolean(moment.assessment)
    && (moment.recommendations || []).length > 0
    && passedEvidenceCheck(moment)
  );
}

/** Coach prose that only describes the moment, or null. Shown as "what happened". */
export function shownObservation(moment) {
  if (moment?.coaching_state !== "no_recommendation" || !moment.assessment) {
    return null;
  }
  return passedEvidenceCheck(moment) ? moment.assessment : null;
}

/** The coach looked at this moment but no recommendation is shown. */
export function coachAttempted(moment) {
  return ATTEMPTED_STATES.includes(moment?.coaching_state) && !isKeyMoment(moment);
}

/** Key and other moments in ranked order. ``index`` is the position the frame URL uses. */
export function splitMoments(moments) {
  const key = [];
  const other = [];
  (moments || []).forEach((moment, index) => {
    (isKeyMoment(moment) ? key : other).push({ moment, index });
  });
  return { key, other };
}

/**
 * The coach's first suggestion from each key moment, ranked order, exact
 * duplicates dropped. A match-level synthesis is not produced upstream, so
 * none is invented here.
 */
export function nextGameTips(keyItems, limit = 3) {
  const seen = new Set();
  const tips = [];
  for (const { moment, index } of keyItems) {
    const text = (moment.recommendations || [])[0];
    if (!text || seen.has(text)) {
      continue;
    }
    seen.add(text);
    tips.push({ text, index, moment });
    if (tips.length === limit) {
      break;
    }
  }
  return tips;
}

/** Observed facts for one moment, in display order. */
export function evidenceLines(moment) {
  if (!moment) {
    return [];
  }
  if (!moment.heading) {
    return [...(moment.statements || [])];
  }
  return [moment.until_active_again, moment.recovery_context, ...(moment.context || [])].filter(
    Boolean,
  );
}

/** Index of the step in progress; ``REVIEW_STEPS.length`` once complete. */
export function reachedStep(view) {
  if (view.status === "complete") {
    return REVIEW_STEPS.length;
  }
  if (view.status === "validating") {
    return 1;
  }
  const index = REVIEW_STEPS.indexOf(view.step);
  return index < 0 ? 0 : index;
}

/** Localized calendar date for an ISO timestamp, or null when it cannot be read. */
export function formatDate(iso, locale) {
  const when = new Date(iso);
  if (!iso || Number.isNaN(when.getTime())) {
    return null;
  }
  return new Intl.DateTimeFormat(locale === "ja" ? "ja-JP" : "en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  }).format(when);
}

/**
 * Observed game-timer seconds as m:ss, or null. The timer only shows whole
 * seconds, so no fractional precision is presented.
 */
export function formatRemaining(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) {
    return null;
  }
  const whole = Math.max(0, Math.floor(Number(seconds)));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/** DOM id for a moment so match tips can link to it. */
export function momentAnchor(index) {
  return `moment-${index}`;
}
