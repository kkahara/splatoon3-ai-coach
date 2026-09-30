import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  loadConfig,
  loadOwnedSubmission,
  loadSubmission,
  sendFeedback,
  shareOwnedReview,
} from "../api.js";
import { useLocale, useT } from "../i18n/LocaleContext.jsx";

const STEPS = ["received", "checked", "queued", "analyzing", "coaching", "ready"];

export function ReviewPage() {
  const { token, id } = useParams();
  const owned = Boolean(id);
  const t = useT();
  const { locale } = useLocale();
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
        const next = owned
          ? await loadOwnedSubmission(id, locale)
          : await loadSubmission(token, locale);
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
    let timer = null;
    async function loop() {
      const status = await poll();
      if (!stopped && !["complete", "failed", "expired"].includes(status)) {
        timer = setTimeout(loop, 3000);
      }
    }
    loop();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [token, id, owned, locale]);

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
      <p className="brand">{t("brand")}</p>
      <h1>{t(headingKey(view))}</h1>
      <p>{t("review.keep_link")}</p>
      <div className="link-row">
        <input readOnly value={window.location.href} aria-label={t("review.private_link")} />
        <button type="button" onClick={copyLink}>
          {copied ? t("review.copied") : t("review.copy_link")}
        </button>
      </div>
      {error ? <p className="error">{error}</p> : null}
      {view?.error ? <p className="error">{view.error}</p> : null}
      <ol className="steps">
        {STEPS.map((step, index) => (
          <li key={step} className={index < reached ? "done" : spinning && index === reached ? "current" : ""}>
            {t(`review.step.${step}`)}
            {spinning && index === reached ? (
              <span className="spinner" role="status" aria-label={t("review.in_progress")} />
            ) : null}
          </li>
        ))}
      </ol>
      {view?.status === "complete" ? (
        <Result
          frameBase={owned ? `/api/me/submissions/${id}` : `/api/submissions/${token}`}
          moments={view.result?.moments || []}
          locale={locale}
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
            <Link to="/coaching">{t("account.past_coaching")}</Link>
            {" · "}
          </>
        ) : null}
        <Link to="/">{t("review.submit_another")}</Link>
      </p>
    </main>
  );
}

function ShareReview({ moments, frameBase, matchSeconds, owned, id }) {
  const t = useT();
  const shareTitle = t("share.card_title");
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
    navigator.share({ title: shareTitle, text: t("share.card_text"), url }).catch(() => {});
  }

  return (
    <section className="share">
      <h2>{t("share.heading")}</h2>
      <p>{t("share.prompt")}</p>
      <button type="button" onClick={openPanel}>
        {t("share.button")}
      </button>
      {error ? <p className="error">{error}</p> : null}
      <p className="note">{t("share.anyone")}</p>
      <dialog className="share-panel" ref={panel} onClose={() => setOpen(false)}>
        <h3>{t("share.panel_title")}</h3>
        <p>{t("share.panel_text")}</p>
        <button type="button" onClick={copyReview} disabled={!url}>
          {copied ? t("share.link_copied") : t("review.copy_link")}
        </button>
        <div className="share-actions">
          <a href={shareHref("x", url, shareTitle)} target="_blank" rel="noreferrer">
            X
          </a>
          <a href={shareHref("facebook", url, shareTitle)} target="_blank" rel="noreferrer">
            Facebook
          </a>
          {native ? (
            <button type="button" onClick={shareMore}>
              {t("share.more")}
            </button>
          ) : null}
        </div>
        <p className="note">{t("share.ready")}</p>
        <div className="share-preview">
          <strong>{shareTitle}</strong>
          <p>{momentLine(moments.length, matchSeconds, t)}</p>
          <p>{t("share.evidence")}</p>
          {frameIndex >= 0 ? (
            <img alt="" src={`${frameBase}/moments/${frameIndex}/frame`} />
          ) : null}
        </div>
        <button type="button" className="linkish" onClick={() => panel.current?.close()}>
          {t("share.close")}
        </button>
      </dialog>
    </section>
  );
}

