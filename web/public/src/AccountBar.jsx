import { Link, useNavigate } from "react-router-dom";
import { logoutAccount } from "./api.js";

export function AccountBar({ account, onChange }) {
  const navigate = useNavigate();

  async function logout() {
    await logoutAccount();
    if (onChange) {
      onChange(null);
    }
    navigate("/");
  }

  return (
    <header className="top">
      <p className="brand">Splatoon 3 AI Coach</p>
      <nav>
        {account ? (
          <>
            <Link to="/coaching">Past coaching</Link>
            <button type="button" className="linkish" onClick={logout}>
              Log out
            </button>
          </>
        ) : (
          <Link to="/login">Log in</Link>
        )}
      </nav>
    </header>
  );
}
