import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { formatTime } from "../labels.js";

export function LibraryPage() {
  const [runs, setRuns] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState("");

  async function refresh() {
    const [nextRuns, nextJobs] = await Promise.all([api("/api/runs"), api("/api/jobs")]);
    setRuns(nextRuns);
    setJobs(nextJobs);
  }

  async function removeRun(run) {
    const name = run.source_filename || run.id;
    const confirmed = window.confirm(
      `Delete the analysis for ${name}? Its uploaded video is deleted when no other run uses it.`,
    );
    if (!confirmed) return;
    try {
      await api(`/api/runs/${encodeURIComponent(run.id)}`, { method: "DELETE" });
      await refresh();
    } catch (exc) {
      setError(exc.message);
    }
  }

  useEffect(() => {
    refresh().catch((exc) => setError(exc.message));
    const timer = setInterval(() => {
      refresh().catch((exc) => setError(exc.message));
    }, 2000);
    return () => clearInterval(timer);
  }, []);

  const queued = jobs.filter((job) => job.status === "queued" || job.status === "running");
  const failed = jobs.filter((job) => job.status === "failed");

  return (
    <div className="stack">
      <h1>Library</h1>
      {error && <p className="error">{error}</p>}
      <section>
        <h2>Queue</h2>
        {queued.length === 0 && <p className="muted">No queued or running analyses.</p>}
        <JobTable jobs={queued} />
      </section>
      <section>
        <h2>Failures</h2>
        {failed.length === 0 && <p className="muted">No failed jobs.</p>}
        <JobTable jobs={failed} showError />
      </section>
      <section>
        <h2>Job history</h2>
        <JobTable jobs={jobs} showError />
      </section>
      <section>
        <h2>Runs</h2>
        <table>
          <thead>
            <tr>
              <th>Run</th>
              <th>Language</th>
              <th>Stage</th>
              <th>Mode</th>
              <th>Duration</th>
              <th>Video</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td><Link to={`/runs/${encodeURIComponent(run.id)}`}>{run.source_filename || run.id}</Link></td>
                <td>{run.language ? run.language.toUpperCase() : "—"}</td>
                <td>{run.stage_id || "—"}</td>
                <td>{run.battle_mode_id || "—"}</td>
                <td>{formatTime(run.duration_seconds)}</td>
                <td>{run.video_available ? "available" : "unavailable"}</td>
                <td>
                  <button type="button" onClick={() => removeRun(run)}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function JobTable({ jobs, showError = false }) {
  if (jobs.length === 0) return null;
  return (
    <table>
      <thead>
        <tr>
          <th>Job</th>
          <th>Status</th>
          <th>Phase</th>
          <th>Language</th>
          <th>Run</th>
          {showError && <th>Error</th>}
        </tr>
      </thead>
      <tbody>
        {jobs.map((job) => (
          <tr key={job.id}>
            <td><code>{job.id.slice(0, 8)}</code></td>
            <td>{job.status}</td>
            <td>{job.phase}</td>
            <td>{job.kind && job.kind !== "analyze" ? "—" : job.language}</td>
            <td>
              {job.run_id ? (
                <Link to={`/runs/${encodeURIComponent(job.run_id)}`}>{job.run_id.slice(0, 8)}</Link>
              ) : "—"}
            </td>
            {showError && (
              <td>
                {job.error ? (
                  <details>
                    <summary>{job.error.message}{job.error.exit_code != null ? ` (${job.error.exit_code})` : ""}</summary>
                    <pre>{job.error.log_tail}</pre>
                  </details>
                ) : "—"}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
