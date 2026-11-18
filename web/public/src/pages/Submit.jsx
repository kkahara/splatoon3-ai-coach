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
import { ReviewCodeForm } from "./ReviewCodeForm.jsx";
import { useVoucher, VoucherField } from "./VoucherField.jsx";
import heroBannerEn from "../assets/splatoon3-ai-coach-banner-en.jpg";
import heroBannerJa from "../assets/splatoon3-ai-coach-banner-ja.jpg";
import { useLocale, useT } from "../i18n/LocaleContext.jsx";
import {
  formatFileSize,
  submissionBody,
  submitButtonLabel,
  submitError,
  submitReady,
  videoProblem,
  voucherErrorKey,
} from "./submitForm.js";

const REGULAR_MAX_BYTES = 750 * 1024 * 1024;

// French (`splatoon3-ai-coach-banner-fr.jpg`) sits beside these and stays unused
// until that site language exists.
const HERO_BANNERS = {
  en: heroBannerEn,
  ja: heroBannerJa,
};

function heroBannerFor(locale) {
  return HERO_BANNERS[locale] ?? HERO_BANNERS.en;
}

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

const METHODS = [
  ["video", "review_code.upload_method"],
  ["review_code", "review_code.code_method"],
];

function MethodTabs({ method, onChange }) {
  const t = useT();
  function onKeyDown(event) {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
    if (!step) {
      return;
    }
    const index = METHODS.findIndex(([id]) => id === method);
    const [next] = METHODS[(index + step + METHODS.length) % METHODS.length];
    onChange(next);
    document.getElementById(`method-tab-${next}`)?.focus();
  }
  return (
    <div className="method-tabs" role="tablist" aria-label={t("review_code.method")}>
      {METHODS.map(([id, label]) => (
        <button
          key={id}
          id={`method-tab-${id}`}
          type="button"
          role="tab"
          aria-selected={method === id}
          aria-controls={`method-panel-${id}`}
          tabIndex={method === id ? 0 : -1}
          onClick={() => onChange(id)}
          onKeyDown={onKeyDown}
        >
          {t(label)}
        </button>
      ))}
    </div>
  );
}

export function SubmitPage() {
  const navigate = useNavigate();
  const { locale } = useLocale();
  const t = useT();
  const [config, setConfig] = useState(null);
  const [file, setFile] = useState(null);
  const [method, setMethod] = useState("video");
  const fileInput = useRef(null);
  const [account, setAccount] = useState(null);
  const [notify, setNotify] = useState(false);
  const [notifyTouched, setNotifyTouched] = useState(false);
  const [email, setEmail] = useState("");
  const [turnstileToken, setTurnstileToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const voucher = useVoucher(validateVoucher);

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
    const problem = submitError({ file, maxBytes, token });
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
        submissionBody({ token, file, account, notify, email, voucherCode: voucher.code }),
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
      const key = voucherErrorKey(exc.reason);
      setError(key ? t(key) : exc.message);
      setBusy(false);
    }
  }

  return (
    <main className="sheet">
      <AccountBar account={account} onChange={setAccount} />
      <div className="hero">
        <img className="hero-banner" src={heroBannerFor(locale)} alt="" />
        <div className="hero-copy">
          <h1>{t("submit.title")}</h1>
          <p className="hero-lede">{t("submit.intro")}</p>
        </div>
      </div>
      <SampleReview />
      {config?.review_codes_enabled && config?.turnstile_site_key ? <div id="turnstile-slot" /> : null}
      {config?.review_codes_enabled ? <>
        <MethodTabs method={method} onChange={setMethod} />
        {method === "review_code" ? <ReviewCodeForm account={account} config={config} turnstileToken={turnstileToken} /> : null}
      </> : null}
      <section
        hidden={method !== "video"}
        className="upload-card"
        aria-labelledby="upload-heading"
        {...(config?.review_codes_enabled
          ? { id: "method-panel-video", role: "tabpanel" }
          : {})}
      >
        <h2 id="upload-heading">{t("submit.upload_heading")}</h2>
        <UploadFlow />
        <form onSubmit={onSubmit}>
          {file ? (
            <ChosenVideo file={file} onClear={clearFile} />
          ) : (
            <DropZone inputRef={fileInput} limits={videoLimits(config, t)} onChoose={chooseFile} />
          )}
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
          {!config?.review_codes_enabled && config?.turnstile_site_key ? <div id="turnstile-slot" /> : null}
          {error ? <p className="error">{error}</p> : null}
          {config?.payment_required && config.price_cents > 0 ? (
            <>
              <p className="note">{t("submit.one_time")}</p>
              <p className="note">{t("submit.terms")}</p>
            </>
          ) : null}
          <VoucherField voucher={voucher} disabled={busy} />
          <button
            type="submit"
            disabled={
              busy
              || voucher.checking
              || (config?.payment_required && config.price_cents == null)
              || !submitReady({ file })
            }
          >
            {busy
              ? t("submit.submitting")
              : submitButtonLabel(t, config, { accepted: voucher.accepted })}
          </button>
          <UploadFacts config={config} />
        </form>
      </section>
      <p className="unofficial">{t("legal.unofficial")}</p>
    </main>
  );
}
