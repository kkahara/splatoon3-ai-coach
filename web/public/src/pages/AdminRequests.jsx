import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { loadAdminReference, loadAdminRequests } from "../api.js";
import { AdminError, AdminLoading, formatDate, listPayload } from "./AdminUi.jsx";
import { looksLikeReference } from "./supportReference.js";

const PAGE_SIZE = 25;

export function AdminRequestsPage() {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [cursors, setCursors] = useState([""]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [reference, setReference] = useState(null);
  const cursor = cursors.at(-1);

  useEffect(() => {
    let stopped = false;
    setReference(null);
    if (looksLikeReference(search)) {
      loadAdminReference(search.replace(/\s+/g, ""))
        .then((payload) => { if (!stopped) setReference(payload); })
        .catch((exc) => { if (!stopped) setReference({ missing: exc.message }); });
    }
    return () => {
      stopped = true;
    };
  }, [search]);

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
          Search (id, email or support reference)
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
      {reference ? <ReferenceMatch match={reference} /> : null}
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

function ReferenceMatch({ match }) {
  if (match.missing) {
    return <p className="muted">Support reference: {match.missing}</p>;
  }
  const id = match.submission_id;
  const reviewCode = match.source === "nintendo_review_code";
  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead>
          <tr><th>Support reference</th><th>Request</th><th>Source</th><th>Status</th><th>Payment</th><th>Created</th></tr>
        </thead>
        <tbody>
          <tr>
            <td><code>{match.public_reference}</code></td>
            <td><code>{id}</code> · <Link to={`/admin/requests/${encodeURIComponent(id)}`}>analysis runs</Link></td>
            <td>{reviewCode ? `Review code (${match.acquisition_state || "unknown"})` : "Video upload"}</td>
            <td>{match.status || "—"}</td>
            <td>{match.payment_state}{match.voucher_state ? ` · voucher ${match.voucher_state}` : ""}</td>
            <td>{formatDate(match.created_at)}</td>
          </tr>
        </tbody>
      </table>
    </div>
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
