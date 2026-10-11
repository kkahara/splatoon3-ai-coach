import { useEffect, useState } from "react";
import { loadAdminVersions } from "../api.js";
import { formatAverage, formatRate, sampleNote } from "./adminFormat.js";
import { WindowSelect } from "./AdminCoaching.jsx";
import { AdminError, AdminLoading, formatDuration } from "./AdminUi.jsx";

export function AdminVersionsPage() {
  const [windowName, setWindowName] = useState("24h");
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stopped = false;
    setReport(null);
    setError("");
    loadAdminVersions({ window: windowName })
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
      <h1>Versions</h1>
      <p>
        Each row is a difference between version groups. A difference is not an
        improvement or a regression.
      </p>
      <WindowSelect value={windowName} onChange={setWindowName} />
      <AdminError message={error} />
      {!report && !error ? <AdminLoading /> : null}
      {report ? <VersionTable rows={report.rows} /> : null}
    </section>
  );
}

function VersionTable({ rows }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Pipeline</th>
          <th>Evidence</th>
          <th>Vision config</th>
          <th>Importance</th>
          <th>Provider</th>
          <th>Model</th>
          <th>Executions</th>
          <th>Failure rate</th>
          <th>Interrupted rate</th>
          <th>Median processing</th>
          <th>Completed submissions</th>
          <th>Recommendations per submission</th>
          <th>Blocked rate</th>
          <th>Snapshot coverage</th>
        </tr>
      </thead>
      <tbody>
        {(rows || []).map((row) => (
          <tr key={versionKey(row)}>
            <td>{blank(row.pipeline_version)}</td>
            <td>{blank(row.evidence_version)}</td>
            <td>{blank(row.vision_config_hash)}</td>
            <td>{blank(row.importance_config_hash)}</td>
            <td>{blank(row.provider)}</td>
            <td>{blank(row.model)}</td>
            <td>{sampleNote(row.n)}</td>
            <td>{formatRate(row.failure_rate)}</td>
            <td>{formatRate(row.interrupted_rate)}</td>
            <td>{formatDuration(row.median_processing_seconds)}</td>
            <td>{sampleNote(row.completed_submissions)}</td>
            <td>{formatAverage(row.recommendations_per_submission)}</td>
            <td>{formatRate(row.blocked_rate)}</td>
            <td>{formatRate(row.snapshot_coverage)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function versionKey(row) {
  return [
    row.pipeline_version,
    row.evidence_version,
    row.vision_config_hash,
    row.importance_config_hash,
    row.provider,
    row.model,
  ].join("|");
}

function blank(value) {
  return value == null || value === "" ? "—" : String(value);
}
