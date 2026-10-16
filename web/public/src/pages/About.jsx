import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import { loadAccount } from "../api.js";
import { useT } from "../i18n/LocaleContext.jsx";

const SOURCE_URL = "https://github.com/kkahara/splatoon3-ai-coach";

export function AboutPage() {
  const t = useT();
  const [account, setAccount] = useState(null);

  useEffect(() => {
    loadAccount()
      .then(setAccount)
      .catch(() => setAccount(null));
  }, []);

  return (
    <main className="sheet">
      <AccountBar account={account} onChange={setAccount} />
      <h1>{t("about.title")}</h1>
      <p>{t("about.p1")}</p>
      <p>{t("about.p2")}</p>
      <p>
        {t("about.source_before")}
        <a href={SOURCE_URL} rel="noreferrer">GitHub</a>
        {t("about.source_after")}
      </p>
      <p>
        <Link to="/contact">{t("about.contact")}</Link>
      </p>
    </main>
  );
}
