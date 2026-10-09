"""The `s3-coach public-site` command."""

from __future__ import annotations

import webbrowser

import typer
from loguru import logger

from splatoon3_ai_coach.cli.repo_tools import require_repo_tools

public_admin_app = typer.Typer(no_args_is_help=True, help="Manage public-site admins.")


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
