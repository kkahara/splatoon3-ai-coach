import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import {
  createSubmission,
  loadAccount,
  loadConfig,
  markUploaded,
  openCheckout,
  recordPageView,
  uploadVideo,
  validateVoucher,
} from "../api.js";
import heroBanner from "../assets/splatoon3-ai-coach-banner.jpg";
import { useT } from "../i18n/LocaleContext.jsx";
import {
  formatFileSize,
  formatUsd,
  rememberedGameLanguage,
  rememberGameLanguage,
  submissionBody,
  submitButtonLabel,
  submitError,
  submitReady,
  videoProblem,
} from "./submitForm.js";

const REGULAR_MAX_BYTES = 750 * 1024 * 1024;

function videoLimits(config, t) {
  if (config && config.max_video_bytes == null) {
    return t("submit.drop_limits_any");
  }
  const bytes = config?.max_video_bytes || REGULAR_MAX_BYTES;
  const mb = Math.round(bytes / (1024 * 1024));
  return t("submit.drop_limits", { mb });
}

/** Size cap to enforce, or null when there is none or config has not loaded. */
function videoCap(config) {
  return config?.max_video_bytes ?? null;
}

function SampleReview() {
  const t = useT();
  return (
    <section className="sample-card" aria-labelledby="sample-heading">
      <header>
        <h2 id="sample-heading">{t("sample.heading")}</h2>
        <span className="sample-tag">{t("sample.tag")}</span>
      </header>
      <dl>
        <dt>{t("sample.improve_label")}</dt>
        <dd>{t("sample.improve")}</dd>
        <dt>{t("sample.next_label")}</dt>
        <dd>{t("sample.next")}</dd>
      </dl>
    </section>
  );
}

function UploadFlow() {
  const t = useT();
  return (
    <ol className="flow" aria-label={t("submit.flow_label")}>
      <li>{t("submit.flow.upload")}</li>
      <li>{t("submit.flow.analyze")}</li>
      <li>{t("submit.flow.review")}</li>
    </ol>
  );
}

function ChosenVideo({ file, onClear }) {
  const t = useT();
  return (
    <div className="file-chosen" role="status" aria-label={t("submit.chosen_file")}>
      <span className="file-icon" aria-hidden="true">▶</span>
      <span className="file-meta">
        <span className="file-name">{file.name}</span>
        <span className="file-size">{formatFileSize(file.size)}</span>
      </span>
      <button type="button" className="file-cancel" onClick={onClear}>
        {t("submit.cancel_file")}
      </button>
    </div>
  );
}

function DropZone({ inputRef, limits, onChoose }) {
  const t = useT();
  const [dragging, setDragging] = useState(false);
  return (
    <>
      <input
        id="video-input"
        ref={inputRef}
        className="drop-input"
        type="file"
        accept=".mp4,.mov,video/mp4,video/quicktime"
        onChange={(event) => onChoose(event.target.files?.[0] || null)}
      />
      <label
        htmlFor="video-input"
        className={dragging ? "dropzone dragging" : "dropzone"}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          onChoose(event.dataTransfer.files?.[0] || null);
        }}
      >
        <span className="drop-title">{t("submit.drop_title")}</span>
        <span className="drop-limits">{limits}</span>
        <span className="drop-hint">{t("submit.drop_hint")}</span>
      </label>
    </>
  );
}

function GameLanguageField({ language, onChange }) {
  const t = useT();
  return (
    <label>
      {t("submit.system_language")}
      <span className="field-help" id="game-language-help">
        {t("submit.system_language_help")}
      </span>
      <select
        value={language}
        aria-describedby="game-language-help"
        onChange={(event) => onChange(event.target.value)}
        required
      >
        <option value="" disabled>
          {t("submit.choose_language")}
        </option>
        <option value="en">English</option>
        <option value="ja">日本語</option>
      </select>
    </label>
  );
}

function UploadFacts({ config }) {
  const t = useT();
  return (
    <ul className="upload-facts">
      <li>{t("submit.duration")}</li>
      {config?.retention_days ? (
        <li>{t("submit.retention", { days: config.retention_days })}</li>
      ) : null}
    </ul>
  );
}

