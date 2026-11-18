import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import { confirmNotices } from "../api.js";
import { useT } from "../i18n/LocaleContext.jsx";
import { safeReviewPath } from "./confirmNotices.js";

export { safeReviewPath };

export function ConfirmNoticesPage() {
  const { token } = useParams();
  const t = useT();
  const [state, setState] = useState("confirming");
  const [reviewPath, setReviewPath] = useState(null);

  useEffect(() => {
    let stopped = false;
    confirmNotices(token)
      .then((payload) => {
        if (!stopped) {
          setReviewPath(safeReviewPath(payload?.review_path));
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
      {state === "done" ? (
        <div role="status">
          <p><strong>{t("confirm_notices.done")}</strong></p>
          <p>{t("confirm_notices.done_return")}</p>
          {reviewPath ? (
            <p>
              <Link to={reviewPath}>{t("confirm_notices.done_link")}</Link>
            </p>
          ) : (
            <p>{t("confirm_notices.done_missing")}</p>
          )}
        </div>
      ) : null}
      {state === "confirming" ? <p>{t("confirm_notices.confirming")}</p> : null}
    </main>
  );
}
