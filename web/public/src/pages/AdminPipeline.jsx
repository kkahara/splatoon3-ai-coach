import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { loadAdminPipeline } from "../api.js";
import { formatRate, sampleNote } from "./adminFormat.js";
import {
  AdminError,
  AdminLoading,
  displayLabel,
  formatDate,
  formatDuration,
} from "./AdminUi.jsx";

export function AdminPipelinePage() {
  const [windowName, setWindowName] = useState("24h");
  const [status, setStatus] = useState("");
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stopped = false;
    setReport(null);
    setError("");
    loadAdminPipeline({ window: windowName, status })
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
  }, [windowName, status]);

  return (
    <section>
      <h1>Pipeline</h1>
      <form className="admin-filters">
        <label>
          Window
          <select value={windowName} onChange={(event) => setWindowName(event.target.value)}>
            <option value="24h">24 hours</option>
            <option value="7d">7 days</option>
            <option value="30d">30 days</option>
          </select>
        </label>
        <label>
          Status
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">All statuses</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="interrupted">Interrupted</option>
            <option value="running">Running</option>
          </select>
        </label>
      </form>
      <AdminError message={error} />
      {!report && !error ? <AdminLoading /> : null}
      {report ? <PipelineReport report={report} /> : null}
    </section>
  );
}

function PipelineReport({ report }) {
  const rates = report.rates || {};
  return (
    <>
      <p>Executions in this window. Current queue depth is not a historical series.</p>
      <dl className="admin-metrics">
        <Metric label="Current queue" value={report.current_queue_depth} />
        <Metric label="In flight" value={report.in_flight} />
        <Metric label="Failure rate" value={formatRate(rates.failure_rate)} />
        <Metric label="Completed rate" value={formatRate(rates.completed_rate)} />
        <Metric label="Interrupted rate" value={formatRate(rates.interrupted_rate)} />
        <Metric label="Median processing" value={formatDuration(report.processing?.median_seconds)} />
        <Metric label="Median queue wait" value={formatDuration(report.queue_wait?.median_seconds)} />
      </dl>
      <h2>Stages</h2>
      <StageTable stages={report.stages || []} />
      <h2>Analyze diagnostics</h2>
      <Diagnostics value={report.diagnostics || {}} />
      <h2>Volume</h2>
      <SeriesChart points={report.series || []} />
      <SeriesTable points={report.series || []} />
      <h2>Version differences</h2>
      <VersionTable rows={report.versions || []} />
      <h2>Processing versus video duration</h2>
      <ScatterPlot scatter={report.scatter || {}} />
    </>
  );
}

function Metric({ label, value }) {
  return <div className="admin-card"><dt>{label}</dt><dd>{value ?? "—"}</dd></div>;
}

function StageTable({ stages }) {
  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead>
          <tr>
            <th>Stage</th><th>Jobs</th><th>Median</th><th>p95</th>
            <th>Nonzero exits</th><th>Terminal failures</th>
          </tr>
        </thead>
        <tbody>
          {stages.map((stage) => (
            <tr key={stage.stage}>
              <td>{displayLabel(stage.stage)}</td>
              <td>{stage.jobs}</td>
              <td>{formatDuration(stage.median_seconds)}</td>
              <td>{formatDuration(stage.p95_seconds)}</td>
              <td>{stage.nonzero_exits}</td>
              <td>{stage.terminal_failures}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Diagnostics({ value }) {
  return (
    <dl className="admin-details">
      <div><dt>Decode median</dt><dd>{formatDuration(value.decode_median)}</dd></div>
      <div><dt>Detector median</dt><dd>{formatDuration(value.detectors_median)}</dd></div>
      <div><dt>Fusion median</dt><dd>{formatDuration(value.fusion_median)}</dd></div>
      <div><dt>Analyze outside vision</dt><dd>{formatDuration(value.analyze_non_vision_median)}</dd></div>
      <div><dt>Missing breakdown</dt><dd>{value.missing_breakdown ?? "—"}</dd></div>
    </dl>
  );
}

function SeriesChart({ points }) {
  const maximum = Math.max(1, ...points.map((point) => point.jobs || 0));
  const width = 640;
  const height = 140;
  const bar = points.length ? width / points.length : width;
  return (
    <svg className="admin-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Jobs per bucket">
      {points.map((point, index) => {
        const barHeight = ((point.jobs || 0) / maximum) * (height - 8);
        return (
          <rect
            key={point.bucket}
            x={index * bar + 1}
            y={height - barHeight}
            width={Math.max(1, bar - 2)}
            height={barHeight}
            fill="currentColor"
          >
            <title>{`${formatDate(point.bucket)}: ${point.jobs} jobs`}</title>
          </rect>
        );
      })}
    </svg>
  );
}

function SeriesTable({ points }) {
  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead>
          <tr>
            <th>Bucket</th><th>Jobs</th><th>Processing median</th>
            <th>Queue wait median</th><th>Failure rate</th><th>Interrupted rate</th>
          </tr>
        </thead>
        <tbody>
          {points.map((point) => (
            <tr key={point.bucket}>
              <td>{formatDate(point.bucket)}</td>
              <td>{point.jobs}</td>
              <td>{formatDuration(point.processing_median)}</td>
              <td>{formatDuration(point.queue_wait_median)}</td>
              <td>{formatRate(point.failure_rate)}</td>
              <td>{formatRate(point.interrupted_rate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function VersionTable({ rows }) {
  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <caption>Differences between versions. These are not causal effects.</caption>
        <thead>
          <tr>
            <th>Pipeline</th><th>Vision config</th><th>Sample</th>
            <th>Failure rate</th><th>Interrupted rate</th><th>Median processing</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={`${row.pipeline_version}-${row.vision_config_hash}`}
              className={row.sample === "insufficient" ? "admin-insufficient" : undefined}
            >
              <td>{row.pipeline_version}</td>
              <td>{row.vision_config_hash}</td>
              <td>{sampleNote(row.n)}</td>
              <td>{formatRate(row.failure_rate)}</td>
              <td>{formatRate(row.interrupted_rate)}</td>
              <td>{formatDuration(row.median_processing)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ScatterPlot({ scatter }) {
  const points = scatter.points || [];
  return (
    <>
      <p>
        Showing {scatter.shown ?? 0} of {scatter.total ?? 0}.
        Exceptional runs included first: {scatter.exceptional_total ?? 0}.
        Sampling mode: {displayLabel(scatter.mode)}.
      </p>
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr><th>Request</th><th>Status</th><th>Processing</th><th>Video</th><th>Timing</th></tr>
          </thead>
          <tbody>
            {points.map((point) => (
              <tr key={point.run_id}>
                <td>
                  <Link to={`/admin/requests/${encodeURIComponent(point.submission_id)}`}>
                    {point.submission_id}
                  </Link>
                </td>
                <td>{displayLabel(point.status)}</td>
                <td>{formatDuration(point.processing_seconds)}</td>
                <td>{formatDuration(point.video_seconds)}</td>
                <td>{point.timing_inconsistent ? "Inconsistent" : "Consistent"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
