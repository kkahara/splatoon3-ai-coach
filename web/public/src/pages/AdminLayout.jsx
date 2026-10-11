import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import { loadAccount } from "../api.js";

export function AdminLayout() {
  const [account, setAccount] = useState(undefined);
  const [error, setError] = useState("");

  useEffect(() => {
    let stopped = false;
    loadAccount()
      .then((next) => {
        if (!stopped) {
          setAccount(next);
        }
      })
      .catch((exc) => {
        if (!stopped) {
          setError(exc.message);
          setAccount(null);
        }
      });
    return () => {
      stopped = true;
    };
  }, []);

  return (
    <main className="sheet admin-sheet" lang="en">
      <AccountBar account={account} onChange={setAccount} />
      {account === undefined ? <p>Loading admin…</p> : null}
      {error ? <p className="error">{error}</p> : null}
      {account !== undefined && account?.role !== "admin" ? (
        <section>
          <h1>Admin</h1>
          <p className="error">You do not have access to this area.</p>
        </section>
      ) : null}
      {account?.role === "admin" ? (
        <>
          <nav className="admin-nav" aria-label="Admin">
            <NavLink to="/admin" end>Overview</NavLink>
            <NavLink to="/admin/price">Price</NavLink>
            <NavLink to="/admin/pipeline">Pipeline</NavLink>
            <NavLink to="/admin/coaching">Coaching</NavLink>
            <NavLink to="/admin/versions">Versions</NavLink>
            <NavLink to="/admin/llm">LLM</NavLink>
            <NavLink to="/admin/product">Product</NavLink>
            <NavLink to="/admin/failures">Failures</NavLink>
            <NavLink to="/admin/requests">Requests</NavLink>
            <NavLink to="/admin/users">Users</NavLink>
          </nav>
          <Outlet />
        </>
      ) : null}
    </main>
  );
}
