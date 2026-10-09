"""Locate the repo-root ``tools/`` packages for checkout-only CLI commands.

``tools/`` is not part of the installed wheel. Commands backed by it call
:func:`require_repo_tools` inside the command body so ``s3-coach --help`` and
the core pipeline commands never import or require it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

REPO_TOOLS_DIR = Path(__file__).resolve().parents[3] / "tools"


def require_repo_tools(command: str, *, tools_dir: Path | None = None) -> None:
    """Put the repo ``tools/`` directory on ``sys.path`` or exit with a clear error.

    Args:
        command: CLI command name, used in the error message.
        tools_dir: Override for tests; defaults to the checkout's ``tools/``.

    Raises:
        typer.Exit: When ``tools/`` is absent (e.g. an installed wheel).
    """
    root = tools_dir or REPO_TOOLS_DIR
    if not root.is_dir():
        typer.echo(
            f"`s3-coach {command}` needs the repo-root tools/ directory and only "
            "runs from a source checkout (pip install -e .).",
            err=True,
        )
        raise typer.Exit(code=2)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
