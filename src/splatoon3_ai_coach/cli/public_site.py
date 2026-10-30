"""The `s3-coach public-site` command."""

from __future__ import annotations

import webbrowser
from datetime import UTC, datetime

import typer
from loguru import logger

from splatoon3_ai_coach.cli.repo_tools import require_repo_tools

public_admin_app = typer.Typer(no_args_is_help=True, help="Manage public-site admins.")
voucher_app = typer.Typer(no_args_is_help=True, help="Issue and revoke vouchers.")
public_admin_app.add_typer(voucher_app, name="voucher")


def public_site(
    port: int = typer.Option(8770, "--port", help="Port. Host defaults to 127.0.0.1."),
    no_open: bool = typer.Option(False, "--no-open", help="Do not open a browser."),
) -> None:
    """Start the public coaching site. This is not the local analysis platform."""
    try:
        import uvicorn
    except ImportError as exc:
        raise typer.BadParameter(
            "public-site requires the web extra: pip install -e '.[web]'"
        ) from exc

    require_repo_tools("public-site")
    from public_site.app import create_app
    from public_site.settings import PublicSettings
    from public_site.turnstile import build_verifier

    settings = PublicSettings.from_env()
    if not settings.turnstile_secret and not settings.dev_mode:
        raise typer.BadParameter("PUBLIC_TURNSTILE_SECRET is required")
    if settings.storage == "r2" and not settings.r2_ready:
        raise typer.BadParameter("R2 credentials are required when PUBLIC_STORAGE=r2")
    try:
        build_verifier(settings)
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not (settings.static_dir / "index.html").is_file():
        logger.warning("React build is missing at {}", settings.static_dir)
    app = create_app(settings, start_worker=True)
    url = f"http://{settings.host}:{port}/"
    if not no_open:
        webbrowser.open(url)
    uvicorn.run(app, host=settings.host, port=port, access_log=False)


def _set_admin_role(email: str, role: str) -> None:
    """Set one role and write its CLI audit record."""
    require_repo_tools("public-admin")
    from public_site.accounts import AccountStore
    from public_site.settings import PublicSettings
    from public_site.telemetry import TelemetryStore

    settings = PublicSettings.from_env()
    if not settings.database_url:
        raise typer.BadParameter("PUBLIC_DATABASE_URL is required")
    accounts = AccountStore(settings.database_url)
    accounts.ensure_schema()
    telemetry = TelemetryStore(settings.database_url)
    telemetry.ensure_schema()
    try:
        user_id, before, after = accounts.set_role(email, role)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    telemetry.audit_role(
        target_id=user_id,
        before=before,  # type: ignore[arg-type]
        after=after,  # type: ignore[arg-type]
    )
    typer.echo(f"{user_id}: {before} -> {after}")


@public_admin_app.command("grant")
def grant_admin(email: str) -> None:
    """Grant the administrator role to a verified account."""
    _set_admin_role(email, "admin")


@public_admin_app.command("revoke")
def revoke_admin(email: str) -> None:
    """Return an administrator to the normal user role."""
    _set_admin_role(email, "user")


@public_admin_app.command("backfill")
def backfill_telemetry() -> None:
    """Rewrite safe telemetry snapshots from retained analysis directories."""
    require_repo_tools("public-admin")
    from public_site.accounts import AccountStore
    from public_site.backfill import backfill_store
    from public_site.settings import PublicSettings
    from public_site.store import PublicStore
    from public_site.telemetry import TelemetryStore

    settings = PublicSettings.from_env()
    if not settings.database_url:
        raise typer.BadParameter("PUBLIC_DATABASE_URL is required")
    AccountStore(settings.database_url).ensure_schema()
    telemetry = TelemetryStore(settings.database_url)
    telemetry.ensure_schema()
    counts = backfill_store(PublicStore(settings.root), telemetry)
    typer.echo(
        "updated={updated} skipped_expired={skipped_expired} "
        "skipped_missing={skipped_missing}".format(**counts)
    )


