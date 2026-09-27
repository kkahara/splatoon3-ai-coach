import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createSubmission, loadConfig, markUploaded, uploadVideo } from "../api.js";

export function SubmitPage() {
  const navigate = useNavigate();
  const [config, setConfig] = useState(null);
  const [file, setFile] = useState(null);
  const [language, setLanguage] = useState("en");
  const [displayName, setDisplayName] = useState("");
  const [notify, setNotify] = useState(false);
  const [email, setEmail] = useState("");
  const [turnstileToken, setTurnstileToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    loadConfig().then(setConfig).catch((exc) => setError(exc.message));
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
      setError("Choose an MP4 or MOV video.");
      return;
    }
    if (config && file.size > config.max_video_bytes) {
      setError("That video is too large.");
      return;
    }
    const token = config?.dev_mode ? "dev" : turnstileToken;
    if (!token) {
      setError("Complete the check before submitting.");
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
        email: notify ? email : null,
        notify,
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
      <p className="brand">Splatoon 3 AI Coach</p>
      <h1>Get AI Coaching for Your Splatoon 3 Match</h1>
      <p>Upload your gameplay recording.</p>
      <form onSubmit={onSubmit}>
        <label>
          Choose video
          <input
            type="file"
            accept=".mp4,.mov,video/mp4,video/quicktime"
            onChange={(event) => setFile(event.target.files?.[0] || null)}
          />
        </label>
        <label>
          Language
          <select value={language} onChange={(event) => setLanguage(event.target.value)}>
            <option value="en">English</option>
            <option value="ja">日本語</option>
          </select>
        </label>
        <label>
          Display name
          <input
            value={displayName}
            maxLength={40}
            onChange={(event) => setDisplayName(event.target.value)}
            placeholder="Optional"
          />
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={notify}
            onChange={(event) => setNotify(event.target.checked)}
          />
          Email me when my review is ready
        </label>
        {notify ? (
          <label>
            Email address
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
          {busy ? "Submitting…" : "Submit Match"}
        </button>
      </form>
      <p className="note">
        Currently, video recordings are supported. Review Code support may be added in the
        future.
      </p>
    </main>
  );
}
