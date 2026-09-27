"""The `s3-coach public-monitor` command."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

_TOOLS = Path(__file__).resolve().parents[3] / "tools"
if _TOOLS.is_dir() and str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))


def public_monitor() -> None:
    """Print public submission status. Omits review tokens and email addresses."""
    from public_site.monitor import render_monitor
    from public_site.settings import PublicSettings

    typer.echo(render_monitor(PublicSettings.from_env()), nl=False)