@voucher_app.command("issue")
def issue_voucher(
    source: str = typer.Option(..., "--source", help="skilltown or youtube."),
    campaign: str = typer.Option(..., "--campaign", help="Reporting id, not the code."),
    expires_at: str = typer.Option(..., "--expires-at", help="Timezone-aware expiry."),
    service: str = typer.Option("match_analysis", "--service"),
    max_redemptions: int | None = typer.Option(None, "--max-redemptions"),
    code: str | None = typer.Option(None, "--code", help="Public YouTube code."),
    benefit: str | None = typer.Option(None, "--benefit"),
    starts_at: str | None = typer.Option(None, "--starts-at"),
    note: str | None = typer.Option(None, "--note"),
) -> None:
    """Create one voucher and print its plaintext code once."""
    require_repo_tools("public-admin")
    from public_site.errors import RequestRejected
    from public_site.vouchers import VoucherIssue, default_benefit

    store = _vouchers()
    try:
        chosen = benefit or default_benefit(source)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    limit = 1 if max_redemptions is None and source == "skilltown" else max_redemptions
    if limit is None:
        raise typer.BadParameter("YouTube vouchers need --max-redemptions")
    spec = VoucherIssue(
        source=source,
        campaign=campaign,
        eligible_service_id=service,
        benefit_type=chosen,
        max_redemptions=limit,
        expires_at=_aware(expires_at, "expiration"),
        code=code,
        starts_at=None if starts_at is None else _aware(starts_at, "start"),
        note=note,
    )
    try:
        issued = store.issue(spec, datetime.now(UTC))
    except (ValueError, RequestRejected) as exc:
        detail = exc.detail if isinstance(exc, RequestRejected) else str(exc)
        raise typer.BadParameter(detail) from exc
    typer.echo(f"voucher_id={issued.voucher_id}")
    typer.echo(f"source={spec.source} campaign={spec.campaign}")
    typer.echo(f"service={spec.eligible_service_id} benefit={spec.benefit_type}")
    typer.echo(f"max_redemptions={spec.max_redemptions}")
    typer.echo(f"expires_at={spec.expires_at.isoformat()}")
    typer.echo("The next line is the only copy of this code. It is not stored.")
    typer.echo(f"code={issued.code}")


@voucher_app.command("revoke")
def revoke_voucher(voucher_id: str = typer.Argument(...)) -> None:
    """Revoke one voucher by id. Queued jobs are left as they are."""
    if not _vouchers().revoke(voucher_id):
        raise typer.BadParameter("voucher was not active")
    typer.echo(f"revoked {voucher_id}")


@voucher_app.command("status")
def voucher_status(voucher_id: str = typer.Argument(...)) -> None:
    """Show redemptions for one voucher. The code itself cannot be shown."""
    found = _vouchers().status(voucher_id)
    if found is None:
        raise typer.BadParameter("voucher was not found")
    typer.echo(f"voucher_id={found.voucher_id} status={found.status}")
    typer.echo(f"source={found.source} campaign={found.campaign}")
    typer.echo(f"service={found.eligible_service_id} benefit={found.benefit_type}")
    typer.echo(
        f"redemptions={found.redemption_count}/{found.max_redemptions} "
        f"expires_at={found.expires_at.isoformat()}"
    )
    if found.note:
        typer.echo(f"note={found.note}")
    for item in found.redemptions:
        redeemed = item.redeemed_at.isoformat()
        typer.echo(f"submission={item.submission_id} redeemed_at={redeemed}")


def _vouchers():
    """Open the voucher store. A missing HMAC secret fails here."""
    require_repo_tools("public-admin")
    from public_site.settings import PublicSettings
    from public_site.vouchers import VoucherStore

    settings = PublicSettings.from_env()
    if not settings.database_url:
        raise typer.BadParameter("PUBLIC_DATABASE_URL is required")
    if not settings.voucher_hmac_secret:
        raise typer.BadParameter("PUBLIC_VOUCHER_HMAC_SECRET is required")
    store = VoucherStore(settings.database_url, settings.voucher_hmac_secret)
    store.ensure_schema()
    return store


def _aware(value: str, label: str) -> datetime:
    """Parse one timezone-aware timestamp. Naive values are rejected."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise typer.BadParameter(f"{label} must include a time zone")
    return parsed
