import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { createReviewCodeSubmission, openCheckout } from "../api.js";
import { useT } from "../i18n/LocaleContext.jsx";
import { normalizedReviewCode } from "./recordingForm.js";

export function ReviewCodeForm({ account, config, turnstileToken }) {
  const t = useT();
  const navigate = useNavigate();
  const [code, setCode] = useState("");
  const [player, setPlayer] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event) {
    event.preventDefault();
    const normalized = normalizedReviewCode(code);
    if (!normalized) { setError(t("review_code.invalid")); return; }
    setBusy(true);
    setError("");
    let created;
    try {
      created = await createReviewCodeSubmission({
        turnstile_token: config?.dev_mode ? "dev" : turnstileToken,
        review_code: normalized, target_player_name: player.trim(),
        ...(account ? {} : { email }),
      });
    } catch (exc) {
      setError(exc.message); setBusy(false); return;
    }
    if (!created.notice_pending && created.price_cents === config?.price_cents) {
      try {
        const checkout = await openCheckout(created.token);
        if (checkout.url) { window.location.assign(checkout.url); return; }
      } catch { /* The saved status page offers a safe checkout retry. */ }
    }
    navigate(created.review_path);
  }

  return (
    <section className="upload-card">
      <h2>{t("review_code.heading")}</h2>
      <p>{t("review_code.intro")}</p>
      <p>{t("review_code.manual")}</p>
      <p>{t("review_code.notify")}</p>
      <form onSubmit={submit}>
        <label>{t("review_code.code")}<input value={code} maxLength={100}
          onChange={(event) => setCode(event.target.value)} required autoComplete="off" /></label>
        <p className="note">{t("review_code.syntax")}</p>
        <label>{t("review_code.player")}<input value={player} maxLength={100}
          onChange={(event) => setPlayer(event.target.value)} required /></label>
        <p className="note">{t("review_code.player_hint")}</p>
        {!account ? <label>{t("common.email")}<input type="email" value={email}
          onChange={(event) => setEmail(event.target.value)} required /></label> : null}
        <p>{t("review_code.verified_email")}</p>
        <p>{t("review_code.price", { amount: ((config?.price_cents || 0) / 100).toFixed(2) })}</p>
        <p className="note">{t("submit.terms")}</p>
        {error ? <p className="error" role="alert">{error}</p> : null}
        <button disabled={busy || !player.trim() || (!turnstileToken && !config?.dev_mode)}>
          {busy ? t("submit.submitting") : t("review_code.continue")}
        </button>
      </form>
    </section>
  );
}
