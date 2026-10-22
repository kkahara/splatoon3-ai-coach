"""Downstream and upstream stages never import the capture boundary."""

import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "splatoon3_ai_coach"
_CAPTURE_IMPORT = re.compile(
    r"^\s*(from|import)\s+(splatoon3_ai_coach\.capture|\.+capture)\b", re.M
)


@pytest.mark.parametrize(
    "stage", ["media", "vision", "review", "analysis", "coach", "alignment"]
)
def test_stage_does_not_import_capture(stage: str) -> None:
    offenders = [
        path.relative_to(PACKAGE).as_posix()
        for path in (PACKAGE / stage).rglob("*.py")
        if _CAPTURE_IMPORT.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
