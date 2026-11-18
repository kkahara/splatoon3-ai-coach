import { useEffect, useState } from "react";
import { changeReviewContact, resendReviewContact } from "../api.js";
import { useT } from "../i18n/LocaleContext.jsx";
import {
  confirmMailActionMessageKey,
  confirmMailDispatched,
  contactVerifyLeadKey,
} from "./contactConfirm.js";
import { cooldownMinutes, cooldownUntil } from "./contactCooldown.js";
import { recordingAuthorized, recordingQueued } from "./recordingForm.js";
import {
  acquisitionStateKey,
  awaitingPayment,
  reviewExpired,
  voucherPending,
} from "./reviewPayment.js";

export function ReviewCodeStatus({ view, token }) {
  const t = useT();
  if (!view?.acquisition) return null;
  const acquisition = view.acquisition;
  const pendingVoucher = voucherPending(view);
  return (
    <section aria-live="polite">
      {recordingQueued(view) ? <>
        <h2>{t("review_code.received")}</h2>
        <p>{t("review_code.queue_confirmation")}</p>
      </> : null}
      {!recordingAuthorized(view)
        && awaitingPayment(view)
        && !pendingVoucher
        && !reviewExpired(view) ? (
        <p>{t("review_code.price", { amount: (acquisition.price_cents / 100).toFixed(2) })}</p>
      ) : null}
      <p>{t(acquisitionStateKey(view))}</p>
      {!acquisition.contact_verified && !reviewExpired(view) ? (
        <ContactPending view={view} token={token} pendingVoucher={pendingVoucher} />
      ) : null}
      {acquisition.customer_message ? <p>{acquisition.customer_message}</p> : null}
    </section>
  );
}

function ContactPending({ view, token, pendingVoucher }) {
  const t = useT();
  const acquisition = view.acquisition;
  const [until, setUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [message, setMessage] = useState("");
  const [editing, setEditing] = useState(false);
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setUntil(cooldownUntil(view.acquisition.resend_retry_after_seconds));
    setNow(Date.now());
  }, [view]);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(timer);
  }, []);

  const minutes = cooldownMinutes(until, now);
  const dispatched = confirmMailDispatched(acquisition);
  const leadKey = contactVerifyLeadKey({
    pendingVoucher,
    dispatched,
    coolingDown: minutes > 0,
  });

  async function resend() {
    setBusy(true);
    try {
      const result = await resendReviewContact(token);
      if (result.cooldown_seconds) {
        setUntil(cooldownUntil(result.cooldown_seconds));
        setMessage(t("review_code.verify_cooldown"));
      } else {
        const key = confirmMailActionMessageKey(result.confirm_mail_outcome || "sent");
        setMessage(key ? t(key) : t("review_code.email_sent"));
        if ((result.confirm_mail_outcome || "sent") === "sent") {
          setUntil(0);
        }
      }
    } catch (exc) {
      setMessage(exc.message);
    } finally {
      setBusy(false);
      setNow(Date.now());
    }
  }

  async function save(event) {
    event.preventDefault();
    setBusy(true);
    try {
      const result = await changeReviewContact(token, email);
      setEditing(false);
      setEmail("");
      setUntil(cooldownUntil(result.retry_after_seconds));
      const key = confirmMailActionMessageKey(result.confirm_mail_outcome, { changed: true })
        || (result.retry_after_seconds ? "review_code.email_changed_later" : "review_code.email_changed");
      setMessage(t(key));
    } catch (exc) {
      setMessage(exc.message);
    } finally {
      setBusy(false);
      setNow(Date.now());
    }
  }

  return (
    <>
      <p>{t(leadKey)}</p>
      {minutes > 0 ? <p className="note">{t("review_code.resend_cooldown", { minutes })}</p> : null}
      {dispatched && acquisition.contact_email_masked ? (
        <p className="note">
          {t("review_code.sent_to", { email: acquisition.contact_email_masked })}
          {token && !editing ? <>
            {" "}
            <button type="button" className="linkish" onClick={() => { setEditing(true); setMessage(""); }}>
              {t("review_code.change_email")}
            </button>
          </> : null}
        </p>
      ) : null}
      {!dispatched && acquisition.contact_email_masked && token && !editing ? (
        <p className="note">
          <button type="button" className="linkish" onClick={() => { setEditing(true); setMessage(""); }}>
            {t("review_code.change_email")}
          </button>
        </p>
      ) : null}
      {editing ? (
        <form onSubmit={save}>
          <label>
            {t("review_code.new_email")}
            <input type="email" value={email} maxLength={254} required onChange={(event) => setEmail(event.target.value)} />
          </label>
          <button type="submit" disabled={busy || !email.trim()}>{t("review_code.save_email")}</button>
          {" "}
          <button type="button" className="linkish" onClick={() => setEditing(false)}>{t("review_code.cancel_change")}</button>
        </form>
      ) : null}
      {token ? <button onClick={resend} disabled={busy || minutes > 0}>{t("review_code.resend")}</button> : null}
      {message ? <p role="status">{message}</p> : null}
    </>
  );
}
