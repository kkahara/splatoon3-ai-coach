import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import { resetPassword } from "../api.js";

export function ResetPage() {
  const { token } = useParams();
  const navigate = useNavigate();
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
      <h1>Choose a new password</h1>
      <form onSubmit={onSubmit}>
        <label>
          New password
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
          {busy ? "Saving…" : "Save password"}
        </button>
      </form>
      <p className="again">
        <Link to="/login">Log in</Link>
      </p>
    </main>
  );
}
