import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { loadAdminUsers } from "../api.js";
import { AdminError, AdminLoading, formatDate, listPayload } from "./AdminUi.jsx";

const PAGE_SIZE = 25;

export function AdminUsersPage() {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [cursors, setCursors] = useState([""]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const cursor = cursors.at(-1);

  useEffect(() => {
    let stopped = false;
    setResult(null);
    setError("");
    loadAdminUsers({ q: search, cursor, limit: PAGE_SIZE })
      .then((payload) => {
        if (!stopped) {
          setResult(listPayload(payload, ["users", "items", "results"]));
        }
      })
      .catch((exc) => {
        if (!stopped) {
          setError(exc.message);
        }
      });
    return () => {
      stopped = true;
    };
  }, [search, cursor]);

  function submit(event) {
    event.preventDefault();
    setCursors([""]);
    setSearch(query.trim());
  }

  return (
    <section>
      <h1>Users</h1>
      <form className="admin-filters" onSubmit={submit}>
        <label>
          Search
          <input value={query} onChange={(event) => setQuery(event.target.value)} />
        </label>
        <button type="submit">Search</button>
      </form>
      <AdminError message={error} />
      {!result && !error ? <AdminLoading /> : null}
      {result ? <UserTable items={result.items} /> : null}
      {result ? (
        <div className="admin-pagination">
          <button
            type="button"
            disabled={cursors.length === 1}
            onClick={() => setCursors((current) => current.slice(0, -1))}
          >
            Previous
          </button>
          <button
            type="button"
            disabled={!result.nextCursor}
            onClick={() => setCursors((current) => [...current, result.nextCursor])}
          >
            Next
          </button>
        </div>
      ) : null}
    </section>
  );
}

function UserTable({ items }) {
  if (!items.length) {
    return <p className="muted">No users found.</p>;
  }
  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead>
          <tr><th>User</th><th>Name</th><th>Role</th><th>Created</th></tr>
        </thead>
        <tbody>
          {items.map((user) => {
            const id = user.id ?? user.user_id;
            return (
              <tr key={id}>
                <td><Link to={`/admin/users/${encodeURIComponent(id)}`}>{user.email ?? id}</Link></td>
                <td>{user.name ?? user.display_name ?? "—"}</td>
                <td>{user.role ?? "user"}</td>
                <td>{formatDate(user.created_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
