"""Shared helpers for coach-inputs and coach-prototype CLIs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TextIO

from splatoon3_ai_coach.coach.coach import annotate_claim_flags
from splatoon3_ai_coach.coach.llm_client import CoachingAssessment

COACH_INPUTS_DIRNAME = "coach_inputs"
COACH_INPUTS_META_FILENAME = "coach_inputs_meta.json"
COACHING_INDEX_FILENAME = "coaching_index.json"


def safe_filename(value: str) -> str:
    """Filesystem-safe token for scenario/model names."""
    return re.sub(r"[^\w.\-]+", "_", value)


def null_display(value: str | None) -> str:
    """Render optional text for markdown summaries."""
    return "null" if value is None else value


def write_json(path: Path, payload: Any) -> None:
    """Write pretty-printed JSON with a trailing newline."""
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_json(path: Path) -> Any:
    """Load a JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def default_coach_inputs_dir(analysis_dir: Path) -> Path:
    """Default directory for generated CoachInput artifacts."""
    return (analysis_dir / COACH_INPUTS_DIRNAME).resolve()


def resolve_coach_inputs_dir(
    analysis_dir: Path,
    inputs: Path | None,
) -> Path:
    """Resolve CoachInput directory; require it to exist for LLM stage."""
    path = inputs.resolve() if inputs is not None else default_coach_inputs_dir(analysis_dir)
    if not path.is_dir():
        raise FileNotFoundError(
            f"Coach inputs directory not found: {path}. "
            f"Run `s3-coach coach-inputs {analysis_dir}` first."
        )
    return path


def write_assessment_artifacts(
    *,
    out_dir: Path,
    safe_id: str,
    model_label: str,
    assessment: CoachingAssessment,
    raw_text: str,
) -> None:
    """Write output/raw/flags for a deterministic or parsed assessment."""
    model_tag = safe_filename(model_label)
    out_path = out_dir / f"{safe_id}.{model_tag}.output.json"
    raw_path = out_dir / f"{safe_id}.{model_tag}.raw.txt"
    flags_path = out_dir / f"{safe_id}.{model_tag}.flags.json"
    write_json(out_path, assessment.model_dump(mode="json"))
    raw_path.write_text(raw_text + "\n", encoding="utf-8")
    flags = annotate_claim_flags(assessment)
    write_json(flags_path, flags)


def write_metric(file: TextIO, record: dict[str, Any]) -> None:
    """Append one JSONL metrics record."""
    file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    file.flush()
