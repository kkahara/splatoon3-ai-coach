import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import { loadAccount, loadHistory, saveNoticePreference } from "../api.js";
import { useLocale, useT } from "../i18n/LocaleContext.jsx";
import { historyCard } from "./historyCard.js";

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

  async function onPreference(enabled) {
    setError("");
    try {
      const saved = await saveNoticePreference(enabled);
      setAccount((current) =>
        current ? { ...current, notify_by_email: saved.notify_by_email } : current,
      );
    } catch (exc) {
      setError(exc.message);
    }
  }

  return (
    <main className="sheet">
      <AccountBar account={account} onChange={setAccount} />
      <h1>{t("coaching.title")}</h1>
      {account ? (
        <label className="check">
          <input
            type="checkbox"
            checked={Boolean(account.notify_by_email)}
            onChange={(event) => onPreference(event.target.checked)}
          />
          {t("coaching.notice_default")}
        </label>
      ) : null}
      {message ? <p className="error">{message}</p> : null}
      {items === null ? <p>{t("coaching.loading")}</p> : null}
      {items?.length === 0 && !message ? <p>{t("coaching.empty")}</p> : null}
      <ul className="history">
        {(items || []).map((item) => {
          const card = historyCard(item, t, locale);
          return (
            <li key={item.id}>
              <Link to={card.href}>
                <span className="history-row">
                  <strong>{card.title}</strong>
                  <span className={item.status === "complete" ? "muted history-done" : "muted"}>
                    {card.status}
                  </span>
                </span>
                <span className="history-row">
                  <span className="muted">{card.meta}</span>
                  <span className="history-chevron" aria-hidden="true">
                    ›
                  </span>
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
      <p className="again">
        <Link to="/">{t("coaching.submit_video")}</Link>
      </p>
    </main>
  );
}
