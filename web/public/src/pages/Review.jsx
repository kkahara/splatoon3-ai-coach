import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  loadConfig,
  loadOwnedSubmission,
  loadSubmission,
  sendFeedback,
  shareOwnedReview,
} from "../api.js";

const STEPS = [
  ["received", "Received"],
  ["checked", "Video checked"],
  ["queued", "Queued"],
  ["analyzing", "Analyzing match"],
  ["coaching", "Building coaching"],
  ["ready", "Ready"],
];

export function ReviewPage() {
  const { token, id } = useParams();
  const owned = Boolean(id);
  const [view, setView] = useState(null);
  const [config, setConfig] = useState(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    loadConfig().then(setConfig).catch(() => setConfig(null));
  }, []);

  useEffect(() => {
    let stopped = false;
    async function poll() {
      try {
        const next = owned ? await loadOwnedSubmission(id) : await loadSubmission(token);
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
  }, [token, id, owned]);

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
  const spinning = Boolean(view) && !["complete", "failed", "expired"].includes(view.status);

  return (
    <main className="sheet">
      <p className="brand">Splatoon 3 AI Coach</p>
      <h1>{heading(view)}</h1>
      <p>Keep this link. You can return to this page at any time.</p>
      <div className="link-row">
        <input readOnly value={window.location.href} aria-label="Private review link" />
        <button type="button" onClick={copyLink}>
          {copied ? "Copied" : "Copy link"}
        </button>
      </div>
      {error ? <p className="error">{error}</p> : null}
      {view?.error ? <p className="error">{view.error}</p> : null}
      <ol className="steps">
        {STEPS.map(([id, label], index) => (
          <li key={id} className={index < reached ? "done" : spinning && index === reached ? "current" : ""}>
            {label}
            {spinning && index === reached ? (
              <span className="spinner" role="status" aria-label="In progress" />
            ) : null}
          </li>
        ))}
      </ol>
      {view?.status === "complete" ? (
        <Result
          frameBase={owned ? `/api/me/submissions/${id}` : `/api/submissions/${token}`}
          moments={view.result?.moments || []}
        />
      ) : null}
      {view?.status === "complete" && config?.feedback ? (
        <FeedbackForm owned={owned} token={token} id={id} />
      ) : null}
      {view?.status === "complete" ? (
        <ShareReview
          moments={view.result?.moments || []}
          frameBase={owned ? `/api/me/submissions/${id}` : `/api/submissions/${token}`}
          matchSeconds={view.match_seconds}
          owned={owned}
          id={id}
        />
      ) : null}
      <p className="again">
        {owned ? (
          <>
            <Link to="/coaching">Past coaching</Link>
            {" · "}
          </>
        ) : null}
        <Link to="/">Submit another video</Link>
      </p>
    </main>
  );
}

const SHARE_TITLE = "Splatoon 3 Coaching Review";
const SHARE_TEXT = "See what happened in this match and get evidence-based coaching feedback.";

function ShareReview({ moments, frameBase, matchSeconds, owned, id }) {
  const panel = useRef(null);
  const [url, setUrl] = useState("");
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const frameIndex = moments.findIndex((moment) => moment.frame);
  const native = typeof navigator !== "undefined" && typeof navigator.share === "function";

  useEffect(() => {
    const node = panel.current;
    if (open && url && node && !node.open) {
      node.showModal();
    }
  }, [open, url]);

  async function openPanel() {
    setCopied(false);
    setError("");
    try {
      const next = owned
        ? `${window.location.origin}${await shareOwnedReview(id)}`
        : window.location.href;
      setUrl(next);
      setOpen(true);
    } catch (exc) {
      setError(exc.message);
    }
  }

  function markCopied() {
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }

  function copyReview() {
    if (!url) {
      return;
    }
    if (copyWithSelection(url)) {
      markCopied();
      return;
    }
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(url).then(markCopied).catch(() => {});
    }
  }

  function shareMore() {
    navigator.share({ title: SHARE_TITLE, text: SHARE_TEXT, url }).catch(() => {});
  }

  return (
    <section className="share">
      <h2>Share your coaching</h2>
      <p>Want to show someone what your coach found?</p>
      <button type="button" onClick={openPanel}>
        Share review
      </button>
      {error ? <p className="error">{error}</p> : null}
      <p className="note">Anyone with the link can view this coaching review.</p>
      <dialog className="share-panel" ref={panel} onClose={() => setOpen(false)}>
        <h3>Share this coaching review</h3>
        <p>Share your Splatoon coaching review with friends.</p>
        <button type="button" onClick={copyReview} disabled={!url}>
          {copied ? "✓ Link copied" : "Copy link"}
        </button>
        <div className="share-actions">
          <a href={shareHref("x", url)} target="_blank" rel="noreferrer">
            X
          </a>
          <a href={shareHref("facebook", url)} target="_blank" rel="noreferrer">
            Facebook
          </a>
          {native ? (
            <button type="button" onClick={shareMore}>
              More…
            </button>
          ) : null}
        </div>
        <p className="note">Your review link is ready to share.</p>
        <div className="share-preview">
          <strong>{SHARE_TITLE}</strong>
          <p>{momentLine(moments.length, matchSeconds)}</p>
          <p>Evidence-based analysis of this match.</p>
          {frameIndex >= 0 ? (
            <img alt="" src={`${frameBase}/moments/${frameIndex}/frame`} />
          ) : null}
        </div>
        <button type="button" className="linkish" onClick={() => panel.current?.close()}>
          Close
        </button>
      </dialog>
    </section>
  );
}

