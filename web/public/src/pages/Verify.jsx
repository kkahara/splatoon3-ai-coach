import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import { verifyAccount } from "../api.js";

export function VerifyPage() {
  const { token } = useParams();
  const navigate = useNavigate();
  const [error, setError] = useState("");

  useEffect(() => {
    let stopped = false;
    verifyAccount(token)
      .then(() => {
        if (!stopped) {
          navigate("/");
        }
      })
      .catch((exc) => {
        if (!stopped) {
          setError(exc.message);
        }
      });
    return () => {
      stopped = true;
    };
  }, [token, navigate]);

  return (
    <main className="sheet">
      <AccountBar account={null} />
      <h1>Confirm your email</h1>
      {error ? <p className="error">{error}</p> : <p>Confirming your email…</p>}
      {error ? (
        <p className="again">
          <Link to="/login">Log in</Link>
        </p>
      ) : null}
    </main>
  );
}
