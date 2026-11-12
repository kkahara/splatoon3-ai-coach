import { useEffect, useRef, useState } from "react";
import {
  cancelRecordingUpload, closeUnpaidRecording, confirmRecordingUpload, loadRecording,
  loadRecordings, putRecording, recordingAction, requestRecordingUpload,
} from "../api.js";
import { formatDate } from "./AdminUi.jsx";
import {
  RECORDING_ACCEPT, analysisSummary, canCancelUpload, canCheckUpload, canRetryAnalysis, canUploadRecording,
  fileMatchesAttempt, historyLine, nextUploadDeadline, normalizedReviewCode,
  recordingContentType, stripeReference, uploadIndicator, uploadPanelVisible,
  uploadPhase,
} from "./recordingForm.js";

export function AdminRecordingsPage() {
  const [jobs, setJobs] = useState([]);
  const [unpaid, setUnpaid] = useState([]);
  const [tab, setTab] = useState("queue");
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
  async function refresh() {
    try {
      const listed = await loadRecordings();
      setJobs(listed.jobs);
      setUnpaid(listed.unpaid || []);
    } catch (exc) { setError(exc.message); }
  }
  useEffect(() => { refresh(); }, []);
  async function select(id) {
    try {
      const next = await loadRecording(id);
      if (next.job.submission_id !== detail?.job.submission_id) {
        setUploadNote(""); setUploadError(""); setFile(null);
        setNote(""); setCode(""); setWrongCode(false); setMessage(""); setRefund("");
      }
      setDetail(next); setError(""); setTab("queue");
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
  const selectedId = detail?.job.submission_id;
  const attemptId = detail?.upload_attempt?.attempt ?? detail?.upload_attempt?.issued_at;
  const uploadDeadline = nextUploadDeadline(detail);
  useEffect(() => {
    if (!selectedId || uploadDeadline === null) return undefined;
    const timer = setTimeout(() => select(selectedId), Math.max(0, uploadDeadline - Date.now()) + 250);
    return () => clearTimeout(timer);
  }, [selectedId, attemptId, uploadDeadline]);
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
  const open = job && !["closed", "ready", "expired"].includes(job.state);
  return (
    <section>
      <h1>Manual recording queue</h1>
      <p>Paid requests with verified contact addresses. Pending recording: {jobs.filter((item) => item.state === "awaiting_recording").length}</p>
      <button onClick={() => refresh()}>Refresh</button>
      {error ? <p className="error" role="alert">{error}</p> : null}
      <RecordingTabs tab={tab} onChange={setTab} queued={jobs.length} unpaid={unpaid.length} />
      {tab === "unpaid" ? <section id="recordings-panel-unpaid" role="tabpanel" aria-labelledby="recordings-tab-unpaid">
        <p>These customers have not paid. Cancelling one first expires its Stripe checkout, then closes the request and emails a confirmed address. If Stripe shows a payment, the payment is recorded and the request is not cancelled. The reason is an internal audit note; the customer does not see it.</p>
        {closedNote ? <p role="status">{closedNote}</p> : null}
        <UnpaidTable jobs={unpaid} busy={busy} onClose={closeUnpaid} />
      </section> : <section id="recordings-panel-queue" role="tabpanel" aria-labelledby="recordings-tab-queue">
      <JobTable jobs={jobs} selected={job?.submission_id} onSelect={select} />
      {job ? <section>
        <h2>Job {job.submission_id}</h2>
        <dl className="admin-details job-facts">
          <div><dt>Support reference</dt><dd><code>{job.public_reference}</code></dd></div>
          {job.review_code ? <div><dt>Review code</dt><dd><code>{job.review_code}</code></dd></div> : null}
          <div><dt>State</dt><dd>{job.state}</dd></div>
          {detail.analysis ? <div><dt>Analysis</dt><dd>{analysisSummary(detail.analysis)}</dd></div> : null}
          <div><dt>Verified contact</dt><dd><a href={`mailto:${detail.verified_contact_email}`}>{detail.verified_contact_email}</a></dd></div>
          <div><dt>Stripe payment reference</dt><dd>{stripeReference(detail)}</dd></div>
        </dl>
        {uploadPanelVisible(detail) ? <UploadPanel
          detail={detail} transferring={transferring} busy={busy} file={file} note={uploadNote}
          error={uploadError} onFile={setFile} onUpload={uploadRecording} onCheck={checkUpload}
          onCancel={cancelUpload}
        /> : null}
        {open ? <section>
          <h3>Recording exception</h3>
          <p>The operator note is an internal audit record. It is required to flag an exception, return a flagged job to the queue, correct its review code, retry its analysis, or close it. The customer does not see it.</p>
          <label>Operator note<textarea value={note} maxLength={2000} onChange={(event) => setNote(event.target.value)} /></label>
          <p>This message appears on the customer's status page after you flag an exception, retry analysis, or close the job. It does not send email.</p>
          <label>Message on the customer's status page (optional)<textarea value={message} maxLength={1000} onChange={(event) => setMessage(event.target.value)} /></label>
          {canRetryAnalysis(detail) ? <>
            <p>The recording is verified and still in storage, but its analysis failed. Retry analysis runs it again without a new upload, payment, or voucher.</p>
            <button type="button" disabled={busy || !note.trim()} onClick={() => { if (window.confirm(RETRY_ANALYSIS_WARNING)) act("retry_analysis"); }}>Retry analysis</button>
          </> : null}
          <button disabled={busy || !note.trim()} onClick={() => act("recording_failed")}>Flag recording exception</button>
          {job.state === "recording_failed" ? <FailedRecordingActions
            detail={detail} busy={busy} noted={Boolean(note.trim())} code={code} setCode={setCode}
            wrongCode={wrongCode} setWrongCode={setWrongCode} refund={refund} setRefund={setRefund} onAct={act}
          /> : null}
        </section> : null}
        <h3>History</h3>
        <ul>{detail.events.map((event, index) => <li key={index}>{historyLine(event)}</li>)}</ul>
      </section> : null}
      </section>}
    </section>
  );
}

const RECORDING_TABS = [["queue", "Recording queue"], ["unpaid", "Unpaid requests"]];

function RecordingTabs({ tab, onChange, queued, unpaid }) {
  const counts = { queue: queued, unpaid };
  function onKeyDown(event) {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const index = RECORDING_TABS.findIndex(([id]) => id === tab);
    const [next] = RECORDING_TABS[(index + step + RECORDING_TABS.length) % RECORDING_TABS.length];
    onChange(next);
    document.getElementById(`recordings-tab-${next}`)?.focus();
  }
  return (
    <div className="method-tabs" role="tablist" aria-label="Recording requests">
      {RECORDING_TABS.map(([id, label]) => (
        <button
          key={id} id={`recordings-tab-${id}`} type="button" role="tab"
          aria-selected={tab === id} aria-controls={`recordings-panel-${id}`}
          tabIndex={tab === id ? 0 : -1} onClick={() => onChange(id)} onKeyDown={onKeyDown}
        >
          {label} ({counts[id]})
        </button>
      ))}
    </div>
  );
}

const RETRY_ANALYSIS_WARNING = "Retry analysis using the verified recording already in storage? The previous partial analysis files will be replaced. The customer will not receive a new email notification, but their status page will update as analysis progresses.";

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
      {detail.authorization !== "payment" ? <>
        <p>{detail.authorization === "admin" ? "Requested by an administrator without payment." : "Paid by voucher."} No refund to record.</p>
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
  rejected: "The last upload was rejected. Cancel it, or wait to upload a new file.",
  waiting: "An earlier upload link can still be used until it expires.",
  verified: "Recording verified. Check it again to finish queueing it for analysis.",
  queued: "Recording verified and queued for analysis.",
  analysis_failed: "Recording verified, but its analysis failed. Retry analysis below; no new upload is needed.",
};

const CANCELLED_TEXT = "This upload was cancelled. Its signed link cannot be revoked at storage before it expires, so a new upload can start only after the time below.";

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
  const indicator = uploadIndicator(phase, note);
  return (
    <section>
      <h3>Upload a recording</h3>
      {canSend && indicator !== "working" ? <>
        <p>Use this for a file recorded in OBS or QuickTime. Choose one MP4 or MOV file, up to the 750 MiB upload limit. The site checks it and queues this same job.</p>
        <label>Recording (MP4 or MOV)<input type="file" accept={RECORDING_ACCEPT} onChange={(event) => onFile(event.target.files?.[0] || null)} /></label>
        <button type="button" disabled={busy || !file || mismatch} onClick={onUpload}>{retrying ? "Retry upload" : "Upload recording"}</button>
        {mismatch ? <p className="error" role="alert">{mismatchText(attempt)}</p> : null}
      </> : null}
      {indicator === "working" ? <Working text={note} /> : null}
      {indicator === "done" ? <Done text={status} /> : null}
      {status && !indicator ? <p>Upload status: {status}</p> : null}
      {attempt && !indicator && !["none", "queued", "analysis_failed"].includes(phase) ? <p>
        Upload attempt: {attempt.suffix || "file"}{attempt.size_bytes ? `, ${attempt.size_bytes} bytes` : ""}
        {" · "}link valid until {formatDate(attempt.link_expires_at)}
        {waitingOut ? ` · a new upload can start after ${formatDate(attempt.replace_after)}` : ""}
      </p> : null}
      {error ? <p className="error" role="alert">{error}</p> : null}
      {canCheckUpload(detail) ? <button type="button" disabled={busy} onClick={onCheck}>Check upload again</button> : null}
      {canCancelUpload(detail) ? <button type="button" disabled={busy} onClick={onCancel}>Cancel upload</button> : null}
    </section>
  );
}

function JobTable({ jobs, selected, onSelect }) {
  if (!jobs.length) return <p>No recording jobs.</p>;
  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead>
          <tr><th>Support reference</th><th>Submission</th><th>Status</th><th>Created</th></tr>
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

function Working({ text }) {
  return <p className="status-line" role="status"><span className="spinner" aria-hidden="true" />{text}</p>;
}

function Done({ text }) {
  return <p className="status-line" role="status"><span className="done-mark" aria-hidden="true">✓</span>{text}</p>;
}
