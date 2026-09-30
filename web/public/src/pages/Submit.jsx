import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AccountBar } from "../AccountBar.jsx";
import { createSubmission, loadAccount, loadConfig, markUploaded, uploadVideo } from "../api.js";
import { useT } from "../i18n/LocaleContext.jsx";

function systemLanguage() {
  const tags = navigator.languages?.length ? navigator.languages : [navigator.language || "en"];
  return tags.some((tag) => String(tag).toLowerCase().startsWith("ja")) ? "ja" : "en";
}

function videoLabel(config, t) {
  const bytes = config?.max_video_bytes || 500 * 1024 * 1024;
  const mb = Math.round(bytes / (1024 * 1024));
  return t("submit.choose_video", { mb });
}

export function SubmitPage() {
  const navigate = useNavigate();
  const t = useT();
  const [config, setConfig] = useState(null);
  const [file, setFile] = useState(null);
  const [language, setLanguage] = useState(systemLanguage());
  const [displayName, setDisplayName] = useState("");
  const [account, setAccount] = useState(null);
  const [notify, setNotify] = useState(false);
  const [email, setEmail] = useState("");
  const [turnstileToken, setTurnstileToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    loadConfig().then(setConfig).catch((exc) => setError(exc.message));
    loadAccount()
      .then((next) => {
        setAccount(next);
        if (next?.name) {
          setDisplayName((current) => current || next.name);
        }
      })
      .catch((exc) => setError(exc.message));
  }, []);

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
  }, [config]);

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    if (!file) {
      setError(t("submit.error_file"));
      return;
    }
    if (config && file.size > config.max_video_bytes) {
      setError(t("submit.error_size"));
      return;
    }
    const token = config?.dev_mode ? "dev" : turnstileToken;
    if (!token) {
      setError(t("submit.error_check"));
      return;
    }
    setBusy(true);
    try {
      const created = await createSubmission({
        turnstile_token: token,
        filename: file.name,
        size_bytes: file.size,
        content_type: file.type || "video/mp4",
        language,
        email: account ? null : notify ? email : null,
        notify: account ? false : notify,
        display_name: displayName || null,
      });
      await uploadVideo(created.upload, file);
      await markUploaded(created.token);
      navigate(created.review_path);
    } catch (exc) {
      setError(exc.message);
      setBusy(false);
    }
  }

  return (
    <main className="sheet">
      <AccountBar account={account} onChange={setAccount} />
      <h1>{t("submit.title")}</h1>
      <p>{t("submit.intro")}</p>
      <form onSubmit={onSubmit}>
        <label>
          {videoLabel(config, t)}
          <input
            type="file"
            accept=".mp4,.mov,video/mp4,video/quicktime"
            onChange={(event) => setFile(event.target.files?.[0] || null)}
          />
        </label>
        <label>
          {t("submit.system_language")}
          <select value={language} onChange={(event) => setLanguage(event.target.value)}>
            <option value="en">English</option>
            <option value="ja">日本語</option>
          </select>
        </label>
        <label>
          {t("submit.display_name")}
          <input
            value={displayName}
            maxLength={40}
            onChange={(event) => setDisplayName(event.target.value)}
            placeholder={t("submit.optional")}
          />
        </label>
        {account ? null : (
          <label className="check">
            <input
              type="checkbox"
              checked={notify}
              onChange={(event) => setNotify(event.target.checked)}
            />
            {t("submit.notify")}
          </label>
        )}
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
        <button type="submit" disabled={busy}>
          {busy ? t("submit.submitting") : t("submit.submit")}
        </button>
      </form>
      <p className="note">{t("submit.note")}</p>
    </main>
  );
}
