import { useEffect, useRef, useState } from "react";
import {
  cancelRecordingUpload, closeUnpaidRecording, confirmRecordingUpload, loadCaptureDevices, loadRecording,
  loadRecordings, putRecording, recordingAction, requestRecordingUpload, stationAction, stationStatus,
} from "../api.js";
import { formatDate } from "./AdminUi.jsx";
import {
  RECORDING_ACCEPT, canCancelUpload, canCheckUpload, canUploadRecording, capturingJob,
  fileMatchesAttempt, hydrationSelection, nextUploadDeadline, normalizedReviewCode,
  recordingContentType, stationHoldsRecording, stationPoll, stripeReference, uploadPhase,
} from "./recordingForm.js";

export function AdminRecordingsPage() {
  const [jobs, setJobs] = useState([]);
  const [unpaid, setUnpaid] = useState([]);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [code, setCode] = useState("");
  const [message, setMessage] = useState("");
  const [refund, setRefund] = useState("");
  const [wrongCode, setWrongCode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState(null);
  const [transferring, setTransferring] = useState(false);
  const [uploadNote, setUploadNote] = useState("");
  const [uploadError, setUploadError] = useState("");
  const uploadInFlight = useRef(false);
  const [closedNote, setClosedNote] = useState("");
  const [stationEnabled, setStationEnabled] = useState(false);
  const [devices, setDevices] = useState([]);
  const [device, setDevice] = useState("");
  const [station, setStation] = useState(null);
  const [stationTakes, setStationTakes] = useState({});
  async function refresh({ initial = false } = {}) {
    try {
      const listed = await loadRecordings();
      setJobs(listed.jobs);
      setUnpaid(listed.unpaid || []);
      setStationEnabled(Boolean(listed.station_enabled));
      setStationTakes(listed.station_takes || {});
      const opened = hydrationSelection(listed.station_takes, null, initial);
      if (opened) await select(opened, Boolean(listed.station_enabled));
    } catch (exc) { setError(exc.message); }
  }
  useEffect(() => { refresh({ initial: true }); }, []);
  function rememberTake(id, view) {
    setStationTakes((takes) => {
      const rest = { ...takes };
      delete rest[id];
      if (!stationHoldsRecording(view?.phase)) return rest;
      const { phase, elapsed_seconds, capture_mode, size_warning } = view;
      return { ...rest, [id]: { phase, elapsed_seconds, capture_mode, size_warning } };
    });
  }
  async function select(id, withStation = stationEnabled) {
    try {
      const next = await loadRecording(id);
      if (next.job.submission_id !== detail?.job.submission_id) {
        setUploadNote(""); setUploadError(""); setFile(null);
        setNote(""); setCode(""); setWrongCode(false); setMessage(""); setRefund("");
      }
      setDetail(next); setError("");
      if (withStation) {
        const view = await stationStatus(id);
        setStation(view); rememberTake(id, view);
      }
    } catch (exc) { setError(exc.message); }
  }
  async function act(action) {
    setBusy(true); setError("");
    try {
      setDetail(await recordingAction(detail.job.submission_id, {
        action, expected_version: detail.job.version, note, review_code: code || null,
        customer_message: message || null, refund_reference: refund || null,
      }));
      setNote(""); setCode(""); setWrongCode(false); setRefund(""); await refresh();
    } catch (exc) { setError(exc.message); }
    finally { setBusy(false); }
  }
  async function reloadDevices() {
    try {
      const payload = await loadCaptureDevices();
      setDevices(payload.devices || []);
    } catch (exc) { setError(exc.message); }
  }
  useEffect(() => {
    if (!stationEnabled) return undefined;
    let stop = false;
    loadCaptureDevices().then((payload) => {
      if (!stop) setDevices(payload.devices || []);
    }).catch((exc) => setError(exc.message));
    return () => { stop = true; };
  }, [stationEnabled]);
  const selectedId = detail?.job.submission_id;
  const poll = stationPoll(stationTakes, selectedId, station?.phase);
  useEffect(() => {
    if (!stationEnabled) return undefined;
    const timer = setInterval(() => {
      if (poll.kind === "queue") { refresh(); return; }
      stationStatus(poll.id).then((view) => {
        rememberTake(poll.id, view);
        if (poll.id === selectedId) setStation(view);
      }).catch(() => {});
    }, poll.ms);
    return () => clearInterval(timer);
  }, [stationEnabled, poll.kind, poll.id, selectedId]);
  const attemptId = detail?.upload_attempt?.attempt ?? detail?.upload_attempt?.issued_at;
  const uploadDeadline = nextUploadDeadline(detail);
  useEffect(() => {
    if (!selectedId || uploadDeadline === null) return undefined;
    const timer = setTimeout(() => select(selectedId), Math.max(0, uploadDeadline - Date.now()) + 250);
    return () => clearTimeout(timer);
  }, [selectedId, attemptId, uploadDeadline]);
  async function runStation(action, body) {
    setBusy(true); setError("");
    try {
      const id = detail.job.submission_id;
      const view = await stationAction(id, action, body);
      setStation(view); rememberTake(id, view);
      await select(id);
    } catch (exc) { setError(exc.message); await refresh(); }
    finally { setBusy(false); }
  }
  async function uploadRecording() {
    if (uploadInFlight.current || !file) return;
    const contentType = recordingContentType(file);
    if (!contentType) { setUploadError("Choose an MP4 or MOV file."); return; }
    uploadInFlight.current = true;
    setBusy(true); setUploadError(""); setUploadNote("Requesting an upload link…");
    const id = detail.job.submission_id;
    try {
      const grant = await requestRecordingUpload(id, {
        expected_version: detail.job.version, filename: file.name,
        content_type: contentType, size_bytes: file.size,
      });
      setTransferring(true); setUploadNote("Uploading… keep this page open.");
      const sent = await putRecording(grant.upload, file);
      setTransferring(false);
      setUploadNote(sent === "already_stored" ? "This upload's file is already stored. Checking it…" : "Upload finished. Checking the file…");
      setDetail(await confirmRecordingUpload(id, { expected_version: grant.version }));
      setFile(null); setUploadNote(""); await refresh();
    } catch (exc) { setUploadNote(""); setUploadError(exc.message); await select(id); }
    finally { setTransferring(false); setBusy(false); uploadInFlight.current = false; }
  }
  async function checkUpload() {
    setBusy(true); setUploadError(""); setUploadNote("Checking the stored file…");
    const id = detail.job.submission_id;
    try {
      setDetail(await confirmRecordingUpload(id, { expected_version: detail.job.version }));
      setUploadNote(""); await refresh();
    } catch (exc) { setUploadNote(""); setUploadError(exc.message); await select(id); }
    finally { setBusy(false); }
  }
  async function cancelUpload() {
    const until = formatDate(detail.upload_attempt?.replace_after);
    const warning = `Cancelling does not revoke the signed upload link at storage, so a new file upload is blocked until ${until}. To send a file with the same type and size again, close this dialog and use Retry upload instead. Cancel this upload?`;
    if (!window.confirm(warning)) return;
    setBusy(true); setUploadError(""); setUploadNote("");
    const id = detail.job.submission_id;
    try {
      setDetail(await cancelRecordingUpload(id, { expected_version: detail.job.version }));
      setFile(null); await refresh();
    } catch (exc) { setUploadError(exc.message); await select(id); }
    finally { setBusy(false); }
  }
  async function closeUnpaid(item, reason) {
    setBusy(true); setError(""); setClosedNote("");
    try {
      const closed = await closeUnpaidRecording(item.submission_id, { expected_version: item.version, reason });
      setClosedNote(cancellationNote(item.public_reference, closed));
      if (detail?.job.submission_id === item.submission_id) setDetail(null);
      await refresh();
    } catch (exc) { setError(exc.message); await refresh(); }
    finally { setBusy(false); }
  }
  const job = detail?.job;
  const held = stationHoldsRecording(station?.phase);
  const open = job && !["closed", "ready", "expired"].includes(job.state);
  const capturingId = capturingJob(stationTakes);
  const capturingTake = capturingId ? stationTakes[capturingId] : null;
  const referenceOf = (id) => jobs.find((item) => item.submission_id === id)?.public_reference || id.slice(0, 8);
  const otherCapturing = capturingId && capturingId !== job?.submission_id ? referenceOf(capturingId) : null;
  return (
    <section>
      <h1>Manual recording queue</h1>
      <p>Paid requests with verified contact addresses. Pending recording: {jobs.filter((item) => item.state === "awaiting_recording").length}</p>
      <button onClick={() => refresh()}>Refresh</button>
      {capturingTake ? <p role="status">
        Recording in progress: <code>{referenceOf(capturingId)}</code> · {Math.round(capturingTake.elapsed_seconds ?? 0)}s
        {capturingTake.capture_mode ? ` · ${capturingTake.capture_mode}` : ""}{" "}
        {capturingId !== job?.submission_id ? <button type="button" onClick={() => select(capturingId)}>Open recording</button> : null}
      </p> : null}
      {error ? <p className="error" role="alert">{error}</p> : null}
      <JobTable jobs={jobs} takes={stationTakes} selected={job?.submission_id} onSelect={select} />
      <h2>Unpaid requests</h2>
      <p>These customers have not paid. Cancelling one first expires its Stripe checkout, then closes the request and emails a confirmed address. If Stripe shows a payment, the payment is recorded and the request is not cancelled. The reason is an internal audit note; the customer does not see it.</p>
      {closedNote ? <p role="status">{closedNote}</p> : null}
      <UnpaidTable jobs={unpaid} busy={busy} onClose={closeUnpaid} />
      {job ? <section>
        <h2>Job {job.submission_id}</h2>
        <p>Support reference: <code>{job.public_reference}</code></p>
        {job.review_code ? <p>Review code: <code>{job.review_code}</code></p> : null}
        <p>State: {job.state}</p>
        <p>Verified contact: <a href={`mailto:${detail.verified_contact_email}`}>{detail.verified_contact_email}</a></p>
        <p>Stripe payment reference: {stripeReference(detail)}</p>
        {canUploadRecording(job) ? <p><strong>録画中は、リプレイの初期視点を変更しないでください。</strong></p> : null}
        {stationEnabled ? <StationPanel
          busy={busy} devices={devices} device={device} setDevice={setDevice}
          station={station} job={job} otherCapturing={otherCapturing} onRefresh={reloadDevices}
          onStart={() => runStation("start", { device_name: device, expected_version: job.version })}
          onStop={() => runStation("stop")}
          onAccept={() => runStation("accept")}
          onDiscard={() => runStation("discard", { expected_version: station?.version ?? job.version })}
          onUpload={() => runStation("upload", { expected_version: station?.version ?? job.version })}
        /> : null}
        {(canUploadRecording(job) || job.state === "uploading") && !held ? <UploadPanel
          detail={detail} transferring={transferring} busy={busy} file={file} note={uploadNote}
          error={uploadError} onFile={setFile} onUpload={uploadRecording} onCheck={checkUpload}
          onCancel={cancelUpload}
        /> : null}
        {open ? <section>
          <h3>Recording exception</h3>
          <p>The operator note is an internal audit record. It is required to flag an exception, return a flagged job to the queue, correct its review code, or close it. The customer does not see it.</p>
          <label>Operator note<textarea value={note} maxLength={2000} onChange={(event) => setNote(event.target.value)} /></label>
          <p>This message appears on the customer's status page after you flag an exception or close the job. It does not send email.</p>
          <label>Message on the customer's status page (optional)<textarea value={message} maxLength={1000} onChange={(event) => setMessage(event.target.value)} /></label>
          <button disabled={busy || !note.trim() || held} onClick={() => act("recording_failed")}>Flag recording exception</button>
          {held ? <p>Stop or discard the capture station take before flagging an exception.</p> : null}
          {job.state === "recording_failed" ? <FailedRecordingActions
            detail={detail} busy={busy} noted={Boolean(note.trim())} code={code} setCode={setCode}
            wrongCode={wrongCode} setWrongCode={setWrongCode} refund={refund} setRefund={setRefund} onAct={act}
          /> : null}
        </section> : null}
        <h3>History</h3>
        <ul>{detail.events.map((event, index) => <li key={index}>{event.created_at} · {event.kind}{event.details.note ? ` · ${event.details.note}` : ""}</li>)}</ul>
      </section> : null}
    </section>
  );
}

const CLOSE_WARNING = "Closing purges the customer's contact details and review code. This cannot be undone. Close this job?";

function FailedRecordingActions({ detail, busy, noted, code, setCode, wrongCode, setWrongCode, refund, setRefund, onAct }) {
  const corrected = normalizedReviewCode(code);
  const correctionReady = Boolean(corrected) && corrected !== detail.job.review_code;
  const closeAfterConfirm = (action) => { if (window.confirm(CLOSE_WARNING)) onAct(action); };
  return (
    <>
      <p>Recording and upload are paused while this job is flagged. Write a note and choose Record again to return it to the queue with the same review code.</p>
      <button type="button" disabled={busy || !noted || wrongCode} onClick={() => onAct("retry_recording")}>Record again</button>
      <label>
        <input type="checkbox" checked={wrongCode} onChange={(event) => { setWrongCode(event.target.checked); setCode(""); }} />
        The review code was wrong
      </label>
      {wrongCode ? <>
        <label>Corrected review code<input value={code} maxLength={100} onChange={(event) => setCode(event.target.value)} /></label>
        <button type="button" disabled={busy || !noted || !correctionReady} onClick={() => onAct("correct_code")}>Save correction and return to queue</button>
      </> : null}
      {detail.authorization === "voucher" ? <>
        <p>Paid by voucher. No refund to record.</p>
        <button type="button" disabled={busy || !noted} onClick={() => closeAfterConfirm("close_without_refund")}>Close without refund</button>
      </> : <>
        <p>First issue the refund in Stripe. Then enter its refund reference (re_…) here to record it in this system. This button does not send money back.</p>
        <label>Completed Stripe refund reference<input value={refund} maxLength={200} onChange={(event) => setRefund(event.target.value)} /></label>
        <button type="button" disabled={busy || !noted || !refund.trim()} onClick={() => closeAfterConfirm("record_refund")}>Record external refund and close acquisition</button>
      </>}
    </>
  );
}

const PHASE_TEXT = {
  permitted: "An upload link is open for this file. If the transfer may have finished, choose Check upload again. If it failed, choose a file with the same type and size and click Retry upload. No cancel is needed.",
  transferring: "Uploading… keep this page open.",
  link_expired: "The upload link expired before the file was checked. If the transfer finished, check it; otherwise cancel this upload.",
  rejected: "The last upload was rejected. Cancel it to use the capture station, or wait to upload a new file.",
  waiting: "An earlier upload link can still be used until it expires.",
  verified: "Recording verified. Check it again to finish queueing it for analysis.",
  queued: "Recording verified and queued for analysis.",
};

const CANCELLED_TEXT = "This upload was cancelled. Its signed link cannot be revoked at storage before it expires, so a new upload can start only after the time below. The capture station can still be used now.";

function phaseText(phase, attempt) {
  if (phase === "waiting" && attempt?.status === "abandoned") return CANCELLED_TEXT;
  return PHASE_TEXT[phase];
}

function mismatchText(attempt) {
  return `The selected file must match the original upload's declared file type (${attempt.suffix}) and size (${attempt.size_bytes} bytes). Choose a matching file to retry, or check the upload if it already finished.`;
}

function UploadPanel({ detail, transferring, busy, file, note, error, onFile, onUpload, onCheck, onCancel }) {
  const phase = uploadPhase(detail, transferring);
  const attempt = detail.upload_attempt;
  const waitingOut = ["link_expired", "rejected", "waiting"].includes(phase);
  const canSend = canUploadRecording(detail.job) && !waitingOut && phase !== "transferring";
  const retrying = phase === "permitted";
  const mismatch = retrying && file && !fileMatchesAttempt(file, attempt);
  const status = phaseText(phase, attempt);
  return (
    <section>
      <h3>Upload a recording</h3>
      {canSend ? <>
        <p>Use this for a file recorded in OBS or QuickTime. Choose one MP4 or MOV file, up to the 750 MiB upload limit. The site checks it and queues this same job.</p>
        <label>Recording (MP4 or MOV)<input type="file" accept={RECORDING_ACCEPT} onChange={(event) => onFile(event.target.files?.[0] || null)} /></label>
        <button type="button" disabled={busy || !file || mismatch} onClick={onUpload}>{retrying ? "Retry upload" : "Upload recording"}</button>
        {mismatch ? <p className="error" role="alert">{mismatchText(attempt)}</p> : null}
      </> : null}
      {status ? <p>Upload status: {status}</p> : null}
      {attempt && phase !== "none" && phase !== "queued" ? <p>
        Upload attempt: {attempt.suffix || "file"}{attempt.size_bytes ? `, ${attempt.size_bytes} bytes` : ""}
        {" · "}link valid until {formatDate(attempt.link_expires_at)}
        {waitingOut ? ` · a new upload can start after ${formatDate(attempt.replace_after)}` : ""}
      </p> : null}
      {note ? <p role="status">{note}</p> : null}
      {error ? <p className="error" role="alert">{error}</p> : null}
      {canCheckUpload(detail) ? <button type="button" disabled={busy} onClick={onCheck}>Check upload again</button> : null}
      {canCancelUpload(detail) ? <button type="button" disabled={busy} onClick={onCancel}>Cancel upload</button> : null}
    </section>
  );
}

function takeLabel(take) {
  if (!take) return "—";
  return take.phase === "capturing" ? `capturing · ${Math.round(take.elapsed_seconds ?? 0)}s` : `take ${take.phase}`;
}

function JobTable({ jobs, takes, selected, onSelect }) {
  if (!jobs.length) return <p>No recording jobs.</p>;
  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead>
          <tr><th>Support reference</th><th>Submission</th><th>Status</th><th>Capture</th><th>Created</th></tr>
        </thead>
        <tbody>
          {jobs.map((item) => (
            <tr
              key={item.submission_id}
              className="clickable"
              tabIndex={0}
              aria-selected={item.submission_id === selected}
              onClick={() => onSelect(item.submission_id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelect(item.submission_id);
                }
              }}
            >
              <td><code>{item.public_reference}</code></td>
              <td><code>{item.submission_id}</code></td>
              <td>{item.state}</td>
              <td>{takeLabel(takes[item.submission_id])}</td>
              <td>{formatDate(item.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function cancellationNote(reference, closed) {
  if (closed.cancellation_email === "skipped_unverified") {
    return `Cancelled ${reference}. No email was sent because the customer never confirmed the address.`;
  }
  return closed.email_sent
    ? `Cancelled ${reference}. The cancellation email was sent.`
    : `Cancelled ${reference}. The cancellation email could not be sent yet; it will be retried automatically.`;
}

function UnpaidTable({ jobs, busy, onClose }) {
  const [reasons, setReasons] = useState({});
  if (!jobs.length) return <p>No unpaid requests.</p>;
  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead>
          <tr><th>Support reference</th><th>Submission</th><th>Status</th><th>Created</th><th>Reason (internal)</th><th></th></tr>
        </thead>
        <tbody>
          {jobs.map((item) => {
            const reason = reasons[item.submission_id] || "";
            return (
              <tr key={item.submission_id}>
                <td><code>{item.public_reference}</code></td>
                <td><code>{item.submission_id}</code></td>
                <td>{item.state}</td>
                <td>{formatDate(item.created_at)}</td>
                <td><input aria-label={`Reason for cancelling ${item.public_reference}`} value={reason} maxLength={1000} onChange={(event) => setReasons({ ...reasons, [item.submission_id]: event.target.value })} /></td>
                <td><button type="button" disabled={busy || !reason.trim()} onClick={() => onClose(item, reason.trim())}>Cancel unpaid request</button></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function StationPanel({ busy, devices, device, setDevice, station, job, otherCapturing, onRefresh, onStart, onStop, onAccept, onDiscard, onUpload }) {
  const phase = station?.phase || "absent";
  const seconds = station?.elapsed_seconds;
  const waiting = job.state === "awaiting_recording" || job.state === "recording";
  return (
    <section>
      <h3>Capture station</h3>
      <p>Capture status: {phase}{seconds != null ? ` · ${Math.round(seconds)}s` : ""}</p>
      {station?.capture_mode ? <p>Capture mode: {station.capture_mode}</p> : null}
      {station?.message ? <p>{station.message}</p> : null}
      {station?.size_warning ? <p className="note">This recording is near the size limit.</p> : null}
      {phase === "absent" || (job.state === "awaiting_recording" && phase !== "capturing") ? (
        <>
          <label>Capture device
            <select value={device} onChange={(event) => setDevice(event.target.value)}>
              <option value="">Choose a device</option>
              {devices.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
          </label>
          <button type="button" onClick={onRefresh}>Refresh devices</button>
          <button disabled={busy || !device || !waiting || Boolean(otherCapturing)} onClick={onStart}>Start recording</button>
          {otherCapturing ? <p>Another job ({otherCapturing}) is currently recording. Open it to stop that capture first.</p> : null}
        </>
      ) : null}
      {phase === "capturing" ? <button disabled={busy} onClick={onStop}>Stop recording</button> : null}
      {phase === "interrupted" ? <>
        <p>The capture stopped before a clean finish. Discard it, or keep the file when it is a valid video. Keeping it does not upload it.</p>
        {station?.acceptable ? <button disabled={busy} onClick={onAccept}>Keep this file for upload</button> : null}
        <button disabled={busy} onClick={onDiscard}>Discard recording</button>
      </> : null}
      {phase === "finalized" || phase === "upload_uncertain" ? <>
        <button disabled={busy} onClick={onUpload}>Upload to storage</button>
        <button disabled={busy} onClick={onDiscard}>Discard recording</button>
      </> : null}
      {phase === "verified" ? <p>Published and queued for analysis.</p> : null}
    </section>
  );
}
