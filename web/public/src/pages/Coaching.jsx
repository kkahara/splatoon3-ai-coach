import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import { loadAccount, loadHistory } from "../api.js";

export function CoachingPage() {
  const [account, setAccount] = useState(null);
  const [items, setItems] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stopped = false;
    loadAccount()
      .then(async (next) => {
        if (stopped) {
          return;
        }
        setAccount(next);
        if (!next) {
          setError("Log in to see your coaching.");
          setItems([]);
          return;
        }
        const history = await loadHistory();
        if (!stopped) {
          setItems(history);
        }
      })
      .catch((exc) => {
        if (!stopped) {
          setError(exc.message);
          setItems([]);
        }
      });
    return () => {
      stopped = true;
    };
  }, []);

  return (
    <main className="sheet">
      <AccountBar account={account} onChange={setAccount} />
      <h1>Past coaching</h1>
      {error ? <p className="error">{error}</p> : null}
      {items === null ? <p>Loading…</p> : null}
      {items?.length === 0 && !error ? <p>You have no coaching yet.</p> : null}
      <ul className="history">
        {(items || []).map((item) => (
          <li key={item.id}>
            <Link to={`/coaching/${item.id}`}>
              <strong>{item.display_name || "Match"}</strong>
              <span className="muted">
                {formatWhen(item.created_at)} · {label(item.status)}
              </span>
            </Link>
          </li>
        ))}
      </ul>
      <p className="again">
        <Link to="/">Submit a video</Link>
      </p>
    </main>
  );
}

function formatWhen(stamp) {
  const date = new Date(stamp);
  if (Number.isNaN(date.getTime())) {
    return stamp;
  }
  return date.toLocaleString();
}

function label(status) {
  if (status === "complete") {
    return "Ready";
  }
  if (status === "failed") {
    return "Failed";
  }
  if (status === "expired") {
    return "Expired";
  }
  return "In progress";
}
