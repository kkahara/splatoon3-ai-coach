"""Read coaching artifacts into the public result. Prompts stay on disk."""

from __future__ import annotations

import json
from pathlib import Path

from public_site.frames import has_frame, ranked_entries
from public_site.models import CoachingMoment, PublicSubmissionResult
from splatoon3_ai_coach.cli.coach_common import COACH_INPUTS_DIRNAME
from splatoon3_ai_coach.coach.llm_runs import empty_coaching_assessment

_SKIP = empty_coaching_assessment().assessment


def load_result(analysis_dir: Path) -> PublicSubmissionResult:
    """Ranked moments with active player statements and optional assessment prose."""
    inputs = analysis_dir / COACH_INPUTS_DIRNAME
    prototype = analysis_dir / "coach_prototype"
    moments = [
        _moment(analysis_dir, inputs, prototype, entry)
        for entry in ranked_entries(analysis_dir)
    ]
    return PublicSubmissionResult(moments=moments)


def _moment(analysis: Path, inputs: Path, prototype: Path, entry: dict) -> CoachingMoment:
    coaching_name = str(entry.get("coaching_json") or "")
    coaching = _read_json(inputs / coaching_name) if coaching_name else {}
    statements = [
        str(factor["statement_player"])
        for factor in coaching.get("factors") or []
        if factor.get("active") and factor.get("statement_player")
    ]
    safe_id = str(entry.get("safe_id") or "")
    return CoachingMoment(
        scenario_type=str(entry.get("candidate_type") or ""),
        video_time=float(entry.get("video_time") or 0),
        statements=statements,
        assessment=_assessment(prototype, safe_id),
        frame=has_frame(analysis, safe_id),
    )


def _assessment(prototype: Path, safe_id: str) -> str | None:
    if not safe_id or not prototype.is_dir():
        return None
    prefix = f"{safe_id}."
    suffix = ".output.json"
    names = sorted(
        path.name
        for path in prototype.iterdir()
        if path.name.startswith(prefix) and path.name.endswith(suffix)
    )
    prose = [_prose(prototype / name) for name in names]
    kept = [text for text in prose if text]
    return kept[-1] if kept else None


def _prose(path: Path) -> str | None:
    text = str(_read_json(path).get("assessment") or "").strip()
    if not text or text == _SKIP:
        return None
    return text


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}
