import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";

export function AnalyzePage() {
  const [file, setFile] = useState(null);
  const [language, setLanguage] = useState("ja");
  const [videos, setVideos] = useState([]);
  const [videoId, setVideoId] = useState("");
  const [job, setJob] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api("/api/videos").then(setVideos).catch((exc) => setError(exc.message));
  }, []);

  useEffect(() => {
    if (!job || job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
      return undefined;
    }
    const timer = setInterval(() => {
      api(`/api/jobs/${job.job_id || job.id}`).then(setJob).catch((exc) => setError(exc.message));
    }, 1500);
    return () => clearInterval(timer);
  }, [job]);

  async function upload(event) {
    event.preventDefault();
    setError("");
    if (!file) {
      setError("Choose a .mov or .mp4 file.");
      return;
    }
    const body = new FormData();
    body.append("file", file);
    try {
      const record = await api("/api/videos", { method: "POST", body });
      setVideos((current) => [record, ...current]);
      setVideoId(record.id);
      setMessage(`Uploaded ${record.original_filename}`);
    } catch (exc) {
      setError(exc.message);
    }
  }

  async function submit(event) {
    event.preventDefault();
    setError("");
    if (!videoId) {
      setError("Upload a video first.");
      return;
    }
    try {
      const created = await api("/api/jobs", {
        method: "POST",
        body: JSON.stringify({ video_id: videoId, language }),
      });
      setJob(created);
      setMessage("Analysis queued. One analysis runs at a time.");
    } catch (exc) {
      setError(exc.message);
    }
  }

  async function cancel() {
    if (!job) return;
    const id = job.job_id || job.id;
    const updated = await api(`/api/jobs/${id}/cancel`, { method: "POST" });
    setJob(updated);
  }

  const jobId = job && (job.job_id || job.id);

  return (
    <div className="stack">
      <h1>Analyze</h1>
      <p className="muted">Upload a video and choose the language you passed to analysis. The site runs the existing analyze command.</p>
      {error && <p className="error">{error}</p>}
      {message && <p>{message}</p>}
      <form onSubmit={upload} className="row">
        <input
          type="file"
          accept=".mov,.mp4,.m4v,video/mp4,video/quicktime"
          onChange={(event) => setFile(event.target.files?.[0] || null)}
        />
        <button type="submit">Upload</button>
      </form>
      <form onSubmit={submit} className="row">
        <label>
          Video
          <select value={videoId} onChange={(event) => setVideoId(event.target.value)}>
            <option value="">Select</option>
            {videos.map((video) => (
              <option key={video.id} value={video.id}>{video.original_filename}</option>
            ))}
          </select>
        </label>
        <label>
          Language
          <select value={language} onChange={(event) => setLanguage(event.target.value)}>
            <option value="ja">JA</option>
            <option value="en">EN</option>
          </select>
        </label>
        <button type="submit">Start analysis</button>
      </form>
      {job && (
        <section>
          <h2>Current job</h2>
          <p>Status: {job.status || "—"} · Phase: {job.phase || job.status || "—"}</p>
          {job.run_id && <p><Link to={`/runs/${encodeURIComponent(job.run_id)}`}>Open run</Link></p>}
          {job.error && (
            <details open>
              <summary>{job.error.message}</summary>
              <pre>{job.error.log_tail}</pre>
            </details>
          )}
          {(job.status === "queued" || job.status === "running") && (
            <button type="button" onClick={cancel}>Cancel {jobId}</button>
          )}
        </section>
      )}
    </div>
  );
}
