"""The `s3-coach public-site` command."""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

import typer
from loguru import logger

_TOOLS = Path(__file__).resolve().parents[3] / "tools"
if _TOOLS.is_dir() and str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))


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
