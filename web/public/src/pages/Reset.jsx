import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import { resetPassword } from "../api.js";
import { useT } from "../i18n/LocaleContext.jsx";

export function ResetPage() {
  const { token } = useParams();
  const navigate = useNavigate();
  const t = useT();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await resetPassword({ token, password });
      navigate("/");
    } catch (exc) {
      setError(exc.message);
      setBusy(false);
    }
  }

  return (
    <main className="sheet">
      <AccountBar account={null} />
      <h1>{t("reset.title")}</h1>
      <form onSubmit={onSubmit}>
        <label>
          {t("reset.new_password")}
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
          {busy ? t("reset.saving") : t("reset.submit")}
        </button>
      </form>
      <p className="again">
        <Link to="/login">{t("common.log_in")}</Link>
      </p>
    </main>
  );
}
