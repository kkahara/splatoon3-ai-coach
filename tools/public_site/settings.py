"""Settings for the public coaching site. Analysis YAML stays in configs/."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from splatoon3_ai_coach.config.paths import PROJECT_ROOT, default_config_path

_MIB = 1024 * 1024


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


_legacy_capacity_warned = False


def _worker_capacity() -> int:
    """``PUBLIC_WORKER_CAPACITY``, else the deprecated ``PUBLIC_MAX_CONCURRENT``, else 1."""
    global _legacy_capacity_warned
    value = os.environ.get("PUBLIC_WORKER_CAPACITY", "").strip()
    if value:
        return int(value)
    legacy = os.environ.get("PUBLIC_MAX_CONCURRENT", "").strip()
    if not legacy:
        return 1
    if not _legacy_capacity_warned:
        _legacy_capacity_warned = True
        logger.warning("PUBLIC_MAX_CONCURRENT is deprecated; set PUBLIC_WORKER_CAPACITY instead")
    return int(legacy)


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
    max_video_bytes: int = 750 * _MIB
    max_duration_seconds: float = 1800
    min_dimension: int = 320
    max_dimension: int = 3840
    submissions_per_hour: int = 6
    guest_submissions_per_hour: int = 2
    account_submissions_per_hour: int = 5
    submission_window_seconds: int = 3600
    accounts_per_ip: int = 3
    max_queued: int = 20
    # This worker's capacity, not a site-wide limit. Postgres overrides it per worker_key.
    max_concurrent_jobs: int = 1
    # Configuration identity a capacity is stored under. Not a process id; see worker_id.
    worker_key: str = "default"
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
    admin_slow_seconds: int = 600
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    voucher_hmac_secret: str = ""
    payment_hold_seconds: int = 24 * 60 * 60
    contact_to: str = ""
    contact_messages_per_hour: int = 5

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
        # Upload caps are the same on localhost and production. Dev mode only
        # relaxes how many accounts one network may register.
        relaxed = dev_mode and per_hour is None and window is None
        submissions = int(per_hour or "6")
        window_seconds = int(window or "3600")
        return cls(
            root=root.resolve(),
            config_path=default_config_path(),
            static_dir=PROJECT_ROOT / "web" / "public" / "dist",
            host=os.environ.get("PUBLIC_HOST", "127.0.0.1"),
            port=int(os.environ.get("PUBLIC_PORT", "8770")),
            storage=os.environ.get("PUBLIC_STORAGE", "local"),
            max_video_bytes=int(os.environ.get("PUBLIC_MAX_VIDEO_BYTES", str(750 * _MIB))),
            max_duration_seconds=float(os.environ.get("PUBLIC_MAX_DURATION_SECONDS", "1800")),
            submissions_per_hour=submissions,
            guest_submissions_per_hour=int(
                os.environ.get("PUBLIC_GUEST_VIDEO_PER_HOUR", "2")
            ),
            account_submissions_per_hour=int(
                os.environ.get("PUBLIC_ACCOUNT_VIDEO_PER_HOUR", "5")
            ),
            submission_window_seconds=window_seconds,
            accounts_per_ip=int(
                os.environ.get("PUBLIC_ACCOUNTS_PER_IP", "50" if relaxed else "3")
            ),
            max_queued=int(os.environ.get("PUBLIC_MAX_QUEUED", "20")),
            max_concurrent_jobs=_worker_capacity(),
            worker_key=os.environ.get("PUBLIC_WORKER_KEY", "").strip() or "default",
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
            admin_slow_seconds=int(os.environ.get("PUBLIC_ADMIN_SLOW_SECONDS", "600")),
            stripe_secret_key=os.environ.get("PUBLIC_STRIPE_SECRET_KEY", "").strip(),
            stripe_webhook_secret=os.environ.get("PUBLIC_STRIPE_WEBHOOK_SECRET", "").strip(),
            voucher_hmac_secret=os.environ.get("PUBLIC_VOUCHER_HMAC_SECRET", "").strip(),
            payment_hold_seconds=int(os.environ.get("PUBLIC_PAYMENT_HOLD_SECONDS", str(24 * 60 * 60))),
            contact_to=os.environ.get("PUBLIC_CONTACT_TO", "").strip(),
            contact_messages_per_hour=int(os.environ.get("PUBLIC_CONTACT_PER_HOUR", "5")),
        )
