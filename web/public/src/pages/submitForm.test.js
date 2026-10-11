import assert from "node:assert/strict";
import test from "node:test";
import { centsFromDollars, formatUsd, submissionBody, submitButtonLabel, submitError } from "./submitForm.js";

const file = { name: "match.mov", size: 10, type: "video/quicktime" };
const ready = { file, maxBytes: 100, token: "ok" };

test("a submission without a game language is blocked", () => {
  assert.equal(submitError({ ...ready, language: "" }), "submit.error_language");
  assert.equal(submitError({ ...ready, language: undefined }), "submit.error_language");
});

test("choosing English or Japanese lets the submission proceed", () => {
  assert.equal(submitError({ ...ready, language: "en" }), null);
  assert.equal(submitError({ ...ready, language: "ja" }), null);
});

test("a missing size cap does not reject a large file", () => {
  assert.equal(
    submitError({ ...ready, maxBytes: null, file: { ...file, size: 2_000_000_000 }, language: "en" }),
    null,
  );
});

test("file problems are reported before the language", () => {
  assert.equal(submitError({ ...ready, file: null, language: "" }), "submit.error_file");
  assert.equal(
    submitError({ ...ready, file: { ...file, size: 200 }, language: "" }),
    "submit.error_size",
  );
});

for (const [siteLocale, gameLanguage] of [
  ["ja", "en"],
  ["en", "ja"],
]) {
  test(`site locale ${siteLocale} keeps the chosen game language ${gameLanguage}`, () => {
    const body = submissionBody({
      token: "ok",
      file,
      language: gameLanguage,
      account: null,
      notify: false,
      email: "",
      displayName: "",
      locale: siteLocale,
    });
    assert.equal(body.language, gameLanguage);
    assert.equal("locale" in body, false);
  });
}

test("a priced submit button shows the amount before upload", () => {
  const t = (key, params) => (params?.price ? `${key}:${params.price}` : key);
  assert.equal(formatUsd(199), "$1.99");
  assert.equal(formatUsd(null), null);
  assert.equal(
    submitButtonLabel(t, { payment_required: true, price_cents: null }),
    "submit.price_unavailable",
  );
  assert.equal(
    submitButtonLabel(t, { payment_required: true, price_cents: 199 }),
    "submit.price_button:$1.99",
  );
  assert.equal(submitButtonLabel(t, { payment_required: false, price_cents: 199 }), "submit.submit");
  assert.equal(centsFromDollars("1.99"), 199);
  assert.equal(centsFromDollars("1.999"), null);
});
