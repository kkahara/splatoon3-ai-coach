import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { normalizeLocale, translate } from "./translate.js";

const load = (name) => JSON.parse(readFileSync(new URL(name, import.meta.url), "utf8"));
const resources = { en: load("./en.json"), ja: load("./ja.json") };

test("English and Japanese resolve", () => {
  assert.equal(translate(resources, "en", "submit.submit"), "Submit Match");
  assert.notEqual(translate(resources, "ja", "submit.submit"), "Submit Match");
  assert.equal(translate(resources, "ja", "common.deaths"), resources.ja["common.deaths"]);
  assert.equal(
    translate(resources, "en", "submit.choose_video", { mb: 500 }),
    "Choose video (500MB max)",
  );
});

test("a missing Japanese key falls back to English", () => {
  const partial = { en: { "only.en": "Hello {name}" }, ja: {} };
  assert.equal(translate(partial, "ja", "only.en", { name: "Rin" }), "Hello Rin");
});

test("a missing key returns the key", () => {
  assert.equal(translate(resources, "ja", "no.such.key"), "no.such.key");
});

test("unknown locales use English", () => {
  assert.equal(normalizeLocale("fr"), "en");
  assert.equal(normalizeLocale("ja"), "ja");
});

test("the unverified-wording badge has English and Japanese text", () => {
  const en = translate(resources, "en", "review.unverified_wording");
  const ja = translate(resources, "ja", "review.unverified_wording");
  assert.match(en, /^Contains unverified wording/);
  assert.notEqual(ja, en);
  assert.notEqual(ja, "review.unverified_wording");
});

test("current-rule revalidation notes say they are not the original check", () => {
  for (const key of ["review.blocked_current", "review.current_unverified"]) {
    const en = translate(resources, "en", key);
    const ja = translate(resources, "ja", key);
    assert.match(en, /^Re-checked with today's evidence rules \(not the rules used/);
    assert.match(ja, /^現在の証拠ルールで再確認しました（作成時のルールではありません）/);
  }
});

test("every public coaching state has English and Japanese text", () => {
  for (const key of ["review.not_reviewed", "review.blocked", "review.no_recommendation"]) {
    const en = translate(resources, "en", key);
    const ja = translate(resources, "ja", key);
    assert.notEqual(en, key);
    assert.notEqual(ja, en);
  }
  assert.equal(
    translate(resources, "en", "review.no_recommendation"),
    "No recommendation supported by the available evidence.",
  );
});

test("the unofficial and trademark notices have English and Japanese text", () => {
  for (const key of ["legal.unofficial", "legal.trademark"]) {
    const en = translate(resources, "en", key);
    const ja = translate(resources, "ja", key);
    assert.match(en, /not affiliated with, endorsed by, or sponsored by Nintendo/i);
    assert.match(ja, /任天堂/);
    assert.notEqual(ja, en);
  }
});

test("payment terms do not promise an administrator refund", () => {
  const english = translate(resources, "en", "submit.terms");
  const japanese = translate(resources, "ja", "submit.terms");
  assert.match(english, /not refunded automatically/);
  assert.match(japanese, /自動で返金されることはありません/);
  assert.doesNotMatch(english, /administrator can refund/i);
  assert.doesNotMatch(japanese, /管理者が返金/);
});

test("the about page keeps the supplied Japanese story", () => {
  const japanese = translate(resources, "ja", "about.p1");
  const english = translate(resources, "en", "about.p1");
  assert.match(japanese, /ガチパワーは1800ぐらいが最高/);
  assert.notEqual(english, japanese);
});

test("the stale-evidence notice has English and Japanese text", () => {
  const en = translate(resources, "en", "review.stale_evidence");
  const ja = translate(resources, "ja", "review.stale_evidence");
  assert.match(en, /older version of the match evidence/);
  assert.notEqual(ja, en);
  assert.notEqual(ja, "review.stale_evidence");
});
