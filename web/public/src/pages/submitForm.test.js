import assert from "node:assert/strict";
import test from "node:test";
import { submissionBody, submitError } from "./submitForm.js";

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
