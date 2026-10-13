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
} from "../api.js";
import heroBanner from "../assets/splatoon3-ai-coach-banner.jpg";
import { useT } from "../i18n/LocaleContext.jsx";
import {
  formatReviewCode,
  rememberedGameLanguage,
  rememberGameLanguage,
  reviewCodeComplete,
  submissionBody,
  submitButtonLabel,
  submitError,
  submitReady,
} from "./submitForm.js";

const REGULAR_MAX_BYTES = 750 * 1024 * 1024;

function videoLabel(config, t) {
  if (config && config.max_video_bytes == null) {
    return t("submit.choose_video_any");
  }
  const bytes = config?.max_video_bytes || REGULAR_MAX_BYTES;
  const mb = Math.round(bytes / (1024 * 1024));
  return t("submit.choose_video", { mb });
}

export function SubmitPage() {
  const navigate = useNavigate();
  const t = useT();
  const [config, setConfig] = useState(null);
  const [file, setFile] = useState(null);
  // No browser-locale default: it selects HUD templates and must match the video.
  const [language, setLanguage] = useState(rememberedGameLanguage);
  const [reviewCode, setReviewCode] = useState("");
  const fileInput = useRef(null);
  const [account, setAccount] = useState(null);
  const [notify, setNotify] = useState(false);
  const [notifyTouched, setNotifyTouched] = useState(false);
  const [email, setEmail] = useState("");
  const [turnstileToken, setTurnstileToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

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

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    if (!file && reviewCodeComplete(reviewCode)) {
      setError(t("submit.review_code_unavailable"));
      return;
    }
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
        submissionBody({ token, file, language, account, notify, email }),
      );
      await uploadVideo(created.upload, file);
      await markUploaded(created.token);
      if (created.payment_required) {
        const checkout = await openCheckout(created.token);
        if (checkout.url) {
          window.location.assign(checkout.url);
          return;
        }
      }
      navigate(created.review_path);
    } catch (exc) {
      setError(exc.message);
      setBusy(false);
    }
  }

  return (
    <main className="sheet">
      <AccountBar account={account} onChange={setAccount} />
      <div className="hero">
        <img className="hero-banner" src={heroBanner} alt="" />
        <h1>{t("submit.title")}</h1>
      </div>
      <p>{t("submit.intro")}</p>
      <form onSubmit={onSubmit}>
        <label>
          {videoLabel(config, t)}
          <span className="file-row">
            <input
              ref={fileInput}
              type="file"
              accept=".mp4,.mov,video/mp4,video/quicktime"
              onChange={(event) => {
                const next = event.target.files?.[0] || null;
                setFile(next);
                if (next) {
                  setReviewCode("");
                }
                setError("");
              }}
            />
            {file ? (
              <button type="button" className="file-cancel" onClick={clearFile}>
                {t("submit.cancel_file")}
              </button>
            ) : null}
          </span>
        </label>
        <label>
          {t("submit.review_code")}
          <input
            value={reviewCode}
            placeholder="XXXX-XXXX-XXXX-XXXX"
            autoCapitalize="characters"
            autoCorrect="off"
            spellCheck={false}
            maxLength={19}
            disabled
            onChange={(event) => {
              setReviewCode(formatReviewCode(event.target.value));
              setError("");
            }}
          />
        </label>
        <label>
          {t("submit.system_language")}
          <select
            value={language}
            onChange={(event) => {
              const next = event.target.value;
              setLanguage(next);
              rememberGameLanguage(next);
            }}
            required
          >
            <option value="" disabled>
              {t("submit.choose_language")}
            </option>
            <option value="en">English</option>
            <option value="ja">日本語</option>
          </select>
        </label>
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
        <button
          type="submit"
          disabled={
            busy
            || (config?.payment_required && config.price_cents == null)
            || !submitReady({ file, language, reviewCode })
          }
        >
          {busy ? t("submit.submitting") : submitButtonLabel(t, config)}
        </button>
      </form>
      <p className="note">{t("submit.note")}</p>
    </main>
  );
}
