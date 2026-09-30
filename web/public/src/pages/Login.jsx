import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import { loginAccount } from "../api.js";
import { useT } from "../i18n/LocaleContext.jsx";

export function LoginPage() {
  const navigate = useNavigate();
  const t = useT();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await loginAccount({ email, password });
      navigate("/");
    } catch (exc) {
      setError(exc.message);
      setBusy(false);
    }
  }

  return (
    <main className="sheet">
      <AccountBar account={null} />
      <h1>{t("login.title")}</h1>
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
        <label>
          {t("common.password")}
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </label>
        {error ? <p className="error">{error}</p> : null}
        <button type="submit" disabled={busy}>
          {busy ? t("login.logging_in") : t("common.log_in")}
        </button>
      </form>
      <p className="again">
        <Link to="/forgot">{t("login.reset")}</Link>
        {" · "}
        <Link to="/register">{t("login.create")}</Link>
      </p>
    </main>
  );
}
