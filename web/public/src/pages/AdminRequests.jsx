import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { loadAdminRequests } from "../api.js";
import { AdminError, AdminLoading, formatDate, listPayload } from "./AdminUi.jsx";

const PAGE_SIZE = 25;

export function AdminRequestsPage() {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [cursors, setCursors] = useState([""]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const cursor = cursors.at(-1);

  useEffect(() => {
    let stopped = false;
    setResult(null);
    setError("");
    loadAdminRequests({ q: search, status, cursor, limit: PAGE_SIZE })
      .then((payload) => {
        if (!stopped) {
          setResult(listPayload(payload, ["requests", "items", "results"]));
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
  }, [search, status, cursor]);

  function submit(event) {
    event.preventDefault();
    setCursors([""]);
    setSearch(query.trim());
  }

  function changeStatus(event) {
    setStatus(event.target.value);
    setCursors([""]);
  }

  return (
    <section>
      <h1>Requests</h1>
      <form className="admin-filters" onSubmit={submit}>
        <label>
          Search
          <input value={query} onChange={(event) => setQuery(event.target.value)} />
        </label>
        <label>
          Status
          <select value={status} onChange={changeStatus}>
            <option value="">All statuses</option>
            <option value="submitted">Submitted</option>
            <option value="validating">Validating</option>
            <option value="queued">Queued</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="interrupted">Interrupted</option>
          </select>
        </label>
        <button type="submit">Search</button>
      </form>
      <AdminError message={error} />
      {!result && !error ? <AdminLoading /> : null}
      {result ? <RequestTable items={result.items} /> : null}
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

function RequestTable({ items }) {
  if (!items.length) {
    return <p className="muted">No requests found.</p>;
  }
  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead>
          <tr><th>Request</th><th>Status</th><th>User</th><th>Created</th></tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const id = item.id ?? item.request_id ?? item.submission_id;
            return (
              <tr key={id}>
                <td><Link to={`/admin/requests/${encodeURIComponent(id)}`}>{id}</Link></td>
                <td><span className={`admin-status status-${item.status || "unknown"}`}>{item.status || "Unknown"}</span></td>
                <td>{item.user_email ?? item.email ?? item.user?.email ?? "—"}</td>
                <td>{formatDate(item.created_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
