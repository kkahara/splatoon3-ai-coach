import assert from "node:assert/strict";
import test from "node:test";
import {
  centsFromDollars,
  formatFileSize,
  formatReviewCode,
  formatUsd,
  isAcceptedVideo,
  rememberedGameLanguage,
  rememberGameLanguage,
  reviewCodeComplete,
  submissionBody,
  submitButtonLabel,
  submitError,
  submitReady,
  videoProblem,
} from "./submitForm.js";

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
      locale: siteLocale,
    });
    assert.equal(body.language, gameLanguage);
    assert.equal(body.display_name, null);
    assert.equal("locale" in body, false);
    assert.equal("voucher_code" in body, false);
  });
}

test("a voucher code is posted with the submission and a blank one is omitted", () => {
  const body = submissionBody({
    token: "ok",
    file,
    language: "en",
    account: null,
    notify: false,
    email: "",
    voucherCode: " squidlab50 ",
  });
  assert.equal(body.voucher_code, "squidlab50");
  const blank = submissionBody({
    token: "ok",
    file,
    language: "en",
    account: null,
    notify: false,
    email: "",
    voucherCode: "   ",
  });
  assert.equal("voucher_code" in blank, false);
});

test("a chosen game language is remembered", () => {
  const stored = new Map();
  const storage = {
    getItem: (key) => (stored.has(key) ? stored.get(key) : null),
    setItem: (key, value) => stored.set(key, value),
  };
  assert.equal(rememberedGameLanguage(storage), "");
  rememberGameLanguage("ja", storage);
  assert.equal(rememberedGameLanguage(storage), "ja");
  rememberGameLanguage("fr", storage);
  assert.equal(rememberedGameLanguage(storage), "ja");
  rememberGameLanguage("en", storage);
  assert.equal(rememberedGameLanguage(storage), "en");
});

test("a logged-in submission follows the checkbox and omits the address", () => {
  const account = { email: "rin@example.com" };
  const on = submissionBody({
    token: "ok",
    file,
    language: "en",
    account,
    notify: true,
    email: "other@example.com",
  });
  assert.equal(on.notify, true);
  assert.equal(on.email, null);
  const off = submissionBody({
    token: "ok",
    file,
    language: "en",
    account,
    notify: false,
    email: "other@example.com",
  });
  assert.equal(off.notify, false);
  assert.equal(off.email, null);
});

test("a guest sends an address only when notices are requested", () => {
  const on = submissionBody({
    token: "ok",
    file,
    language: "en",
    account: null,
    notify: true,
    email: "player@example.com",
  });
  assert.equal(on.notify, true);
  assert.equal(on.email, "player@example.com");
  const off = submissionBody({
    token: "ok",
    file,
    language: "en",
    account: null,
    notify: false,
    email: "player@example.com",
  });
  assert.equal(off.notify, false);
  assert.equal(off.email, null);
});

test("a review code is masked as four groups of four", () => {
  assert.equal(formatReviewCode("ab"), "AB");
  assert.equal(formatReviewCode("abcd1234efgh5678"), "ABCD-1234-EFGH-5678");
  assert.equal(formatReviewCode("abcd-1234-efgh-5678"), "ABCD-1234-EFGH-5678");
  assert.equal(formatReviewCode("abcd 1234 efgh 5678!!extra"), "ABCD-1234-EFGH-5678");
  assert.equal(reviewCodeComplete("ABCD-1234-EFGH-5678"), true);
  assert.equal(reviewCodeComplete("ABCD-1234-EFGH-567"), false);
  assert.equal(reviewCodeComplete(""), false);
});

test("the submit button waits for a language and a video", () => {
  assert.equal(submitReady({ file, language: "", reviewCode: "" }), false);
  assert.equal(submitReady({ file: null, language: "en", reviewCode: "" }), false);
  assert.equal(submitReady({ file: null, language: "en", reviewCode: "ABCD-1234" }), false);
  assert.equal(submitReady({ file, language: "ja", reviewCode: "" }), true);
  assert.equal(
    submitReady({ file: null, language: "en", reviewCode: "ABCD-1234-EFGH-5678" }),
    false,
  );
});

test("a dropped file must be an MP4 or MOV within the cap", () => {
  assert.equal(isAcceptedVideo({ name: "clip.MOV", type: "" }), true);
  assert.equal(isAcceptedVideo({ name: "clip", type: "video/mp4" }), true);
  assert.equal(isAcceptedVideo({ name: "clip.webm", type: "video/webm" }), false);
  assert.equal(isAcceptedVideo(null), false);
  assert.equal(videoProblem({ name: "a.png", type: "image/png", size: 1 }, 100), "submit.error_file");
  assert.equal(videoProblem({ ...file, size: 200 }, 100), "submit.error_size");
  assert.equal(videoProblem({ ...file, size: 200 }, null), null);
  assert.equal(videoProblem(file, 100), null);
});

test("file sizes read in KB or MB", () => {
  assert.equal(formatFileSize(10), "1 KB");
  assert.equal(formatFileSize(512 * 1024), "512 KB");
  assert.equal(formatFileSize(1.5 * 1024 * 1024), "1.5 MB");
});

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
  assert.equal(
    submitButtonLabel(t, { payment_required: true, price_cents: 199 }, { accepted: true }),
    "submit.submit",
  );
  assert.equal(centsFromDollars("1.99"), 199);
  assert.equal(centsFromDollars("1.999"), null);
});
