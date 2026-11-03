import assert from "node:assert/strict";
import test from "node:test";
import {
  centsFromDollars,
  formatFileSize,
  formatReviewCode,
  formatUsd,
  isAcceptedVideo,
  removeObsoleteStorage,
  reviewCodeComplete,
  submissionBody,
  showVoucherApplyButton,
  submitButtonLabel,
  submitError,
  submitReady,
  videoProblem,
  voucherPreviewSuccessKeys,
  voucherStateAfterCodeChange,
} from "./submitForm.js";

const file = { name: "match.mov", size: 10, type: "video/quicktime" };
const ready = { file, maxBytes: 100, token: "ok" };

test("a video with a check token can be submitted without any language", () => {
  assert.equal(submitError(ready), null);
});

test("the removed game-language preference is deleted and nothing else", () => {
  const entries = new Map([
    ["s3-coach-game-language", "ja"],
    ["s3-locale", "ja"],
  ]);
  const storage = { removeItem: (key) => entries.delete(key) };
  removeObsoleteStorage(() => storage);
  assert.deepEqual([...entries.keys()], ["s3-locale"]);
  removeObsoleteStorage(() => storage);
  assert.deepEqual([...entries.keys()], ["s3-locale"]);
});

test("blocked storage does not break the page", () => {
  assert.doesNotThrow(() =>
    removeObsoleteStorage(() => {
      throw new Error("SecurityError");
    }),
  );
});

test("a missing size cap does not reject a large file", () => {
  assert.equal(
    submitError({ ...ready, maxBytes: null, file: { ...file, size: 2_000_000_000 } }),
    null,
  );
});

test("file problems are reported before the check token", () => {
  assert.equal(submitError({ ...ready, file: null, token: "" }), "submit.error_file");
  assert.equal(
    submitError({ ...ready, file: { ...file, size: 200 }, token: "" }),
    "submit.error_size",
  );
  assert.equal(submitError({ ...ready, token: "" }), "submit.error_check");
});

for (const siteLocale of ["en", "ja"]) {
  test(`site locale ${siteLocale} posts no locale and no game language`, () => {
    const body = submissionBody({
      token: "ok",
      file,
      account: null,
      notify: false,
      email: "",
      locale: siteLocale,
      language: siteLocale,
    });
    assert.equal("language" in body, false);
    assert.equal(body.display_name, null);
    assert.equal("locale" in body, false);
    assert.equal("voucher_code" in body, false);
  });
}

test("a voucher code is posted with the submission and a blank one is omitted", () => {
  const body = submissionBody({
    token: "ok",
    file,
    account: null,
    notify: false,
    email: "",
    voucherCode: " squidlab50 ",
  });
  assert.equal(body.voucher_code, "squidlab50");
  const blank = submissionBody({
    token: "ok",
    file,
    account: null,
    notify: false,
    email: "",
    voucherCode: "   ",
  });
  assert.equal("voucher_code" in blank, false);
});

test("a logged-in submission follows the checkbox and omits the address", () => {
  const account = { email: "rin@example.com" };
  const on = submissionBody({
    token: "ok",
    file,
    account,
    notify: true,
    email: "other@example.com",
  });
  assert.equal(on.notify, true);
  assert.equal(on.email, null);
  const off = submissionBody({
    token: "ok",
    file,
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
    account: null,
    notify: true,
    email: "player@example.com",
  });
  assert.equal(on.notify, true);
  assert.equal(on.email, "player@example.com");
  const off = submissionBody({
    token: "ok",
    file,
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

test("the submit button waits only for a video", () => {
  assert.equal(submitReady({ file, reviewCode: "" }), true);
  assert.equal(submitReady({ file: null, reviewCode: "" }), false);
  assert.equal(submitReady({ file: null, reviewCode: "ABCD-1234" }), false);
  assert.equal(submitReady({ file: null, reviewCode: "ABCD-1234-EFGH-5678" }), false);
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

test("voucher preview success hides Apply until the code changes", () => {
  assert.equal(showVoucherApplyButton(null), true);
  assert.equal(showVoucherApplyButton({ accepted: false }), true);
  assert.equal(showVoucherApplyButton({ accepted: true }), false);
  const reset = voucherStateAfterCodeChange();
  assert.equal(reset.voucherPreview, null);
  assert.equal(reset.voucherError, "");
  assert.equal(showVoucherApplyButton(reset.voucherPreview), true);
});

test("voucher preview success uses only the concise confirmation keys", () => {
  assert.deepEqual(voucherPreviewSuccessKeys(), [
    "submit.voucher_confirmed",
    "submit.voucher_no_payment",
  ]);
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
