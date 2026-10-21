import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import {
  loadAccount,
  loadConfig,
  loadOwnedSubmission,
  loadSubmission,
  openCheckout,
  sendFeedback,
  shareOwnedReview,
} from "../api.js";
import { useLocale, useT } from "../i18n/LocaleContext.jsx";
import {
  coachAttempted,
  evidenceLines,
  formatDate,
  formatRemaining,
  momentAnchor,
  nextGameTips,
  reachedStep,
  REVIEW_STEPS,
  shownObservation,
  splitMoments,
} from "./reviewMoments.js";

export function ReviewPage() {
  const { token, id } = useParams();
  const owned = Boolean(id);
  const t = useT();
  const { locale } = useLocale();
  const [view, setView] = useState(null);
  const [config, setConfig] = useState(null);
  const [account, setAccount] = useState(null);
  const [error, setError] = useState("");
  const [payError, setPayError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    loadConfig().then(setConfig).catch(() => setConfig(null));
    loadAccount().then(setAccount).catch(() => setAccount(null));
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

  async function pay() {
    if (!token) {
      return;
    }
    setPayError("");
    try {
      const checkout = await openCheckout(token);
      if (checkout.url) {
        window.location.assign(checkout.url);
        return;
      }
      if (checkout.pending || checkout.paid) {
        setConfirming(true);
      }
    } catch (exc) {
      setPayError(exc.message);
    }
  }

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

  return (
    <main className="sheet">
      <AccountBar account={account} onChange={setAccount} />
      <p className="brand">{t("brand")}</p>
      <h1>{t(headingKey(view))}</h1>
      {view?.notice_pending ? <p className="note">{t("review.notice_pending")}</p> : null}
      {view?.status === "expired" ? null : (
        <ReviewLink
          view={view}
          locale={locale}
          copied={copied}
          onCopy={copyLink}
          shared={!owned}
        />
      )}
      {error ? <p className="error">{error}</p> : null}
      {payError ? <p className="error">{payError}</p> : null}
      {view?.error ? <p className="error">{view.error}</p> : null}
      {token && awaitingPayment(view) ? (
        <p>
          <button type="button" onClick={pay}>{t("review.pay")}</button>
        </p>
      ) : null}
      {confirming || (view?.payment_state === "paid" && view.status === "received") ? (
        <p className="note">{t("review.waiting_payment")}</p>
      ) : null}
      <ReviewProgress view={view} />
      {noDeaths(view) ? <p className="note">{t("review.no_deaths")}</p> : null}
      {view?.status === "complete" && !noDeaths(view) ? (
        <Result
          frameBase={owned ? `/api/me/submissions/${id}` : `/api/submissions/${token}`}
          moments={view.result?.moments || []}
          stale={Boolean(view.result?.stale)}
          locale={locale}
        />
      ) : null}
      {view?.status === "complete" && config?.feedback ? (
        <FeedbackForm owned={owned} token={token} id={id} />
      ) : null}
      {view?.status === "complete" && !noDeaths(view) ? (
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
      <button type="button" className="secondary" onClick={openPanel}>
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

function ReviewLink({ view, locale, copied, onCopy, shared }) {
  const t = useT();
  const date = view?.status === "complete" ? formatDate(view.expires_at, locale) : null;
  return (
    <p className="review-link">
      <span>{date ? t("review.saved_until", { date }) : t("review.keep_link")}</span>
      <button type="button" className="linkish" onClick={onCopy}>
        {copied ? t("review.copied") : t("review.copy_link")}
      </button>
      {copied && shared ? (
        <span className="review-link-note" role="status">
          {t("share.anyone")}
        </span>
      ) : null}
    </p>
  );
}

function ReviewProgress({ view }) {
  const t = useT();
  const reached = view ? reachedStep(view) : 0;
  const spinning = Boolean(view)
    && !awaitingPayment(view)
    && !["complete", "failed", "expired"].includes(view.status);
  const steps = <ProgressSteps reached={reached} spinning={spinning} />;
  if (view?.status !== "complete") {
    return steps;
  }
  return (
    <div className="progress-done">
      <p className="progress-summary">
        <span aria-hidden="true">✓ </span>
        {t("review.progress.complete")}
      </p>
      <details>
        <summary>{t("review.progress.details")}</summary>
        {steps}
      </details>
    </div>
  );
}

function ProgressSteps({ reached, spinning }) {
  const t = useT();
  return (
    <ol className="progress">
      {REVIEW_STEPS.map((step, index) => {
        const state = index < reached ? "done" : spinning && index === reached ? "current" : "pending";
        return (
          <li
            key={step}
            className={`progress-step ${state}`}
            aria-current={state === "current" ? "step" : undefined}
          >
            <span className="progress-mark" aria-hidden="true">
              {state === "done" ? "✓" : ""}
            </span>
            <span>{t(`review.step.${step}`)}</span>
            {state === "current" ? (
              <span className="spinner" role="status" aria-label={t("review.in_progress")} />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

function Result({ frameBase, moments, stale, locale }) {
  const t = useT();
  const { key: coached, other } = splitMoments(moments);
  return (
    <>
      {stale ? <p className="notice stale">{t("review.stale_evidence")}</p> : null}
      {moments.length === 0 ? (
        <p>{t("review.no_moments")}</p>
      ) : (
        <>
          <MatchSummary coached={coached} total={moments.length} locale={locale} />
          {coached.length ? (
            <section className="key-moments">
              <h2>{t("review.key_moments")}</h2>
              <ul className="moments">
                {coached.map(({ moment, index }) => (
                  <li key={index} id={momentAnchor(index)} className="moment-card">
                    <KeyMoment moment={moment} frameBase={frameBase} index={index} locale={locale} />
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          <OtherMoments items={other} frameBase={frameBase} locale={locale} />
        </>
      )}
    </>
  );
}

function MatchSummary({ coached, total, locale }) {
  const t = useT();
  const tips = nextGameTips(coached);
  return (
    <section className="match-summary">
      <h2>{t("review.match_heading")}</h2>
      <p className="note">
        {coached.length
          ? t("review.match_counts", { count: coached.length, total })
          : t("review.no_key_moments")}
      </p>
      {tips.length ? <h3>{t("review.tips_heading")}</h3> : null}
      {tips.length === 1 ? (
        <div className="tips single">
          <Tip tip={tips[0]} locale={locale} />
        </div>
      ) : null}
      {tips.length > 1 ? (
        <ol className="tips">
          {tips.map((tip) => (
            <li key={tip.index}>
              <Tip tip={tip} locale={locale} />
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}

function Tip({ tip, locale }) {
  const t = useT();
  const anchor = momentAnchor(tip.index);
  return (
    <>
      <span
        className="tip-text"
        lang={locale !== "en" && tip.moment.recommendations_locale === "en" ? "en" : undefined}
      >
        {tip.text}
      </span>
      <a
        className="tip-from"
        href={`#${anchor}`}
        onClick={(event) => {
          event.preventDefault();
          document.getElementById(anchor)?.scrollIntoView({ behavior: "smooth" });
        }}
      >
        {t("review.tip_from", { moment: momentTitle(tip.moment, t) })}
      </a>
    </>
  );
}

function KeyMoment({ moment, frameBase, index, locale }) {
  const t = useT();
  return (
    <>
      <h3>{momentTitle(moment, t)}</h3>
      <Coaching moment={moment} locale={locale} />
      {moment.frame ? <MomentStill frameBase={frameBase} index={index} /> : null}
      <Evidence moment={moment} label={t("review.match_evidence")} />
      {moment.marks?.length ? (
        <AnalysisDetails>
          <EpisodeTimeline marks={moment.marks} gaps={moment.gaps || []} />
        </AnalysisDetails>
      ) : null}
    </>
  );
}

function Coaching({ moment, locale }) {
  const t = useT();
  const fellBack = locale !== "en" && moment.assessment_locale === "en";
  const recsFellBack = locale !== "en" && moment.recommendations_locale === "en";
  const recommendations = moment.recommendations;
  return (
    <div className="coaching">
      <h4>{t("review.work_on")}</h4>
      {recommendations.length === 1 ? (
        <p className="coaching-text" lang={recsFellBack ? "en" : undefined}>
          {recommendations[0]}
        </p>
      ) : (
        <ul className="coaching-list" lang={recsFellBack ? "en" : undefined}>
          {recommendations.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
      <h4 className="quiet-label">{t("review.coach_note")}</h4>
      <p className="coach-note" lang={fellBack ? "en" : undefined}>
        {moment.assessment}
      </p>
      {fellBack || recsFellBack ? <p className="note">{t("review.english_fallback")}</p> : null}
    </div>
  );
}

function Evidence({ moment, label }) {
  const lines = evidenceLines(moment);
  if (!lines.length) {
    return null;
  }
  return (
    <div className="evidence">
      <h4>{label}</h4>
      <ul>
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

function AnalysisDetails({ children }) {
  const t = useT();
  return (
    <details className="analysis-details">
      <summary>{t("review.analysis_details")}</summary>
      {children}
    </details>
  );
}

function OtherMoments({ items, frameBase, locale }) {
  const t = useT();
  if (!items.length) {
    return null;
  }
  return (
    <section className="other-moments">
      <h2>{t("review.other_moments")}</h2>
      <p className="note">{t("review.other_intro")}</p>
      <ul className="moments">
        {items.map(({ moment, index }) => (
          <li key={index} id={momentAnchor(index)}>
            <OtherMoment moment={moment} frameBase={frameBase} index={index} locale={locale} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function OtherMoment({ moment, frameBase, index, locale }) {
  const t = useT();
  const marks = moment.marks || [];
  const observation = shownObservation(moment);
  return (
    <>
      <h3>{momentTitle(moment, t)}</h3>
      {observation ? (
        <>
          <h4 className="quiet-label">{t("review.what_happened")}</h4>
          <p
            className="coach-note"
            lang={locale !== "en" && moment.assessment_locale === "en" ? "en" : undefined}
          >
            {observation}
          </p>
        </>
      ) : null}
      {coachAttempted(moment) ? (
        <p className="no-recommendation">
          <strong>{t("review.no_recommendation")}</strong>
          <span>{t("review.no_recommendation_detail")}</span>
        </p>
      ) : null}
      <Evidence moment={moment} label={t("review.match_evidence")} />
      {moment.frame || marks.length ? (
        <AnalysisDetails>
          {moment.frame ? <MomentStill frameBase={frameBase} index={index} /> : null}
          {marks.length ? <EpisodeTimeline marks={marks} gaps={moment.gaps || []} /> : null}
        </AnalysisDetails>
      ) : null}
    </>
  );
}

function momentTitle(moment, t) {
  if (moment.heading) {
    return moment.heading;
  }
  const time = t("review.video_time", { time: formatMatch(moment.video_time) });
  return `${scenarioTitle(moment.scenario_type, t)} — ${time}`;
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
  const t = useT();
  const place = spread ? { left: `${left}%` } : undefined;
  const remaining = mark.anchor ? formatRemaining(mark.seconds_remaining) : null;
  return (
    <>
      <div className={spread ? "episode-mark" : "episode-mark inline"} style={place}>
        <span className={mark.anchor ? "episode-dot anchor" : "episode-dot"} />
        <span
          className={copyClass(lane, mark.anchor)}
          style={spread ? { transform: align } : undefined}
        >
          <span className="episode-name">{mark.title}</span>
          {remaining ? (
            <span className="episode-clock">{t("review.remaining", { time: remaining })}</span>
          ) : null}
          {!mark.anchor && mark.offset_label ? (
            <span className="episode-clock">{mark.offset_label}</span>
          ) : null}
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
      loading="lazy"
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

function noDeaths(view) {
  return view?.status === "complete" && view.outcome === "no_deaths";
}

function awaitingPayment(view) {
  return view?.payment_state === "unpaid" || view?.payment_state === "checkout_created";
}

function headingKey(view) {
  if (!view) {
    return "review.heading.default";
  }
  if (awaitingPayment(view)) {
    return "review.heading.payment";
  }
  if (view.status === "complete") {
    return noDeaths(view) ? "review.heading.no_moments" : "review.heading.complete";
  }
  if (view.status === "failed") {
    return "review.heading.failed";
  }
  if (view.status === "expired") {
    return "review.heading.expired";
  }
  return "review.heading.processing";
}

function titleCase(value) {
  return String(value || "Moment")
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}