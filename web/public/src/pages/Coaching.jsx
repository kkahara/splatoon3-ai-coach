import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import { loadAccount, loadHistory } from "../api.js";
import { useLocale, useT } from "../i18n/LocaleContext.jsx";

export function CoachingPage() {
  const t = useT();
  const { locale } = useLocale();
  const [account, setAccount] = useState(null);
  const [items, setItems] = useState(null);
  const [error, setError] = useState("");
  const [needsLogin, setNeedsLogin] = useState(false);

  useEffect(() => {
    let stopped = false;
    loadAccount()
      .then(async (next) => {
        if (stopped) {
          return;
        }
        setAccount(next);
        if (!next) {
          setNeedsLogin(true);
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

  const message = needsLogin ? t("coaching.login_needed") : error;

  return (
    <main className="sheet">
      <AccountBar account={account} onChange={setAccount} />
      <h1>{t("coaching.title")}</h1>
      {message ? <p className="error">{message}</p> : null}
      {items === null ? <p>{t("coaching.loading")}</p> : null}
      {items?.length === 0 && !message ? <p>{t("coaching.empty")}</p> : null}
      <ul className="history">
        {(items || []).map((item) => (
          <li key={item.id}>
            <Link to={`/coaching/${item.id}`}>
              <strong>{item.display_name || t("coaching.match")}</strong>
              <span className="muted">
                {formatWhen(item.created_at, locale)} · {t(statusKey(item.status))}
              </span>
            </Link>
          </li>
        ))}
      </ul>
      <p className="again">
        <Link to="/">{t("coaching.submit_video")}</Link>
      </p>
    </main>
  );
}

function formatWhen(stamp, locale) {
  const date = new Date(stamp);
  if (Number.isNaN(date.getTime())) {
    return stamp;
  }
  return locale === "ja" ? date.toLocaleString("ja-JP") : date.toLocaleString();
}

function statusKey(status) {
  if (status === "complete") {
    return "coaching.status.ready";
  }
  if (status === "failed") {
    return "coaching.status.failed";
  }
  if (status === "expired") {
    return "coaching.status.expired";
  }
  return "coaching.status.in_progress";
}