export function SubmitPage() {
  const navigate = useNavigate();
  const t = useT();
  const [config, setConfig] = useState(null);
  const [file, setFile] = useState(null);
  // No browser-locale default: it selects HUD templates and must match the video.
  const [language, setLanguage] = useState(rememberedGameLanguage);
  const fileInput = useRef(null);
  const [account, setAccount] = useState(null);
  const [notify, setNotify] = useState(false);
  const [notifyTouched, setNotifyTouched] = useState(false);
  const [email, setEmail] = useState("");
  const [turnstileToken, setTurnstileToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [voucherCode, setVoucherCode] = useState("");
  const [voucherPreview, setVoucherPreview] = useState(null);
  const [voucherError, setVoucherError] = useState("");

  const sessionKey = account ? `${account.role}:${account.email}` : "";

  useEffect(() => {
    recordPageView().catch(() => {});
    loadAccount()
      .then(setAccount)
      .catch((exc) => setError(exc.message));
  }, []);

  useEffect(() => {
    // The priced label depends on the session: an admin is not charged.
    let current = true;
    loadConfig()
      .then((next) => {
        if (current) {
          setConfig(next);
        }
      })
      .catch((exc) => {
        if (current) {
          setError(exc.message);
        }
      });
    return () => {
      current = false;
    };
  }, [sessionKey]);

  useEffect(() => {
    if (account && !notifyTouched) {
      setNotify(Boolean(account.notify_by_email));
    }
  }, [account, notifyTouched]);

  useEffect(() => {
    if (!config?.turnstile_site_key) {
      return undefined;
    }
    const script = document.createElement("script");
    script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js";
    script.async = true;
    script.onload = () => {
      const slot = document.getElementById("turnstile-slot");
      if (slot && window.turnstile) {
        window.turnstile.render(slot, {
          sitekey: config.turnstile_site_key,
          callback: (token) => setTurnstileToken(token),
        });
      }
    };
    document.body.appendChild(script);
    return () => script.remove();
  }, [config?.turnstile_site_key]);

  function clearFile() {
    setFile(null);
    setError("");
    if (fileInput.current) {
      fileInput.current.value = "";
    }
  }

  function chooseFile(next) {
    if (!next) {
      return;
    }
    const problem = videoProblem(next, videoCap(config));
    if (problem) {
      clearFile();
      setError(t(problem));
      return;
    }
    setFile(next);
    setError("");
  }

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    const token = config?.dev_mode ? "dev" : turnstileToken;
    const maxBytes = config && config.max_video_bytes == null ? null : config?.max_video_bytes;
    const problem = submitError({ file, language, maxBytes, token });
    if (problem) {
      setError(t(problem));
      return;
    }
    const fresh = await loadConfig();
    if (fresh.payment_required && fresh.price_cents !== config?.price_cents) {
      setConfig(fresh);
      setError(t("submit.price_changed"));
      return;
    }
    setBusy(true);
    try {
      const created = await createSubmission(
        submissionBody({ token, file, language, account, notify, email, voucherCode }),
      );
      await uploadVideo(created.upload, file);
      await markUploaded(created.token);
      if (created.voucher_state === "pending") {
        navigate(created.review_path);
        return;
      }
      if (created.payment_required) {
        const checkout = await openCheckout(created.token);
        if (checkout.url) {
          window.location.assign(checkout.url);
          return;
        }
      }
      navigate(created.review_path);
    } catch (exc) {
      setError(exc.reason ? t(`submit.voucher.${exc.reason}`) : exc.message);
      setBusy(false);
    }
  }

  function changeVoucher(value) {
    setVoucherCode(value);
    setVoucherPreview(null);
    setVoucherError("");
  }

  async function applyVoucher() {
    setVoucherError("");
    setError("");
    try {
      setVoucherPreview(await validateVoucher(voucherCode));
    } catch (exc) {
      setVoucherPreview(null);
      setVoucherError(exc.reason ? t(`submit.voucher.${exc.reason}`) : exc.message);
    }
  }

  return (
    <main className="sheet">
      <AccountBar account={account} onChange={setAccount} />
      <div className="hero">
        <img className="hero-banner" src={heroBanner} alt="" />
        <div className="hero-copy">
          <h1>{t("submit.title")}</h1>
          <p className="hero-lede">{t("submit.intro")}</p>
        </div>
      </div>
      <SampleReview />
      <section className="upload-card" aria-labelledby="upload-heading">
        <h2 id="upload-heading">{t("submit.upload_heading")}</h2>
        <UploadFlow />
        <form onSubmit={onSubmit}>
          {file ? (
            <ChosenVideo file={file} onClear={clearFile} />
          ) : (
            <DropZone inputRef={fileInput} limits={videoLimits(config, t)} onChoose={chooseFile} />
          )}
          <GameLanguageField
            language={language}
            onChange={(next) => {
              setLanguage(next);
              rememberGameLanguage(next);
            }}
          />
          <label className="check">
            <input
              type="checkbox"
              checked={notify}
              onChange={(event) => {
                setNotifyTouched(true);
                setNotify(event.target.checked);
              }}
            />
            {t("submit.notify")}
          </label>
          {!account && notify ? (
            <label>
              {t("common.email")}
              <input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
              />
            </label>
          ) : null}
          {config?.turnstile_site_key ? <div id="turnstile-slot" /> : null}
          {error ? <p className="error">{error}</p> : null}
          {config?.payment_required && config.price_cents > 0 ? (
            <>
              <p className="note">{t("submit.one_time")}</p>
              <p className="note">{t("submit.terms")}</p>
            </>
          ) : null}
          <details className="voucher">
            <summary>{t("submit.voucher_prompt")}</summary>
            <label>
              {t("submit.voucher_code")}
              <input
                value={voucherCode}
                autoComplete="off"
                spellCheck="false"
                onChange={(event) => changeVoucher(event.target.value)}
              />
            </label>
            <button type="button" onClick={applyVoucher} disabled={busy || !voucherCode.trim()}>
              {t("submit.voucher_apply")}
            </button>
            {voucherError ? <p className="error">{voucherError}</p> : null}
            {voucherPreview?.accepted ? (
              <>
                <p className="note">{t("submit.voucher_accepted")}</p>
                {voucherPreview.benefit_type === "externally_prepaid" ? (
                  <p className="note">{t("submit.voucher_prepaid")}</p>
                ) : null}
                <p className="note">{t("submit.voucher_payment", { price: formatUsd(0) })}</p>
              </>
            ) : null}
          </details>
          <button
            type="submit"
            disabled={
              busy
              || (config?.payment_required && config.price_cents == null)
              || !submitReady({ file, language })
            }
          >
            {busy ? t("submit.submitting") : submitButtonLabel(t, config, voucherPreview)}
          </button>
          <UploadFacts config={config} />
        </form>
      </section>
      <p className="unofficial">{t("legal.unofficial")}</p>
    </main>
  );
}
