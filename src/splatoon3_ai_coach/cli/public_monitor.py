"""The `s3-coach public-monitor` command."""

from __future__ import annotations

import typer

from splatoon3_ai_coach.cli.repo_tools import require_repo_tools


def public_monitor() -> None:
    """Print public submission status. Omits review tokens and email addresses."""
    require_repo_tools("public-monitor")
    from public_site.monitor import render_monitor
    from public_site.settings import PublicSettings

    typer.echo(render_monitor(PublicSettings.from_env()), nl=False)
