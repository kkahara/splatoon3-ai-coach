"""The installed CLI must not depend on the repo-root ``tools/`` directory."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from splatoon3_ai_coach.cli import repo_tools
from splatoon3_ai_coach.cli.app import app
from splatoon3_ai_coach.cli.repo_tools import require_repo_tools

TOOLS_MODULES = ("vision_manifest_viewer", "vmv_site", "public_site")

_PROBE = """
import sys
import splatoon3_ai_coach.cli.app  # noqa: F401
from splatoon3_ai_coach.cli.repo_tools import REPO_TOOLS_DIR
loaded = [m for m in {mods!r} if m in sys.modules]
on_path = str(REPO_TOOLS_DIR) in sys.path
print(loaded, on_path)
"""


def test_importing_the_cli_does_not_touch_tools(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE.format(mods=TOOLS_MODULES)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.strip() == "[] False"


@pytest.mark.parametrize(
    "argv",
    [["--help"], ["analyze", "--help"], ["coach-inputs", "--help"],
     ["coach-prototype", "--help"]],
)
def test_core_help_commands_run(argv: list[str]) -> None:
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 0, result.output
    assert "Usage" in result.output


def test_require_repo_tools_exits_cleanly_without_tools(tmp_path: Path) -> None:
    with pytest.raises(typer.Exit) as excinfo:
        require_repo_tools("vision-view", tools_dir=tmp_path / "missing")
    assert excinfo.value.exit_code == 2


def test_vision_view_reports_missing_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repo_tools, "REPO_TOOLS_DIR", tmp_path / "missing")
    result = CliRunner().invoke(app, ["vision-view", "x.json", "--no-open"])
    assert result.exit_code == 2
    assert "source checkout" in result.output


def test_require_repo_tools_adds_checkout_tools_to_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", list(sys.path))
    require_repo_tools("vision-view", tools_dir=tmp_path)
    assert sys.path[0] == str(tmp_path)
