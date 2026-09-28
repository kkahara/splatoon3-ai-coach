import { useState } from "react";
import { Link } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import { forgotPassword } from "../api.js";

export function ForgotPage() {
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
      <h1>Reset password</h1>
      {sent ? (
        <p>If an account exists for that email, a reset link is on its way.</p>
      ) : (
        <form onSubmit={onSubmit}>
          <label>
            Email address
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </label>
          {error ? <p className="error">{error}</p> : null}
          <button type="submit" disabled={busy}>
            {busy ? "Sending…" : "Send reset link"}
          </button>
        </form>
      )}
      <p className="again">
        <Link to="/login">Log in</Link>
      </p>
    </main>
  );
}
