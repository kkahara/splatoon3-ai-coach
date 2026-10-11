import { Link, useNavigate } from "react-router-dom";
import { logoutAccount } from "./api.js";
import { useT } from "./i18n/LocaleContext.jsx";

export function AccountBar({ account, onChange }) {
  const navigate = useNavigate();
  const t = useT();

  async function logout() {
    await logoutAccount();
    if (onChange) {
      onChange(null);
    }
    navigate("/");
  }

  return (
    <header className="top">
      <nav>
        {account ? (
          <>
            <Link to="/coaching">{t("account.past_coaching")}</Link>
            {account.role === "admin" ? <Link to="/admin">Admin</Link> : null}
            <button type="button" className="linkish" onClick={logout}>
              {t("account.log_out")}
            </button>
          </>
        ) : (
          <Link to="/login">{t("common.log_in")}</Link>
        )}
      </nav>
    </header>
  );
}
