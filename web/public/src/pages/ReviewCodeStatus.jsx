import { useState } from "react";
import { resendReviewContact } from "../api.js";
import { useT } from "../i18n/LocaleContext.jsx";
import { recordingQueued } from "./recordingForm.js";

export function ReviewCodeStatus({ view, token }) {
  const t = useT();
  const [message, setMessage] = useState("");
  if (!view?.acquisition) return null;
  const acquisition = view.acquisition;
  async function resend() {
    try { await resendReviewContact(token); setMessage(t("review_code.email_sent")); }
    catch (exc) { setMessage(exc.message); }
  }
  return (
    <section aria-live="polite">
      {recordingQueued(view) ? <>
        <h2>{t("review_code.received")}</h2>
        <p>{t("review_code.queue_confirmation")}</p>
      </> : null}
      {view.payment_state !== "paid" ? <p>{t("review_code.price", { amount: (acquisition.price_cents / 100).toFixed(2) })}</p> : null}
      <p>{t("review_code.job_reference")}: <code>{acquisition.job_reference}</code></p>
      <p>{t(`review_code.state.${acquisition.state}`)}</p>
      {acquisition.target_player_name ? <p>{t("review_code.player")}: {acquisition.target_player_name}</p> : null}
      <p>{t(acquisition.target_confirmed ? "review_code.target_confirmed" : "review_code.target_pending")}</p>
      {!acquisition.contact_verified && view.status !== "expired" ? <>
        <p>{t("review_code.verify_wait")}</p>
        {token ? <button onClick={resend}>{t("review_code.resend")}</button> : null}
      </> : null}
      {message ? <p role="status">{message}</p> : null}
      {acquisition.customer_message ? <p>{acquisition.customer_message}</p> : null}
    </section>
  );
}
