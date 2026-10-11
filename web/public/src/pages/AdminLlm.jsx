import { useEffect, useState } from "react";
import { loadAdminLlm } from "../api.js";
import { formatEstimatedCost, formatRate } from "./adminFormat.js";
import { WindowSelect } from "./AdminCoaching.jsx";
import { AdminError, AdminLoading } from "./AdminUi.jsx";

export function AdminLlmPage() {
  const [windowName, setWindowName] = useState("24h");
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stopped = false;
    setReport(null);
    setError("");
    loadAdminLlm({ window: windowName })
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
      <h1>LLM</h1>
      <WindowSelect value={windowName} onChange={setWindowName} />
      <AdminError message={error} />
      {!report && !error ? <AdminLoading /> : null}
      {report ? <LlmReport report={report} /> : null}
    </section>
  );
}

function LlmReport({ report }) {
  const latency = report.latency_ms || {};
  return (
    <>
      <p>
        Estimated from estimated tokens. Pricing version {report.pricing_version}. An
        unknown model has no cost.
      </p>
      <dl className="admin-metrics">
        <Metric label="Calls" value={report.calls} />
        <Metric label="Requested" value={report.requested} />
        <Metric label="Succeeded" value={report.succeeded} />
        <Metric label="Failed" value={report.failed} />
        <Metric label="Skipped" value={report.skipped} />
        <Metric label="Median latency" value={milliseconds(latency.median)} />
        <Metric label="p95 latency" value={milliseconds(latency.p95)} />
        <Metric label="Input tokens" value={report.input_tokens_est} />
        <Metric label="Output tokens" value={report.output_tokens_est} />
        <Metric label="Estimated cost" value={formatEstimatedCost(report.estimated_cost)} />
        <Metric label="Known price share" value={formatRate(report.priced_call_share)} />
      </dl>
      <h2>Latency</h2>
      <table>
        <tbody>
          <tr>
            <th>Median</th>
            <td>{milliseconds(latency.median)}</td>
          </tr>
          <tr>
            <th>p95</th>
            <td>{milliseconds(latency.p95)}</td>
          </tr>
          <tr>
            <th>Timed calls</th>
            <td>{latency.n}</td>
          </tr>
        </tbody>
      </table>
      <GroupTable title="Provider" rows={report.providers} nameKey="provider" />
      <GroupTable title="Model" rows={report.models} nameKey="model" />
    </>
  );
}

function GroupTable({ title, rows, nameKey }) {
  return (
    <section>
      <h2>{title}</h2>
      <table>
        <thead>
          <tr>
            <th>{title}</th>
            <th>Calls</th>
            <th>Estimated cost</th>
          </tr>
        </thead>
        <tbody>
          {(rows || []).map((row) => (
            <tr key={`${row.provider || ""}-${row[nameKey]}`}>
              <td>{row[nameKey]}</td>
              <td>{row.calls}</td>
              <td>{formatEstimatedCost(row.estimated_cost)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
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

function milliseconds(value) {
  if (value == null) {
    return "—";
  }
  return `${Math.round(Number(value))} ms`;
}
