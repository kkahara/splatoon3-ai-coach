import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { normalizeLocale, translate } from "./translate.js";

const load = (name) => JSON.parse(readFileSync(new URL(name, import.meta.url), "utf8"));
const resources = { en: load("./en.json"), ja: load("./ja.json") };

test("English and Japanese resolve", () => {
  assert.equal(translate(resources, "en", "submit.submit"), "Start analysis");
  assert.equal(translate(resources, "ja", "submit.submit"), "分析を開始");
  assert.equal(translate(resources, "ja", "common.deaths"), resources.ja["common.deaths"]);
  assert.equal(
    translate(resources, "en", "submit.drop_limits", { mb: 500 }),
    "MP4 / MOV • Up to 500 MB",
  );
  assert.equal(
    translate(resources, "ja", "submit.retention", { days: 30 }),
    "アップロードした動画とレビューは30日後に自動削除されます。",
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

test("every public coaching state has English and Japanese text", () => {
  const keys = [
    "review.other_intro",
    "review.what_happened",
    "review.no_recommendation",
    "review.no_recommendation_detail",
  ];
  for (const key of keys) {
    const en = translate(resources, "en", key);
    const ja = translate(resources, "ja", key);
    assert.notEqual(en, key);
    assert.notEqual(ja, en);
  }
  assert.equal(
    translate(resources, "en", "review.no_recommendation"),
    "No coaching recommendation for this moment",
  );
});

test("the review page has no engineering-style validation warning", () => {
  for (const locale of ["en", "ja"]) {
    for (const value of Object.values(resources[locale])) {
      assert.doesNotMatch(value, /unverified wording|Re-checked with today's evidence rules/i);
      assert.doesNotMatch(value, /未検証の表現|現在の証拠ルールで再確認/);
    }
  }
});

test("the unofficial and trademark notices have English and Japanese text", () => {
  for (const key of ["legal.unofficial", "legal.trademark"]) {
    const en = translate(resources, "en", key);
    const ja = translate(resources, "ja", key);
    assert.match(en, /not affiliated with/i);
    assert.match(ja, /任天堂とは関係/);
    assert.notEqual(ja, en);
  }
  assert.match(
    translate(resources, "en", "legal.trademark"),
    /not affiliated with, endorsed by, or sponsored by Nintendo/,
  );
});

test("English and Japanese define the same keys", () => {
  assert.deepEqual(Object.keys(resources.ja).sort(), Object.keys(resources.en).sort());
});

test("payment terms do not promise an administrator refund", () => {
  const english = translate(resources, "en", "submit.terms");
  const japanese = translate(resources, "ja", "submit.terms");
  assert.match(english, /not refunded automatically/);
  assert.match(japanese, /自動で返金されることはありません/);
  assert.doesNotMatch(english, /administrator can refund/i);
  assert.doesNotMatch(japanese, /管理者が返金/);
});

test("voucher preview success copy is concise in English and Japanese", () => {
  const keys = ["submit.voucher_confirmed", "submit.voucher_no_payment"];
  assert.deepEqual(keys, ["submit.voucher_confirmed", "submit.voucher_no_payment"]);
  assert.equal(resources.ja["submit.voucher_confirmed"], "✓ クーポンを確認しました");
  assert.equal(resources.ja["submit.voucher_no_payment"], "お支払いは不要です。");
  assert.equal(resources.en["submit.voucher_confirmed"], "✓ Voucher confirmed");
  assert.equal(resources.en["submit.voucher_no_payment"], "No payment is required.");
  for (const locale of ["en", "ja"]) {
    for (const key of keys) {
      const text = translate(resources, locale, key);
      assert.doesNotMatch(text, /\$0\.00/);
      assert.doesNotMatch(text, /Stripe/i);
      assert.doesNotMatch(text, /checked again when the video is submitted/i);
      assert.doesNotMatch(text, /動画の送信時にもう一度確認/);
      assert.doesNotMatch(text, /分析はまだ開始されていません/);
      assert.doesNotMatch(text, /Payment was already made through the seller/i);
      assert.doesNotMatch(text, /代金は販売元で支払い済み/);
    }
  }
  assert.equal("submit.voucher_accepted" in resources.en, false);
  assert.equal("submit.voucher_prepaid" in resources.en, false);
  assert.equal("submit.voucher_payment" in resources.en, false);
});

test("voucher validation error messages are unchanged", () => {
  for (const reason of ["invalid", "expired", "exhausted", "revoked", "inactive", "wrong_service"]) {
    const key = `submit.voucher.${reason}`;
    assert.notEqual(translate(resources, "en", key), key);
    assert.notEqual(translate(resources, "ja", key), key);
    assert.notEqual(translate(resources, "en", key), translate(resources, "ja", key));
  }
});

test("the pre-checkout price makes applicable tax clear", () => {
  const price = { price: "$2.00" };
  assert.equal(
    translate(resources, "en", "submit.price_button", price),
    "Start analysis — $2.00 + applicable tax",
  );
  assert.equal(translate(resources, "ja", "submit.price_button", price), "分析を開始 — $2.00（税別）");
  assert.match(translate(resources, "en", "submit.one_time"), /applicable tax/);
  assert.match(translate(resources, "ja", "submit.one_time"), /税/);
});

test("the about page keeps the supplied Japanese story", () => {
  const japanese = translate(resources, "ja", "about.p3");
  const english = translate(resources, "en", "about.p1");
  assert.match(japanese, /Xパワーは1700くらいが最高/);
  assert.notEqual(english, japanese);
});

test("the stale-evidence notice has English and Japanese text", () => {
  const en = translate(resources, "en", "review.stale_evidence");
  const ja = translate(resources, "ja", "review.stale_evidence");
  assert.match(en, /older version of the match evidence/);
  assert.notEqual(ja, en);
  assert.notEqual(ja, "review.stale_evidence");
});
