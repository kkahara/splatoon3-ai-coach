import { useState } from "react";
import { Link } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import { registerAccount } from "../api.js";
import { useT } from "../i18n/LocaleContext.jsx";

export function RegisterPage() {
  const t = useT();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await registerAccount({ name, email, password });
      setSent(true);
    } catch (exc) {
      setError(exc.message);
      setBusy(false);
    }
  }

  return (
    <main className="sheet">
      <AccountBar account={null} />
      <h1>{t("register.title")}</h1>
      {sent ? (
        <p>{t("register.check_email")}</p>
      ) : (
        <form onSubmit={onSubmit}>
          <label>
            {t("register.name")}
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
            {t("common.password")}
            <input
              type="password"
              value={password}
              minLength={8}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {error ? <p className="error">{error}</p> : null}
          <button type="submit" disabled={busy}>
            {busy ? t("register.creating") : t("register.submit")}
          </button>
        </form>
      )}
      <p className="again">
        <Link to="/login">{t("common.log_in")}</Link>
      </p>
    </main>
  );
}
