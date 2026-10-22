"""Match facts for the past-coaching list. Read from existing analysis files only.

Mode and stage are the ids the pipeline latched from the pre-match intro and
wrote to ``match_identity.json``; nothing is inferred here. The moment count is
the number of moments the review page lists (key and other detected moments).
"""

from __future__ import annotations

import json
from pathlib import Path

from public_site.frames import ranked_entries
from public_site.models import Submission
from splatoon3_ai_coach.vision.stage_maps import MATCH_IDENTITY_FILENAME


def match_identity(analysis_dir: str | None) -> tuple[str | None, str | None]:
    """``(battle_mode_id, stage_id)`` as stored, ``None`` for anything missing."""
    if not analysis_dir:
        return None, None
    path = Path(analysis_dir) / MATCH_IDENTITY_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    return _text(data.get("battle_mode_id")), _text(data.get("stage_id"))


def moment_count(submission: Submission) -> int | None:
    """Moments the completed review lists; ``None`` until the analysis is complete."""
    if submission.status != "complete":
        return None
    if submission.outcome == "no_deaths" or not submission.analysis_dir:
        return 0
    return len(ranked_entries(Path(submission.analysis_dir)))


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