function shareHref(kind, url) {
  if (!url) {
    return undefined;
  }
  if (kind === "x") {
    const text = encodeURIComponent(SHARE_TITLE);
    return `https://twitter.com/intent/tweet?text=${text}&url=${encodeURIComponent(url)}`;
  }
  return `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(url)}`;
}

function momentLine(count, matchSeconds) {
  const label = count === 1 ? "1 coaching moment" : `${count} coaching moments`;
  if (matchSeconds == null) {
    return label;
  }
  return `${label} · ${formatMatch(matchSeconds)} match`;
}

function formatMatch(seconds) {
  const whole = Math.max(0, Math.floor(Number(seconds) || 0));
  const minutes = Math.floor(whole / 60);
  const secs = String(whole % 60).padStart(2, "0");
  return `${minutes}:${secs}`;
}

function FeedbackForm({ owned, token, id }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (sent) {
    return <p>Thanks, we received your feedback.</p>;
  }
  if (!open) {
    return (
      <p className="again">
        <button type="button" className="linkish" onClick={() => setOpen(true)}>
          Feedback
        </button>
      </p>
    );
  }

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await sendFeedback(owned ? { id, body: text } : { token, body: text });
      setSent(true);
    } catch (exc) {
      setError(exc.message);
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit}>
      <label>
        Feedback
        <textarea
          value={text}
          maxLength={2000}
          rows={4}
          onChange={(event) => setText(event.target.value)}
          required
        />
      </label>
      {error ? <p className="error">{error}</p> : null}
      <button type="submit" disabled={busy}>
        {busy ? "Sending…" : "Send feedback"}
      </button>
    </form>
  );
}

