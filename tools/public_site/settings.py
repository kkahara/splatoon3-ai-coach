"""Settings for the public coaching site. Analysis YAML stays in configs/."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from splatoon3_ai_coach.config.paths import PROJECT_ROOT, default_config_path

_MIB = 1024 * 1024


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


def _load_env() -> None:
    """Load the repo ``.env`` without overriding variables already set."""
    path = PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    from dotenv import load_dotenv

    load_dotenv(path, override=False)


@dataclass(frozen=True)
class PublicSettings:
    """Locations, limits, and optional Cloudflare credentials."""

    root: Path
    config_path: Path
    static_dir: Path
    host: str = "127.0.0.1"
    port: int = 8770
    storage: str = "local"
    max_video_bytes: int = 500 * _MIB
    max_duration_seconds: float = 1800
    min_dimension: int = 320
    max_dimension: int = 3840
    submissions_per_hour: int = 6
    guest_submissions_per_hour: int = 2
    account_submissions_per_hour: int = 2
    submission_window_seconds: int = 3600
    accounts_per_ip: int = 3
    max_queued: int = 20
    max_concurrent: int = 1
    retention_days: int = 30
    account_retention_days: int = 365
    database_url: str = ""
    upload_url_seconds: int = 900
    turnstile_secret: str | None = None
    turnstile_site_key: str | None = None
    dev_mode: bool = False
    public_base_url: str = "http://127.0.0.1:8770"
    trust_proxy: bool = False
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    worker_id: str = ""
    llm_provider: str = ""

    @property
    def r2_ready(self) -> bool:
        """True when the four R2 settings needed for presigned uploads are set."""
        return bool(
            self.r2_account_id
            and self.r2_access_key_id
            and self.r2_secret_access_key
            and self.r2_bucket
        )

    @classmethod
    def from_env(cls) -> PublicSettings:
        """Build settings from the repo layout and ``PUBLIC_*`` env vars."""
        _load_env()
        analysis = Path(
            os.environ.get("VMV_ANALYSIS_ROOT", PROJECT_ROOT / "analysis")
        ).expanduser()
        root = Path(os.environ.get("PUBLIC_ROOT", analysis / "_public")).expanduser()
        base = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8770").rstrip("/")
        dev_mode = _flag("PUBLIC_DEV_MODE")
        per_hour = os.environ.get("PUBLIC_VIDEO_PER_HOUR")
        window = os.environ.get("PUBLIC_VIDEO_WINDOW_SECONDS")
        relaxed = dev_mode and per_hour is None and window is None
        if relaxed:
            submissions, window_seconds = 50, 86400
        else:
            submissions = int(per_hour or "6")
            window_seconds = int(window or "3600")
        per_user_default = str(submissions) if relaxed else "2"
        return cls(
            root=root.resolve(),
            config_path=default_config_path(),
            static_dir=PROJECT_ROOT / "web" / "public" / "dist",
            host=os.environ.get("PUBLIC_HOST", "127.0.0.1"),
            port=int(os.environ.get("PUBLIC_PORT", "8770")),
            storage=os.environ.get("PUBLIC_STORAGE", "local"),
            max_video_bytes=int(os.environ.get("PUBLIC_MAX_VIDEO_BYTES", str(500 * _MIB))),
            max_duration_seconds=float(os.environ.get("PUBLIC_MAX_DURATION_SECONDS", "1800")),
            submissions_per_hour=submissions,
            guest_submissions_per_hour=int(
                os.environ.get("PUBLIC_GUEST_VIDEO_PER_HOUR", per_user_default)
            ),
            account_submissions_per_hour=int(
                os.environ.get("PUBLIC_ACCOUNT_VIDEO_PER_HOUR", per_user_default)
            ),
            submission_window_seconds=window_seconds,
            accounts_per_ip=int(
                os.environ.get("PUBLIC_ACCOUNTS_PER_IP", "50" if relaxed else "3")
            ),
            max_queued=int(os.environ.get("PUBLIC_MAX_QUEUED", "20")),
            max_concurrent=int(os.environ.get("PUBLIC_MAX_CONCURRENT", "1")),
            retention_days=int(os.environ.get("PUBLIC_RETENTION_DAYS", "30")),
            account_retention_days=int(os.environ.get("PUBLIC_ACCOUNT_RETENTION_DAYS", "365")),
            database_url=os.environ.get("PUBLIC_DATABASE_URL", "").strip(),
            turnstile_secret=os.environ.get("PUBLIC_TURNSTILE_SECRET") or None,
            turnstile_site_key=os.environ.get("PUBLIC_TURNSTILE_SITE_KEY") or None,
            dev_mode=dev_mode,
            public_base_url=base,
            trust_proxy=_flag("PUBLIC_TRUST_PROXY"),
            r2_account_id=os.environ.get("PUBLIC_R2_ACCOUNT_ID", ""),
            r2_access_key_id=os.environ.get("PUBLIC_R2_ACCESS_KEY_ID", ""),
            r2_secret_access_key=os.environ.get("PUBLIC_R2_SECRET_ACCESS_KEY", ""),
            r2_bucket=os.environ.get("PUBLIC_R2_BUCKET", ""),
            smtp_host=os.environ.get("PUBLIC_SMTP_HOST", ""),
            smtp_port=int(os.environ.get("PUBLIC_SMTP_PORT", "587")),
            smtp_user=os.environ.get("PUBLIC_SMTP_USER", ""),
            smtp_password=os.environ.get("PUBLIC_SMTP_PASSWORD", ""),
            smtp_from=os.environ.get("PUBLIC_SMTP_FROM", ""),
            worker_id=os.environ.get("PUBLIC_WORKER_ID") or uuid.uuid4().hex,
            llm_provider=os.environ.get("PUBLIC_LLM_PROVIDER", "").strip(),
        )
