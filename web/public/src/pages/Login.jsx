import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import { loginAccount } from "../api.js";

export function LoginPage() {
  const navigate = useNavigate();
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
      <h1>Log in</h1>
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
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </label>
        {error ? <p className="error">{error}</p> : null}
        <button type="submit" disabled={busy}>
          {busy ? "Logging in…" : "Log in"}
        </button>
      </form>
      <p className="again">
        <Link to="/forgot">Reset password</Link>
        {" · "}
        <Link to="/register">Create an account</Link>
      </p>
    </main>
  );
}
