import { useState } from "react";
import { Link } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import { registerAccount } from "../api.js";

export function RegisterPage() {
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
      <h1>Create an account</h1>
      {sent ? (
        <p>Check your email to finish registration.</p>
      ) : (
        <form onSubmit={onSubmit}>
          <label>
            Name
            <input value={name} onChange={(event) => setName(event.target.value)} required />
          </label>
          <label>
            Email address
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </label>
          <label>
            Password
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
            {busy ? "Creating…" : "Create account"}
          </button>
        </form>
      )}
      <p className="again">
        <Link to="/login">Log in</Link>
      </p>
    </main>
  );
}
