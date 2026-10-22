"""Match facts on the past-coaching list come only from stored analysis files."""

from __future__ import annotations

import json
from pathlib import Path

from public_site.history import match_identity, moment_count
from public_site.models import Submission

from splatoon3_ai_coach.coach.artifacts import (
    CoachingIndexEntry,
    coach_inputs_dir,
    write_coaching_index,
)

STAMP = "2026-10-21T23:14:20Z"


def _submission(analysis: Path | None, **update: object) -> Submission:
    fields = {
        "id": "s1",
        "access_token_hash": "h",
        "status": "complete",
        "step": "ready",
        "created_at": STAMP,
        "updated_at": STAMP,
        "expires_at": STAMP,
        "analysis_dir": None if analysis is None else str(analysis),
        "outcome": "coached",
    }
    fields.update(update)
    return Submission(**fields)


def _entry(index: int) -> CoachingIndexEntry:
    return CoachingIndexEntry(
        scenario_id=f"sc{index}",
        candidate_id=f"c{index}",
        candidate_type="death_episode",
        safe_id=f"unit_{index}",
        video_time=10.0 * index,
        importance_score=1.0,
        rank=index,
        coach_input_json=f"unit_{index}.coach_input.json",
        coaching_json=f"unit_{index}.coaching.json",
    )


def _index(analysis: Path, entries: list[CoachingIndexEntry]) -> None:
    inputs = coach_inputs_dir(analysis)
    inputs.mkdir(parents=True, exist_ok=True)
    write_coaching_index(inputs, entries, analysis_id="a")


def _identity(analysis: Path, payload: object) -> None:
    analysis.mkdir(parents=True, exist_ok=True)
    (analysis / "match_identity.json").write_text(json.dumps(payload), encoding="utf-8")


def test_identity_reads_stored_mode_and_stage(tmp_path: Path) -> None:
    _identity(
        tmp_path, {"battle_mode_id": "splat_zones", "stage_id": "hagglefish_market"}
    )
    assert match_identity(str(tmp_path)) == ("splat_zones", "hagglefish_market")


def test_identity_keeps_a_mode_without_a_stage(tmp_path: Path) -> None:
    _identity(tmp_path, {"battle_mode_id": "clam_blitz", "stage_id": None})
    assert match_identity(str(tmp_path)) == ("clam_blitz", None)


def test_identity_missing_or_unreadable_is_none(tmp_path: Path) -> None:
    assert match_identity(None) == (None, None)
    assert match_identity(str(tmp_path)) == (None, None)
    (tmp_path / "match_identity.json").write_text("{not json", encoding="utf-8")
    assert match_identity(str(tmp_path)) == (None, None)
    _identity(tmp_path, ["splat_zones"])
    assert match_identity(str(tmp_path)) == (None, None)
    _identity(tmp_path, {"battle_mode_id": "", "stage_id": 3})
    assert match_identity(str(tmp_path)) == (None, None)


def test_moment_count_is_the_listed_review_moments(tmp_path: Path) -> None:
    _index(tmp_path, [_entry(1), _entry(2), _entry(3)])
    assert moment_count(_submission(tmp_path)) == 3


def test_no_deaths_outcome_counts_zero(tmp_path: Path) -> None:
    _index(tmp_path, [_entry(1)])
    assert moment_count(_submission(tmp_path, outcome="no_deaths")) == 0


def test_complete_without_index_counts_zero(tmp_path: Path) -> None:
    assert moment_count(_submission(tmp_path)) == 0
    assert moment_count(_submission(None)) == 0


def test_unfinished_submission_has_no_count(tmp_path: Path) -> None:
    for status in ("received", "queued", "processing", "failed", "expired"):
        assert moment_count(_submission(tmp_path, status=status, outcome=None)) is None
