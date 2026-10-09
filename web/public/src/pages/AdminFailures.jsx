import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { loadAdminFailureSummary, loadAdminFailures } from "../api.js";
import {
  AdminError,
  AdminLoading,
  displayLabel,
  formatDate,
  listPayload,
} from "./AdminUi.jsx";

const PAGE_SIZE = 25;

export function AdminFailuresPage() {
  const [windowName, setWindowName] = useState("7d");
  const [status, setStatus] = useState("");
  const [stage, setStage] = useState("");
  const [cursors, setCursors] = useState([""]);
  const [page, setPage] = useState(null);
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState("");
  const cursor = cursors.at(-1);

  useEffect(() => {
    let stopped = false;
    setError("");
    const filters = { window: windowName, status, stage, cursor, limit: PAGE_SIZE };
    Promise.all([loadAdminFailures(filters), loadAdminFailureSummary(filters)])
      .then(([failures, groups]) => {
        if (!stopped) {
          setPage(listPayload(failures, ["items"]));
          setSummary(groups);
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
  }, [windowName, status, stage, cursor]);

  function changeWindow(event) {
    setWindowName(event.target.value);
    setCursors([""]);
  }

  return (
    <section>
      <h1>Failures</h1>
      <form className="admin-filters">
        <label>
          Window
          <select value={windowName} onChange={changeWindow}>
            <option value="24h">24 hours</option>
            <option value="7d">7 days</option>
            <option value="30d">30 days</option>
          </select>
        </label>
        <label>
          Status
          <select value={status} onChange={(event) => { setStatus(event.target.value); setCursors([""]); }}>
            <option value="">Failed and interrupted</option>
            <option value="failed">Failed</option>
            <option value="interrupted">Interrupted</option>
          </select>
        </label>
        <label>
          Stage
          <select value={stage} onChange={(event) => { setStage(event.target.value); setCursors([""]); }}>
            <option value="">All stages</option>
            <option value="analyze">Analyze</option>
            <option value="coach_inputs">Coach inputs</option>
            <option value="coach_prototype">Coach prototype</option>
            <option value="frames">Frames</option>
          </select>
        </label>
      </form>
      <AdminError message={error} />
      {!page && !error ? <AdminLoading /> : null}
      {summary ? <Summary groups={summary.groups || []} /> : null}
      {page ? (
        <FailureTable
          items={page.items}
          onNext={page.nextCursor ? () => setCursors((current) => [...current, page.nextCursor]) : null}
        />
      ) : null}
    </section>
  );
}

function Summary({ groups }) {
  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <caption>Grouped failure counts</caption>
        <thead>
          <tr><th>Error</th><th>Diagnostic</th><th>Stage</th><th>Count</th></tr>
        </thead>
        <tbody>
          {groups.map((group) => (
            <tr key={`${group.error_code}-${group.diagnostic}-${group.failed_stage}`}>
              <td>{displayLabel(group.error_code)}</td>
              <td>{displayLabel(group.diagnostic)}</td>
              <td>{displayLabel(group.failed_stage)}</td>
              <td>{group.n}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FailureTable({ items, onNext }) {
  return (
    <>
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr><th>When</th><th>Request</th><th>Status</th><th>Stage</th><th>Diagnostic</th></tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.run_id}>
                <td>{formatDate(item.created_at)}</td>
                <td>
                  <Link to={`/admin/requests/${encodeURIComponent(item.submission_id)}`}>
                    {item.submission_id}
                  </Link>
                </td>
                <td>{displayLabel(item.status)}</td>
                <td>{displayLabel(item.failed_stage)}</td>
                <td>{displayLabel(item.diagnostic)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {onNext ? <button type="button" onClick={onNext}>Next</button> : null}
    </>
  );
}
