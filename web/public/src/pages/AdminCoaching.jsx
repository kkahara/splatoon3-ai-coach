import { useEffect, useState } from "react";
import { loadAdminCoaching } from "../api.js";
import { CountBars, CountTable, NarrowingFunnel } from "./AdminCharts.jsx";
import { AdminError, AdminLoading, displayLabel } from "./AdminUi.jsx";

const WINDOWS = [
  ["24h", "24 hours"],
  ["7d", "7 days"],
  ["30d", "30 days"],
];

export function AdminCoachingPage() {
  const [windowName, setWindowName] = useState("24h");
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stopped = false;
    setReport(null);
    setError("");
    loadAdminCoaching({ window: windowName })
      .then((next) => {
        if (!stopped) {
          setReport(next);
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
  }, [windowName]);

  return (
    <section>
      <h1>Coaching</h1>
      <WindowSelect value={windowName} onChange={setWindowName} />
      <AdminError message={error} />
      {!report && !error ? <AdminLoading /> : null}
      {report ? <CoachingReport report={report} /> : null}
    </section>
  );
}

function CoachingReport({ report }) {
  const selection = countRows(report.selection);
  return (
    <>
      <p>
        Latest completed run for each submission. Selection narrows from candidates.
        LLM disposition, validation, and public disposition are separate counts.
      </p>
      <p>
        Runs {report.runs}. Missing snapshot {report.missing_snapshot}. Review missing{" "}
        {report.review_missing}.
      </p>
      <SnapshotMix versions={report.snapshot_versions} />
      <h2>Selection</h2>
      <NarrowingFunnel rows={selection} />
      <CountTable rows={selection} />
      <div className="admin-split">
        <CountPanel title="LLM disposition" counts={report.llm_disposition} />
        <CountPanel title="Validation" counts={report.validation} />
        <CountPanel title="Public disposition" counts={report.public} />
      </div>
      <p>Unselected requested residual: {report.unselected_requested}.</p>
      <Breakout title="Candidate type" rows={report.by_candidate_type} />
      <Breakout title="Active factor" rows={report.by_factor} />
    </>
  );
}

function CountPanel({ title, counts }) {
  const rows = countRows(counts);
  return (
    <section>
      <h2>{title}</h2>
      <CountBars rows={rows} label={title} />
      <CountTable rows={rows} />
    </section>
  );
}

function Breakout({ title, rows }) {
  const items = rows || [];
  return (
    <section>
      <h2>{title}</h2>
      <table>
        <thead>
          <tr>
            <th>Key</th>
            <th>Recommendation</th>
            <th>No recommendation</th>
            <th>Blocked</th>
            <th>Not reviewed</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row) => (
            <tr key={row.key}>
              <td>{row.key}</td>
              <td>{row.recommendation}</td>
              <td>{row.no_recommendation}</td>
              <td>{row.blocked}</td>
              <td>{row.not_reviewed}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function SnapshotMix({ versions }) {
  const items = versions || [];
  if (!items.length) {
    return null;
  }
  return (
    <p>
      Snapshot schema{" "}
      {items.map((item) => `${item.snapshot_schema_version}: ${item.runs}`).join(", ")}.
    </p>
  );
}

function countRows(counts) {
  return Object.entries(counts || {}).map(([label, count]) => ({
    label: displayLabel(label),
    count,
  }));
}

export function WindowSelect({ value, onChange }) {
  return (
    <form className="admin-filters">
      <label>
        Window
        <select value={value} onChange={(event) => onChange(event.target.value)}>
          {WINDOWS.map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>
      </label>
    </form>
  );
}
