import { useState } from "react";
import { Link } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import { forgotPassword } from "../api.js";
import { useT } from "../i18n/LocaleContext.jsx";

export function ForgotPage() {
  const t = useT();
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await forgotPassword(email);
      setSent(true);
    } catch (exc) {
      setError(exc.message);
      setBusy(false);
    }
  }

  return (
    <main className="sheet">
      <AccountBar account={null} />
      <h1>{t("forgot.title")}</h1>
      {sent ? (
        <p>{t("forgot.sent")}</p>
      ) : (
        <form onSubmit={onSubmit}>
          <label>
            {t("common.email")}
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </label>
          {error ? <p className="error">{error}</p> : null}
          <button type="submit" disabled={busy}>
            {busy ? t("common.sending") : t("forgot.submit")}
          </button>
        </form>
      )}
      <p className="again">
        <Link to="/login">{t("common.log_in")}</Link>
      </p>
    </main>
  );
}
