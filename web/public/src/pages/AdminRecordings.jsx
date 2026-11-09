import { useEffect, useState } from "react";
import { loadRecording, loadRecordings, recordingAction } from "../api.js";

export function AdminRecordingsPage() {
  const [jobs, setJobs] = useState([]);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [message, setMessage] = useState("");
  const [refund, setRefund] = useState("");
  const [busy, setBusy] = useState(false);
  async function refresh() {
    try { setJobs((await loadRecordings()).jobs); } catch (exc) { setError(exc.message); }
  }
  useEffect(() => { refresh(); }, []);
  async function select(id) {
    try {
      const next = await loadRecording(id);
      setDetail(next); setError(""); setNote(""); setName("");
      setCode(next.job.review_code || ""); setMessage(""); setRefund("");
    } catch (exc) { setError(exc.message); }
  }
  async function act(action) {
    setBusy(true); setError("");
    try {
      setDetail(await recordingAction(detail.job.submission_id, {
        action, expected_version: detail.job.version, note,
        confirmed_player_name: name || null, review_code: code || null,
        customer_message: message || null, refund_reference: refund || null,
      }));
      setNote(""); await refresh();
    } catch (exc) { setError(exc.message); }
    finally { setBusy(false); }
  }
  const job = detail?.job;
  return (
    <section>
      <h1>Manual recording queue</h1>
      <p>Paid requests with verified contact addresses. Pending recording: {jobs.filter((item) => item.state === "awaiting_recording").length}</p>
      <button onClick={refresh}>Refresh</button>
      {error ? <p className="error" role="alert">{error}</p> : null}
      <ul>{jobs.map((item) => <li key={item.submission_id}>
        <button onClick={() => select(item.submission_id)}>{item.submission_id} · {item.state} · {item.created_at}</button>
      </li>)}</ul>
      {job ? <section>
        <h2>Job {job.submission_id}</h2>
        {job.review_code ? <p>Review code: <code>{job.review_code}</code></p> : null}
        {job.target_player_name ? <p>Customer's in-game name hint: {job.target_player_name}</p> : null}
        <p>State: {job.state}</p>
        <p>Verified contact: <a href={`mailto:${detail.verified_contact_email}`}>{detail.verified_contact_email}</a></p>
        <p>Stripe payment reference: {detail.payment_reference}</p>
        <p>Target: {job.target_confirmed_at ? `Confirmed as ${job.confirmed_player_name}` : "Awaiting operator confirmation"}</p>
        <p>Confirm the intended player by observing the replay. Do not infer their identity from a slot or replay order.</p>
        <label>Operator note (required)<textarea value={note} maxLength={2000} onChange={(event) => setNote(event.target.value)} /></label>
        {job.state === "awaiting_recording" ? <>
          <label>Observed target-player name<input value={name} maxLength={100} onChange={(event) => setName(event.target.value)} /></label>
          <button disabled={busy || !note.trim() || !name.trim()} onClick={() => act("confirm_target")}>Confirm target player</button>
        </> : null}
        <label>Message visible to customer (optional)<textarea value={message} maxLength={1000} onChange={(event) => setMessage(event.target.value)} /></label>
        {!["closed", "ready", "expired"].includes(job.state) ? <button disabled={busy || !note.trim()} onClick={() => act("recording_failed")}>Flag recording exception / contact customer</button> : null}
        {job.state === "recording_failed" ? <>
          <label>Corrected review code<input value={code} maxLength={100} onChange={(event) => setCode(event.target.value)} /></label>
          <button disabled={busy || !note.trim()} onClick={() => act("correct_code")}>Save correction and return to queue</button>
          <p>Refunds must be completed separately in Stripe. This action only records the operator's resolution.</p>
          <label>Completed Stripe refund reference<input value={refund} maxLength={200} onChange={(event) => setRefund(event.target.value)} /></label>
          <button disabled={busy || !note.trim() || !refund.trim()} onClick={() => act("record_refund")}>Record external refund and close acquisition</button>
        </> : null}
        <h3>History</h3>
        <ul>{detail.events.map((event, index) => <li key={index}>{event.created_at} · {event.kind}{event.details.note ? ` · ${event.details.note}` : ""}</li>)}</ul>
      </section> : null}
    </section>
  );
}
