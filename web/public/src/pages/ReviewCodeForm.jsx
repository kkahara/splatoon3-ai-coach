import { useLayoutEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createReviewCodeSubmission, openCheckout, validateReviewVoucher } from "../api.js";
import { useT } from "../i18n/LocaleContext.jsx";
import { maskReviewCodeEdit, normalizedReviewCode } from "./recordingForm.js";
import { voucherErrorKey } from "./submitForm.js";
import { useVoucher, VoucherField } from "./VoucherField.jsx";

function ReviewCodeInput({ value, onChange }) {
  const t = useT();
  const input = useRef(null);
  const caret = useRef(null);

  useLayoutEffect(() => {
    if (caret.current !== null && input.current === document.activeElement) {
      input.current.setSelectionRange(caret.current, caret.current);
    }
    caret.current = null;
  }, [value]);

  function edit(event) {
    const next = maskReviewCodeEdit({
      previous: value,
      raw: event.target.value,
      caret: event.target.selectionStart ?? event.target.value.length,
      inputType: event.nativeEvent?.inputType,
    });
    caret.current = next.caret;
    onChange(next.value);
  }

  return (
    <input ref={input} value={value} onChange={edit} required autoComplete="off"
      autoCapitalize="characters" spellCheck="false"
      placeholder={t("review_code.placeholder")} />
  );
}

export function ReviewCodeForm({ account, config, turnstileToken }) {
  const t = useT();
  const navigate = useNavigate();
  const [code, setCode] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const voucher = useVoucher(validateReviewVoucher);

  async function submit(event) {
    event.preventDefault();
    const normalized = normalizedReviewCode(code);
    if (!normalized) { setError(t("review_code.invalid")); return; }
    setBusy(true);
    setError("");
    const voucherCode = voucher.code.trim();
    let created;
    try {
      created = await createReviewCodeSubmission({
        turnstile_token: config?.dev_mode ? "dev" : turnstileToken,
        review_code: normalized,
        ...(account ? {} : { email }),
        ...(voucherCode ? { voucher_code: voucherCode } : {}),
      });
    } catch (exc) {
      const key = voucherErrorKey(exc.reason);
      setError(key ? t(key) : exc.message); setBusy(false); return;
    }
    if (!voucherCode && !created.notice_pending && created.price_cents === config?.price_cents) {
      try {
        const checkout = await openCheckout(created.token);
        if (checkout.url) { window.location.assign(checkout.url); return; }
      } catch { /* The saved status page offers a safe checkout retry. */ }
    }
    navigate(created.review_path);
  }

  return (
    <section id="method-panel-review_code" role="tabpanel"
      aria-labelledby="method-tab-review_code" className="upload-card">
      <h2>{t("review_code.heading")}</h2>
      <p>{t("review_code.manual")}</p>
      <form onSubmit={submit}>
        <p>{t("review_code.own_match")}</p>
        <label>{t("review_code.code")}<ReviewCodeInput value={code} onChange={setCode} /></label>
        {!account ? <>
          <p>{t("review_code.email_notice")}</p>
          <label>{t("common.email")}<input type="email" autoComplete="email" value={email}
            onChange={(event) => setEmail(event.target.value)} required /></label>
        </> : null}
        <VoucherField voucher={voucher} disabled={busy} inputId="review-voucher-code" />
        {error ? <p className="error" role="alert">{error}</p> : null}
        <button disabled={busy || voucher.checking || !normalizedReviewCode(code)
          || (!turnstileToken && !config?.dev_mode)}>
          {busy ? t("submit.submitting")
            : t(voucher.accepted ? "submit.submit" : "review_code.continue")}
        </button>
      </form>
    </section>
  );
}