function shareHref(kind, url, title) {
  if (!url) {
    return undefined;
  }
  if (kind === "x") {
    const text = encodeURIComponent(title);
    return `https://twitter.com/intent/tweet?text=${text}&url=${encodeURIComponent(url)}`;
  }
  return `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(url)}`;
}

function momentLine(count, matchSeconds, t) {
  const label = count === 1 ? t("share.moments_one") : t("share.moments_many", { count });
  if (matchSeconds == null) {
    return label;
  }
  return t("share.moments_match", { label, time: formatMatch(matchSeconds) });
}

function formatMatch(seconds) {
  const whole = Math.max(0, Math.floor(Number(seconds) || 0));
  const minutes = Math.floor(whole / 60);
  const secs = String(whole % 60).padStart(2, "0");
  return `${minutes}:${secs}`;
}

function FeedbackForm({ owned, token, id }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (sent) {
    return <p>{t("feedback.thanks")}</p>;
  }
  if (!open) {
    return (
      <p className="again">
        <button type="button" className="linkish" onClick={() => setOpen(true)}>
          {t("feedback.label")}
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
        {t("feedback.label")}
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
        {busy ? t("common.sending") : t("feedback.send")}
      </button>
    </form>
  );
}

function Result({ frameBase, moments, locale }) {
  const t = useT();
  return (
    <section>
      <h2>{t("review.key_moments")}</h2>
      {moments.length === 0 ? <p>{t("review.no_moments")}</p> : null}
      <ul className="moments">
        {moments.map((moment, index) => (
          <li key={`${moment.scenario_type}-${moment.video_time}-${index}`}>
            {moment.heading ? (
              <Episode moment={moment} frameBase={frameBase} index={index} locale={locale} />
            ) : (
              <PlainMoment moment={moment} frameBase={frameBase} index={index} locale={locale} />
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Assessment({ moment, locale }) {
  const t = useT();
  if (!moment.assessment) {
    return null;
  }
  const fellBack = locale !== "en" && moment.assessment_locale === "en";
  return (
    <>
      <p className="assessment" lang={fellBack ? "en" : undefined}>
        {moment.assessment}
      </p>
      {fellBack ? <p className="note">{t("review.english_fallback")}</p> : null}
    </>
  );
}

function Episode({ moment, frameBase, index, locale }) {
  const t = useT();
  return (
    <>
      <h3>{moment.heading}</h3>
      <EpisodeTimeline marks={moment.marks || []} gaps={moment.gaps || []} />
      {moment.until_active_again ? <p className="until">{moment.until_active_again}</p> : null}
      {moment.recovery_context ? <p>{moment.recovery_context}</p> : null}
      {moment.context?.length ? (
        <>
          <h4>{t("review.before_death")}</h4>
          <ul className="context">
            {moment.context.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </>
      ) : null}
      <h4>{t("review.coaching")}</h4>
      <Assessment moment={moment} locale={locale} />
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

function PlainMoment({ moment, frameBase, index, locale }) {
  const t = useT();
  return (
    <>
      <h3>
        {scenarioTitle(moment.scenario_type, t)} — {formatTime(moment.video_time)}
      </h3>
      {moment.frame ? <MomentStill frameBase={frameBase} index={index} /> : null}
      {moment.statements.map((statement) => (
        <p key={statement}>{statement}</p>
      ))}
      <Assessment moment={moment} locale={locale} />
    </>
  );
}

function scenarioTitle(type, t) {
  if (!type) {
    return t("review.moment");
  }
  const key = `scenario.${type}`;
  const label = t(key);
  return label === key ? titleCase(type) : label;
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

function headingKey(view) {
  if (!view) {
    return "review.heading.default";
  }
  if (view.status === "complete") {
    return "review.heading.complete";
  }
  if (view.status === "failed") {
    return "review.heading.failed";
  }
  if (view.status === "expired") {
    return "review.heading.expired";
  }
  return "review.heading.processing";
}

function reachedStep(view) {
  if (view.status === "complete") {
    return STEPS.length;
  }
  if (view.status === "validating") {
    return 1;
  }
  const index = STEPS.indexOf(view.step);
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
