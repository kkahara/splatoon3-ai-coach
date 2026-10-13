import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import { confirmNotices } from "../api.js";
import { useT } from "../i18n/LocaleContext.jsx";

export function ConfirmNoticesPage() {
  const { token } = useParams();
  const t = useT();
  const [state, setState] = useState("confirming");

  useEffect(() => {
    let stopped = false;
    confirmNotices(token)
      .then(() => {
        if (!stopped) {
          setState("done");
        }
      })
      .catch(() => {
        if (!stopped) {
          setState("invalid");
        }
      });
    return () => {
      stopped = true;
    };
  }, [token]);

  return (
    <main className="sheet">
      <AccountBar account={null} />
      <h1>{t("confirm_notices.title")}</h1>
      {state === "invalid" ? <p className="error">{t("confirm_notices.invalid")}</p> : null}
      {state === "done" ? <p>{t("confirm_notices.done")}</p> : null}
      {state === "confirming" ? <p>{t("confirm_notices.confirming")}</p> : null}
    </main>
  );
}
