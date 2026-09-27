import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { loadSubmission } from "../api.js";

const STEPS = [
  ["received", "Received"],
  ["checked", "Video checked"],
  ["queued", "Queued"],
  ["analyzing", "Analyzing match"],
  ["coaching", "Building coaching"],
  ["ready", "Ready"],
];

export function ReviewPage() {
  const { token } = useParams();
  const [view, setView] = useState(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let stopped = false;
    async function poll() {
      try {
        const next = await loadSubmission(token);
        if (!stopped) {
          setView(next);
          setError("");
        }
        return next.status;
      } catch (exc) {
        if (!stopped) {
          setError(exc.message);
        }
        return "failed";
      }
    }
    poll();
    const timer = setInterval(async () => {
      const status = await poll();
      if (["complete", "failed", "expired"].includes(status)) {
        clearInterval(timer);
      }
    }, 3000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [token]);

  function copyLink() {
    const link = window.location.href;
    if (copyWithSelection(link)) {
      setCopied(true);
      return;
    }
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(link).then(() => setCopied(true)).catch(() => {});
    }
  }

  const reached = view ? reachedStep(view) : 0;

  return (
    <main className="sheet">
      <p className="brand">Splatoon 3 AI Coach</p>
      <h1>{heading(view)}</h1>
      <p>Keep this link. You can return to this page at any time.</p>
      <input readOnly value={window.location.href} aria-label="Private review link" />
      <button type="button" onClick={copyLink}>
        {copied ? "Copied" : "Copy link"}
      </button>
      {error ? <p className="error">{error}</p> : null}
      {view?.error ? <p className="error">{view.error}</p> : null}
      <ol className="steps">
        {STEPS.map(([id, label], index) => (
          <li key={id} className={index < reached ? "done" : ""}>
            {label}
          </li>
        ))}
      </ol>
      {view?.status === "complete" ? (
        <Result token={token} moments={view.result?.moments || []} />
      ) : null}
    </main>
  );
}

function Result({ token, moments }) {
  return (
    <section>
      <h2>Key moments</h2>
      {moments.length === 0 ? <p>No coaching moments were produced.</p> : null}
      <ul className="moments">
        {moments.map((moment, index) => (
          <li key={`${moment.scenario_type}-${moment.video_time}-${index}`}>
            <h3>
              {titleCase(moment.scenario_type)} — {formatTime(moment.video_time)}
            </h3>
            {moment.frame ? <MomentStill token={token} index={index} /> : null}
            {moment.statements.map((statement) => (
              <p key={statement}>{statement}</p>
            ))}
            {moment.assessment ? <p className="assessment">{moment.assessment}</p> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function MomentStill({ token, index }) {
  const [hidden, setHidden] = useState(false);
  if (hidden) {
    return null;
  }
  return (
    <img
      className="still"
      alt=""
      src={`/api/submissions/${encodeURIComponent(token)}/moments/${index}/frame`}
      onError={() => setHidden(true)}
    />
  );
}

function copyWithSelection(link) {
  const area = document.createElement("textarea");
  area.value = link;
  area.setAttribute("readonly", "");
  document.body.appendChild(area);
  area.select();
  const copiedLink = document.execCommand("copy");
  area.remove();
  return copiedLink;
}

function heading(view) {
  if (!view) {
    return "Your review";
  }
  if (view.status === "complete") {
    return "Your Coaching Review";
  }
  if (view.status === "failed") {
    return "Review failed";
  }
  if (view.status === "expired") {
    return "Review expired";
  }
  return "Your analysis is processing";
}

function reachedStep(view) {
  if (view.status === "complete") {
    return STEPS.length;
  }
  if (view.status === "validating") {
    return 1;
  }
  const index = STEPS.findIndex(([id]) => id === view.step);
  return index < 0 ? 0 : index;
}

function titleCase(value) {
  return String(value || "Moment")
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatTime(seconds) {
  const safe = Number(seconds) || 0;
  const whole = Math.floor(safe);
  const minutes = Math.floor(whole / 60);
  const secs = String(whole % 60).padStart(2, "0");
  const tenths = Math.floor((safe - whole) * 10);
  return `${minutes}:${secs}.${tenths}`;
}
