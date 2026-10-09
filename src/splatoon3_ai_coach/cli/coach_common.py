"""Shared helpers for coach-inputs and coach-prototype CLIs.

Artifact filenames, schemas, and readers live in :mod:`coach.artifacts`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TextIO

from splatoon3_ai_coach.coach.artifacts import coach_inputs_dir


def null_display(value: str | None) -> str:
    """Render optional text for markdown summaries."""
    return "null" if value is None else value


def default_coach_inputs_dir(analysis_dir: Path) -> Path:
    """Default directory for generated CoachInput artifacts."""
    return coach_inputs_dir(analysis_dir).resolve()


def resolve_coach_inputs_dir(
    analysis_dir: Path,
    inputs: Path | None,
) -> Path:
    """Resolve CoachInput directory; require it to exist for LLM stage."""
    path = (
        inputs.resolve() if inputs is not None else default_coach_inputs_dir(analysis_dir)
    )
    if not path.is_dir():
        raise FileNotFoundError(
            f"Coach inputs directory not found: {path}. "
            f"Run `s3-coach coach-inputs {analysis_dir}` first."
        )
    return path


def write_metric(file: TextIO, record: dict[str, Any]) -> None:
    """Append one JSONL metrics record."""
    file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    file.flush()
