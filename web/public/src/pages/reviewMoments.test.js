import assert from "node:assert/strict";
import test from "node:test";
import {
  coachAttempted,
  evidenceLines,
  formatDate,
  formatRemaining,
  isKeyMoment,
  nextGameTips,
  reachedStep,
  REVIEW_STEPS,
  shownObservation,
  splitMoments,
} from "./reviewMoments.js";

const coached = (recommendations = ["Check the map."]) => ({
  coaching_state: "recommendation",
  assessment: "You pushed in alone.",
  assessment_status: "validated",
  current_assessment_status: "validated",
  recommendations,
});

test("only supported recommendations are key moments", () => {
  assert.equal(isKeyMoment(coached()), true);
  assert.equal(isKeyMoment(coached([])), false);
  assert.equal(isKeyMoment({ ...coached(), assessment: "" }), false);
  assert.equal(isKeyMoment({ coaching_state: "no_recommendation", assessment: "Text" }), false);
  assert.equal(isKeyMoment({ coaching_state: "not_reviewed" }), false);
  assert.equal(isKeyMoment({ coaching_state: "blocked", assessment: null }), false);
  assert.equal(isKeyMoment({ coaching_state: null, assessment: "legacy" }), false);
});

test("unverified wording under either rule set is never shown as coaching", () => {
  const original = { ...coached(), assessment_status: "contains_unverified_wording" };
  const current = { ...coached(), current_assessment_status: "contains_unverified_wording" };
  for (const moment of [original, current]) {
    assert.equal(isKeyMoment(moment), false);
    assert.equal(coachAttempted(moment), true);
  }
  const observed = { coaching_state: "no_recommendation", assessment: "You got a splat." };
  assert.equal(shownObservation(observed), "You got a splat.");
  assert.equal(
    shownObservation({ ...observed, current_assessment_status: "contains_unverified_wording" }),
    null,
  );
  assert.equal(shownObservation(coached()), null);
});

test("only moments the coach looked at say no recommendation was made", () => {
  assert.equal(coachAttempted({ coaching_state: "no_recommendation", assessment: "x" }), true);
  assert.equal(coachAttempted({ coaching_state: "blocked" }), true);
  assert.equal(coachAttempted({ coaching_state: "not_reviewed" }), false);
  assert.equal(coachAttempted(coached()), false);
});

test("splitting keeps ranked order and the original frame index", () => {
  const moments = [{ coaching_state: "not_reviewed" }, coached(), { coaching_state: "blocked" }];
  const { key, other } = splitMoments(moments);
  assert.deepEqual(key.map((item) => item.index), [1]);
  assert.deepEqual(other.map((item) => item.index), [0, 2]);
  assert.deepEqual(splitMoments(undefined), { key: [], other: [] });
});

test("next-game tips take one suggestion per moment, deduplicated, at most three", () => {
  const items = [
    coached(["Check the map.", "Second idea."]),
    coached(["Check the map."]),
    coached([]),
    coached(["Hold cover."]),
    coached(["Swim back."]),
    coached(["Fourth."]),
  ].map((moment, index) => ({ moment, index }));
  const tips = nextGameTips(items);
  assert.deepEqual(
    tips.map((tip) => [tip.text, tip.index]),
    [
      ["Check the map.", 0],
      ["Hold cover.", 3],
      ["Swim back.", 4],
    ],
  );
});

test("evidence lines put recovery facts before pre-death context", () => {
  const death = {
    heading: "Death — 2:43 remaining",
    until_active_again: "9.0s out of play",
    recovery_context: null,
    context: ["Special was not ready just before the death."],
    statements: ["duplicate factor line"],
  };
  assert.deepEqual(evidenceLines(death), [
    "9.0s out of play",
    "Special was not ready just before the death.",
  ]);
  assert.deepEqual(evidenceLines({ heading: null, statements: ["A splat."] }), ["A splat."]);
});

test("progress reaches the end only when complete", () => {
  assert.equal(reachedStep({ status: "complete" }), REVIEW_STEPS.length);
  assert.equal(reachedStep({ status: "validating" }), 1);
  assert.equal(reachedStep({ status: "processing", step: "coaching" }), 4);
  assert.equal(reachedStep({ status: "processing", step: "unknown" }), 0);
});

test("expiry dates are localized and unreadable ones are dropped", () => {
  assert.equal(formatDate("2026-11-20T12:00:00Z", "en"), "November 20, 2026");
  assert.equal(formatDate("2026-11-20T12:00:00Z", "ja"), "2026年11月20日");
  assert.equal(formatDate("", "en"), null);
  assert.equal(formatDate("nonsense", "en"), null);
});

test("remaining game time is whole-second m:ss and absent when unobserved", () => {
  assert.equal(formatRemaining(206), "3:26");
  assert.equal(formatRemaining(65), "1:05");
  assert.equal(formatRemaining(9), "0:09");
  assert.equal(formatRemaining(0), "0:00");
  assert.equal(formatRemaining(206.9), "3:26");
  assert.equal(formatRemaining(null), null);
  assert.equal(formatRemaining(undefined), null);
});
