import { useEffect, useState } from "react";
import { AccountBar } from "../AccountBar.jsx";
import { loadAccount, loadConfig, sendContact } from "../api.js";
import { useT } from "../i18n/LocaleContext.jsx";
import {
  CONTACT_REASONS,
  contactBody,
  contactErrorText,
  contactProblem,
  showPaymentWarning,
  showReviewLink,
} from "./contactForm.js";

export function ContactPage() {
  const t = useT();
  const [account, setAccount] = useState(null);
  const [config, setConfig] = useState(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    loadAccount()
      .then((next) => {
        setAccount(next);
        if (!next) {
          return;
        }
        setName((current) => current || next.name || "");
        setEmail((current) => current || next.email || "");
      })
      .catch(() => setAccount(null));
    loadConfig()
      .then(setConfig)
      .catch((exc) => setError(exc.message));
  }, []);

  return (
    <main className="sheet">
      <AccountBar account={account} onChange={setAccount} />
      <h1>{t("contact.title")}</h1>
      <p>{t("contact.intro")}</p>
      <ContactForm
        config={config}
        name={name}
        email={email}
        setName={setName}
        setEmail={setEmail}
        loadError={error}
      />
    </main>
  );
}

function ContactForm({ config, name, email, setName, setEmail, loadError }) {
  const t = useT();
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState("");
  const [reference, setReference] = useState("");
  const [trap, setTrap] = useState("");
  const [turnstileToken, setTurnstileToken] = useState("");
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!config?.turnstile_site_key) {
      return undefined;
    }
    const script = document.createElement("script");
    script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js";
    script.async = true;
    script.onload = () => {
      const slot = document.getElementById("contact-turnstile");
      if (slot && window.turnstile) {
        window.turnstile.render(slot, {
          sitekey: config.turnstile_site_key,
          callback: (token) => setTurnstileToken(token),
        });
      }
    };
    document.body.appendChild(script);
    return () => script.remove();
  }, [config?.turnstile_site_key]);

  async function onSubmit(event) {
    event.preventDefault();
    const problem = contactProblem({
      reason,
      name,
      email,
      message,
      reference,
      token: turnstileToken,
      devMode: Boolean(config?.dev_mode),
    });
    if (problem) {
      setError(t(problem));
      return;
    }
    setBusy(true);
    setError("");
    try {
      await sendContact(
        contactBody({
          token: config?.dev_mode ? "dev" : turnstileToken,
          reason,
          name,
          email,
          message,
          reference,
          trap,
        }),
      );
      setSent(true);
    } catch (exc) {
      setError(contactErrorText(exc.message, t));
      setBusy(false);
    }
  }

  if (sent) {
    return <p>{t("contact.thanks")}</p>;
  }

  return (
    <form onSubmit={onSubmit}>
      <label>
        {t("contact.reason")}
        <select value={reason} onChange={(event) => setReason(event.target.value)} required>
          <option value="">{t("contact.choose_reason")}</option>
          {CONTACT_REASONS.map((item) => (
            <option key={item} value={item}>
              {t(`contact.reason.${item}`)}
            </option>
          ))}
        </select>
      </label>
      {showPaymentWarning(reason) ? (
        <p className="contact-warning">{t("contact.payment_warning")}</p>
      ) : null}
      <label>
        {t("contact.name")}
        <input value={name} onChange={(event) => setName(event.target.value)} required />
      </label>
      <label>
        {t("common.email")}
        <input
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          required
        />
      </label>
      <label>
        {t("contact.message")}
        <textarea
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          rows={6}
          required
        />
      </label>
      {showReviewLink(reason) ? (
        <label>
          {t("contact.review_link")}
          <input value={reference} onChange={(event) => setReference(event.target.value)} />
        </label>
      ) : null}
      <ContactTrap value={trap} onChange={setTrap} />
      {config?.turnstile_site_key ? <div id="contact-turnstile" /> : null}
      {error || loadError ? <p className="error">{error || loadError}</p> : null}
      <button type="submit" disabled={busy}>
        {busy ? t("common.sending") : t("contact.send")}
      </button>
    </form>
  );
}

function ContactTrap({ value, onChange }) {
  return (
    <div className="contact-trap" aria-hidden="true">
      <label>
        Leave blank
        <input
          tabIndex={-1}
          autoComplete="off"
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      </label>
    </div>
  );
}
