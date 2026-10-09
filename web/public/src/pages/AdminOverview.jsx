import { useEffect, useState } from "react";
import { loadAdminOverview } from "../api.js";
import {
  AdminError,
  AdminLoading,
  displayLabel,
  formatDate,
  formatDuration,
} from "./AdminUi.jsx";

export function AdminOverviewPage() {
  const [overview, setOverview] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stopped = false;
    loadAdminOverview()
      .then((next) => {
        if (!stopped) {
          setOverview(next);
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
  }, []);

  return (
    <section>
      <h1>Overview</h1>
      <AdminError message={error} />
      {!overview && !error ? <AdminLoading /> : null}
      {overview ? <OverviewValues value={overview} /> : null}
    </section>
  );
}

function OverviewValues({ value }) {
  return (
    <>
      <dl className="admin-metrics">
        <Metric label="Requests" value={value.requests} />
        <Metric label="Completed" value={value.completed} />
        <Metric label="Failed" value={value.failed} />
        <Metric label="Processing now" value={value.processing_now} />
        <Metric label="Queued now" value={value.queued_now} />
        <Metric label="Active users" value={value.active_users} />
        <Metric label="New users" value={value.new_users} />
        <Metric label="Uploads confirmed" value={value.uploads_confirmed} />
        <Metric label="Median job elapsed" value={formatDuration(value.median_job_elapsed_seconds)} />
        <Metric label="Median processing" value={formatDuration(value.median_processing_seconds)} />
      </dl>
      <h2>Request volume</h2>
      <VolumeBars values={value.volume || []} />
      <h2>Pipeline stages</h2>
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead><tr><th>Stage</th><th>Runs</th><th>Median</th><th>p95</th></tr></thead>
          <tbody>
            {(value.stages || []).map((stage) => (
              <tr key={stage.stage}>
                <td>{displayLabel(stage.stage)}</td>
                <td>{stage.count}</td>
                <td>{formatDuration(stage.median_seconds)}</td>
                <td>{formatDuration(stage.p95_seconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>Recent problems</h2>
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead><tr><th>When</th><th>Kind</th><th>Request</th><th>Elapsed</th></tr></thead>
          <tbody>
            {(value.recent_problems || []).map((problem) => (
              <tr key={`${problem.submission_id}-${problem.finished_at}`}>
                <td>{formatDate(problem.finished_at)}</td>
                <td>{displayLabel(problem.kind)}</td>
                <td>{problem.submission_id}</td>
                <td>{formatDuration(problem.job_elapsed_seconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Metric({ label, value }) {
  return <div className="admin-card"><dt>{label}</dt><dd>{value ?? "—"}</dd></div>;
}

function VolumeBars({ values }) {
  const maximum = Math.max(1, ...values.map((item) => item.count || 0));
  return (
    <div className="admin-volume" aria-label="Requests in each of the last 24 hours">
      {values.map((item) => (
        <span
          key={item.hour}
          title={`${formatDate(item.hour)}: ${item.count}`}
          style={{ height: `${Math.max(4, ((item.count || 0) / maximum) * 100)}%` }}
        />
      ))}
    </div>
  );
}
