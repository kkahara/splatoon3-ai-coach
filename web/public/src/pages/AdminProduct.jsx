import { useEffect, useState } from "react";
import { loadAdminProduct } from "../api.js";
import { formatRate, sampleNote } from "./adminFormat.js";
import { WindowSelect } from "./AdminCoaching.jsx";
import { AdminError, AdminLoading } from "./AdminUi.jsx";

export function AdminProductPage() {
  const [windowName, setWindowName] = useState("24h");
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stopped = false;
    setReport(null);
    setError("");
    loadAdminProduct({ window: windowName })
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
      <h1>Product</h1>
      <WindowSelect value={windowName} onChange={setWindowName} />
      <AdminError message={error} />
      {!report && !error ? <AdminLoading /> : null}
      {report ? <ProductReport report={report} /> : null}
    </section>
  );
}

function ProductReport({ report }) {
  const accounts = report.accounts || {};
  const submissions = report.submissions || {};
  const thresholds = submissions.users_with_completed_submissions || {};
  return (
    <>
      <dl className="admin-metrics">
        <Metric label="New accounts" value={accounts.new?.n} />
        <Metric label="Newly verified" value={accounts.newly_verified?.n} />
        <Metric label="Authenticated active" value={accounts.authenticated_active?.n} />
        <Metric label="Registered stock (all-time)" value={accounts.registered?.n} />
        <Metric label="Submitting" value={accounts.submitting?.n} />
        <Metric label="Guest submissions" value={submissions.guest} />
        <Metric label="Account submissions" value={submissions.account} />
        <Metric label="Processed video hours" value={submissions.processed_video_hours} />
        <Metric label="Requests" value={report.requests?.n} />
      </dl>
      <h2>Completed submissions</h2>
      <table>
        <thead>
          <tr>
            <th>Threshold</th>
            <th>Users</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>At least 1</td>
            <td>{thresholds.at_least_1}</td>
          </tr>
          <tr>
            <td>At least 2</td>
            <td>{thresholds.at_least_2}</td>
          </tr>
          <tr>
            <td>At least 5</td>
            <td>{thresholds.at_least_5}</td>
          </tr>
        </tbody>
      </table>
      <h2>Observed resubmission</h2>
      <table>
        <thead>
          <tr>
            <th>Horizon</th>
            <th>Eligible</th>
            <th>Resubmitted</th>
            <th>Rate</th>
            <th>Coverage</th>
          </tr>
        </thead>
        <tbody>
          {(report.observed_resubmission || []).map((row) => (
            <ResubmissionRow key={row.horizon_days} row={row} />
          ))}
        </tbody>
      </table>
    </>
  );
}

function ResubmissionRow({ row }) {
  const unavailable = row.available === false;
  return (
    <tr>
      <td>{row.horizon_days}-day</td>
      <td>{unavailable ? "—" : sampleNote(row.eligible_n)}</td>
      <td>{unavailable ? "—" : row.resubmitted_n}</td>
      <td>{unavailable ? "—" : formatRate(row.rate)}</td>
      <td>
        {unavailable
          ? "Unavailable because account retention is shorter than this horizon. "
          : ""}
        {row.coverage_note}
      </td>
    </tr>
  );
}

function Metric({ label, value }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value ?? "—"}</dd>
    </div>
  );
}
