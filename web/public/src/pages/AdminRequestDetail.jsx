import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { loadAdminRequest } from "../api.js";
import {
  AdminError,
  AdminLoading,
  DetailList,
  displayLabel,
  formatDate,
  formatDuration,
  formatValue,
} from "./AdminUi.jsx";

export function AdminRequestDetailPage() {
  const { id } = useParams();
  const [request, setRequest] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stopped = false;
    loadAdminRequest(id)
      .then((next) => {
        if (!stopped) {
          setRequest(next);
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
  }, [id]);

  return (
    <section>
      <p><Link to="/admin/requests">← Requests</Link></p>
      <h1>Request detail</h1>
      <AdminError message={error} />
      {!request && !error ? <AdminLoading /> : null}
      {request ? <RequestDetail request={request} /> : null}
    </section>
  );
}

function RequestDetail({ request }) {
  return (
    <>
      <DetailList rows={[
        ["Request ID", request.submission_id],
        ["Submission", request.submission_state === "expired" ? "Expired" : "Retained"],
        ["User", request.user_email],
      ]} />
      {(request.runs || []).map((run) => <RunDetail key={run.run_id} run={run} />)}
    </>
  );
}

function RunDetail({ run }) {
  const inconsistent = run.timing_status === "timing_inconsistent";
  return (
    <article className="admin-run">
      <h2>Run {run.attempt}</h2>
      <DetailList rows={[
        ["Outcome", displayLabel(run.outcome)],
        ["Created", formatDate(run.created_at)],
        ["Started", formatDate(run.started_at)],
        ["Finished", formatDate(run.finished_at)],
        ["Job elapsed", formatDuration(run.job_elapsed_seconds)],
        ["Upload and validation", formatDuration(run.upload_validation_seconds)],
        ["Queue wait", formatDuration(run.queue_wait_seconds)],
        ["Processing duration", formatDuration(run.processing_seconds)],
        ["Other", formatDuration(run.other_seconds)],
        ["Failed stage", run.failed_stage],
        ["Error code", run.error_code],
        ["Diagnostic", run.diagnostic],
        ["Timing", run.timing_status],
        ["Timing coverage", run.timing_coverage],
        ["Pipeline version", run.pipeline_version],
        ["Importance config", run.importance_config_hash],
        ["Snapshot schema", run.snapshot_schema_version],
        ["Snapshot reader", run.snapshot_reader_version],
        ["Snapshot at", formatDate(run.snapshot_at)],
      ]} />
      {inconsistent ? (
        <p className="notice stale">
          Recorded job timing is inconsistent. The negative remainder was preserved.
        </p>
      ) : null}
      <h3>Top-level stages</h3>
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead><tr><th>Stage</th><th>Duration</th></tr></thead>
          <tbody>
            {(run.stages || []).map((stage) => (
              <tr key={stage.stage}>
                <td>{displayLabel(stage.stage)}</td>
                <td>{formatDuration(stage.seconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details className="admin-raw">
        <summary>Vision breakdown and provenance</summary>
        <DetailList rows={[
          ["Vision breakdown", formatValue(run.breakdown)],
          ["LLM", formatValue(run.llm)],
          ["Provenance", formatValue(run.provenance)],
        ]} />
      </details>
      <h3>Timeline</h3>
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead><tr><th>Time</th><th>Event</th><th>Stage</th></tr></thead>
          <tbody>
            {(run.events || []).map((event, index) => (
              <tr key={`${event.at}-${event.kind}-${index}`}>
                <td>{formatDate(event.at)}</td>
                <td>{displayLabel(event.kind)}</td>
                <td>{displayLabel(event.stage)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </article>
  );
}
