import { Link, useLocation, useNavigate } from "react-router-dom";
import { logoutAccount } from "./api.js";
import { useT } from "./i18n/LocaleContext.jsx";

export function AccountBar({ account, onChange }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
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
        {pathname === "/" ? null : (
          <Link to="/">{t("coaching.submit_video")}</Link>
        )}
        <Link to="/about">{t("nav.about")}</Link>
        {account ? (
          <>
            <Link to="/coaching">{t("account.past_coaching")}</Link>
            <Link to="/contact">{t("nav.contact")}</Link>
            {account.role === "admin" ? (
              <Link to="/admin" className="nav-quiet">Admin</Link>
            ) : null}
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