function Result({ frameBase, moments }) {
  return (
    <section>
      <h2>Key moments</h2>
      {moments.length === 0 ? <p>No coaching moments were produced.</p> : null}
      <ul className="moments">
        {moments.map((moment, index) => (
          <li key={`${moment.scenario_type}-${moment.video_time}-${index}`}>
            {moment.heading ? (
              <Episode moment={moment} frameBase={frameBase} index={index} />
            ) : (
              <PlainMoment moment={moment} frameBase={frameBase} index={index} />
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Episode({ moment, frameBase, index }) {
  return (
    <>
      <h3>{moment.heading}</h3>
      <EpisodeTimeline marks={moment.marks || []} gaps={moment.gaps || []} />
      {moment.until_active_again ? <p className="until">{moment.until_active_again}</p> : null}
      {moment.recovery_context ? <p>{moment.recovery_context}</p> : null}
      {moment.context?.length ? (
        <>
          <h4>Before death</h4>
          <ul className="context">
            {moment.context.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </>
      ) : null}
      <h4>Coaching</h4>
      {moment.assessment ? <p className="assessment">{moment.assessment}</p> : null}
      {moment.frame ? <MomentStill frameBase={frameBase} index={index} /> : null}
    </>
  );
}

function EpisodeTimeline({ marks, gaps }) {
  if (!marks.length) {
    return null;
  }
  const times = marks.map((mark) => Number(mark.video_time) || 0);
  const start = Math.min(...times);
  const span = Math.max(...times) - start;
  const spread = marks.length > 1 && span > 0;
  const lanes = markLanes(times, span, spread);
  const tall = lanes.some((lane) => lane > 0);
  return (
    <div className={timelineClass(spread, tall)}>
      {spread ? <div className="episode-track" /> : null}
      {marks.map((mark, index) => (
        <EpisodeMark
          key={`${mark.label}-${mark.video_time}-${index}`}
          mark={mark}
          spread={spread}
          lane={lanes[index]}
          left={spread ? ((times[index] - start) / span) * 100 : 0}
          align={markAlign(index, marks.length)}
          gap={index < marks.length - 1 ? gaps[index] : null}
          gapLeft={gapLeft(times, index, start, span, spread)}
        />
      ))}
    </div>
  );
}

function timelineClass(spread, tall) {
  if (!spread) {
    return "episode-row";
  }
  return tall ? "episode-timeline tall" : "episode-timeline";
}

function markLanes(times, span, spread) {
  const lanes = times.map(() => 0);
  if (!spread) {
    return lanes;
  }
  for (let index = 1; index < times.length; index += 1) {
    const gap = ((times[index] - times[index - 1]) / span) * 100;
    if (gap < 30) {
      lanes[index] = lanes[index - 1] === 0 ? 1 : 0;
    }
  }
  return lanes;
}

function EpisodeMark({ mark, spread, lane, left, align, gap, gapLeft }) {
  const place = spread ? { left: `${left}%` } : undefined;
  return (
    <>
      <div className={spread ? "episode-mark" : "episode-mark inline"} style={place}>
        <span className={mark.anchor ? "episode-dot anchor" : "episode-dot"} />
        <span
          className={copyClass(lane, mark.anchor)}
          style={spread ? { transform: align } : undefined}
        >
          <span className="episode-name">{mark.title}</span>
          {mark.clock ? <span className="episode-clock">{mark.clock}</span> : null}
        </span>
      </div>
      {gap ? (
        <span
          className={spread ? "episode-gap" : "episode-gap-inline"}
          style={spread ? { left: `${gapLeft}%` } : undefined}
        >
          {gap}
        </span>
      ) : null}
    </>
  );
}

function copyClass(lane, anchor) {
  const names = ["episode-copy"];
  if (lane) {
    names.push("low");
  }
  if (anchor) {
    names.push("anchor");
  }
  return names.join(" ");
}

function gapLeft(times, index, start, span, spread) {
  if (!spread || index >= times.length - 1) {
    return 0;
  }
  return (((times[index] + times[index + 1]) / 2 - start) / span) * 100;
}

function markAlign(index, count) {
  if (index === 0) {
    return "translateX(0)";
  }
  if (index === count - 1) {
    return "translateX(-100%)";
  }
  return "translateX(-50%)";
}

function PlainMoment({ moment, frameBase, index }) {
  return (
    <>
      <h3>
        {titleCase(moment.scenario_type)} — {formatTime(moment.video_time)}
      </h3>
      {moment.frame ? <MomentStill frameBase={frameBase} index={index} /> : null}
      {moment.statements.map((statement) => (
        <p key={statement}>{statement}</p>
      ))}
      {moment.assessment ? <p className="assessment">{moment.assessment}</p> : null}
    </>
  );
}

function MomentStill({ frameBase, index }) {
  const [hidden, setHidden] = useState(false);
  if (hidden) {
    return null;
  }
  return (
    <img
      className="still"
      alt=""
      src={`${frameBase}/moments/${index}/frame`}
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
