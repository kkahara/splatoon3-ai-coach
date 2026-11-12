import { useRef, useState } from "react";
import { useT } from "../i18n/LocaleContext.jsx";
import {
  voucherAcceptedFor,
  voucherApplyAllowed,
  voucherErrorKey,
  voucherPreviewSuccessKeys,
  voucherResponseIsCurrent,
  voucherStateAfterCodeChange,
} from "./submitForm.js";

/**
 * Voucher code state for one form. `validate` is the form's own server route, so the
 * route decides the submission path; acceptance here is only a hint for the button.
 */
export function useVoucher(validate) {
  const [code, setCode] = useState("");
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [checking, setChecking] = useState(false);
  const latest = useRef({ sequence: 0, code: "" });

  function change(value) {
    latest.current = { sequence: latest.current.sequence + 1, code: value.trim() };
    const reset = voucherStateAfterCodeChange();
    setCode(value);
    setPreview(reset.voucherPreview);
    setError(null);
    setChecking(false);
  }

  async function apply() {
    const trimmed = code.trim();
    if (!voucherApplyAllowed({ code, checking, disabled: false })) {
      return;
    }
    const request = { sequence: latest.current.sequence + 1, code: trimmed };
    latest.current = request;
    setChecking(true);
    setError(null);
    try {
      const payload = await validate(trimmed);
      if (voucherResponseIsCurrent(request, latest.current)) {
        setPreview({ ...payload, code: trimmed });
      }
    } catch (exc) {
      if (voucherResponseIsCurrent(request, latest.current)) {
        setPreview(null);
        setError({ reason: exc.reason || "", message: exc.message });
      }
    } finally {
      if (voucherResponseIsCurrent(request, latest.current)) {
        setChecking(false);
      }
    }
  }

  return {
    code,
    preview,
    error,
    checking,
    accepted: voucherAcceptedFor(preview, code),
    change,
    apply,
  };
}

/** "Have a voucher code?" disclosure with Apply, shared by both submission forms. */
export function VoucherField({ voucher, disabled, inputId = "voucher-code" }) {
  const t = useT();
  const errorKey = voucherErrorKey(voucher.error?.reason);
  return (
    <details className="voucher">
      <summary>{t("submit.voucher_prompt")}</summary>
      <label htmlFor={inputId}>{t("submit.voucher_code")}</label>
      <div className="voucher-controls">
        <input
          id={inputId}
          value={voucher.code}
          autoComplete="off"
          spellCheck="false"
          onChange={(event) => voucher.change(event.target.value)}
        />
        {!voucher.accepted ? (
          <button
            type="button"
            onClick={voucher.apply}
            disabled={!voucherApplyAllowed({ ...voucher, disabled })}
          >
            {t("submit.voucher_apply")}
          </button>
        ) : null}
      </div>
      {voucher.error ? (
        <p className="error">{errorKey ? t(errorKey) : voucher.error.message}</p>
      ) : null}
      {voucher.accepted ? (
        <div className="voucher-ok" aria-live="polite">
          {voucherPreviewSuccessKeys().map((key) => (
            <p key={key}>{t(key)}</p>
          ))}
        </div>
      ) : null}
    </details>
  );
}
