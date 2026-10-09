"""The `s3-coach vmv-site` command."""

from __future__ import annotations

import webbrowser

import typer
from loguru import logger

from splatoon3_ai_coach.cli.repo_tools import require_repo_tools


def vmv_site(
    port: int = typer.Option(8765, "--port", help="Port on 127.0.0.1."),
    no_open: bool = typer.Option(False, "--no-open", help="Do not open a browser."),
) -> None:
    """Start the local analysis platform and the React admin UI."""
    try:
        import uvicorn
    except ImportError as exc:
        raise typer.BadParameter(
            "vmv-site requires the web extra: pip install -e '.[web]'"
        ) from exc

    require_repo_tools("vmv-site")
    from vmv_site.app import create_app
    from vmv_site.settings import PlatformSettings

    settings = PlatformSettings.from_env()
    if not (settings.static_dir / "index.html").is_file():
        logger.warning("React build is missing at {}", settings.static_dir)
    app = create_app(settings, start_worker=True)
    url = f"http://127.0.0.1:{port}/"
    if not no_open:
        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=port)
