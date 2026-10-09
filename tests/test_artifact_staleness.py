"""Stale coach artifacts are detected the same way by every consumer."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest
from coach_artifact_helpers import index_entry
from loguru import logger
from public_site.results import load_result
from reports.importance_report import read_old_ranking
from typer.testing import CliRunner
from vision_manifest_viewer.loader import _warn_if_stale
from vmv_site.coach_layers import staleness_payload

from splatoon3_ai_coach.cli import app
from splatoon3_ai_coach.coach.artifacts import (
    COACH_EVIDENCE_VERSION,
    CoachingIndex,
    CoachingIndexEntry,
    StalenessReason,
    artifact_staleness,
    coach_inputs_staleness,
)

ANALYSIS_ID = "a-current"
SAFE = "death_episode_37_500"
TRACE_C_STATEMENT = "Special: not ready"


def _index(**fields: object) -> CoachingIndex:
    entry = CoachingIndexEntry.model_validate(index_entry(SAFE, video_time=37.5))
    return CoachingIndex(entries=[entry], **fields)


def _manifest(analysis_id: str = ANALYSIS_ID) -> dict:
    return {"analysis": {"analysis_id": analysis_id}, "game_events": []}


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({}, StalenessReason.EVIDENCE_VERSION_MISSING),
        ({"evidence_version": 1, "analysis_id": ANALYSIS_ID}, "evidence_version_old"),
        (
            {"evidence_version": COACH_EVIDENCE_VERSION, "analysis_id": "a-old"},
            StalenessReason.ANALYSIS_ID_MISMATCH,
        ),
        (
            {"evidence_version": COACH_EVIDENCE_VERSION},
            StalenessReason.ANALYSIS_ID_MISMATCH,
        ),
    ],
)
def test_staleness_reasons(fields: dict, reason: str) -> None:
    staleness = artifact_staleness(_index(**fields), _manifest())
    assert staleness.stale
    assert staleness.reason == reason
    assert "coach-inputs" in staleness.message()


def test_current_index_is_current() -> None:
    index = _index(evidence_version=COACH_EVIDENCE_VERSION, analysis_id=ANALYSIS_ID)
    staleness = artifact_staleness(index, _manifest())
    assert staleness.status == "current"
    assert staleness.message() == ""


def test_missing_manifest_still_checks_evidence_version() -> None:
    assert artifact_staleness(_index(), None).stale
    index = _index(evidence_version=COACH_EVIDENCE_VERSION, analysis_id="x")
    assert not artifact_staleness(index, None).stale


def _old_trace_c(analysis: Path) -> Path:
    """Pre-versioning coach_inputs: bare-list index and a 'not ready' statement."""
    inputs = analysis / "coach_inputs"
    inputs.mkdir(parents=True)
    (analysis / "vision_manifest.json").write_text(json.dumps(_manifest()))
    entry = index_entry(SAFE, scenario_id="death_episode:37.500", video_time=37.5)
    (inputs / "coaching_index.json").write_text(json.dumps([entry]))
    (inputs / f"{SAFE}.coaching.json").write_text(
        json.dumps({"factors": [{"active": True, "statement_player": TRACE_C_STATEMENT}]})
    )
    (inputs / f"{SAFE}.coach_input.json").write_text(json.dumps({}))
    (inputs / f"{SAFE}.llm_view.json").write_text(json.dumps({}))
    return inputs


@pytest.fixture
def warnings() -> list[str]:
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    yield messages
    with contextlib.suppress(ValueError):  # the CLI may reset loguru handlers
        logger.remove(sink)


def test_old_trace_c_inputs_are_stale_in_every_consumer(
    tmp_path: Path, warnings: list[str]
) -> None:
    analysis = tmp_path / "enprev"
    inputs = _old_trace_c(analysis)

    shared = coach_inputs_staleness(inputs, analysis)
    assert shared is not None and shared.reason == "evidence_version_missing"

    result = load_result(analysis)
    assert result.stale is True
    assert result.stale_reason == "evidence_version_missing"

    payload = staleness_payload(inputs, analysis)
    assert payload["status"] == "stale"
    assert payload["reason"] == "evidence_version_missing"

    _warn_if_stale(analysis)
    read_old_ranking(analysis)
    stale_logs = [m for m in warnings if "evidence_version_missing" in m]
    assert len(stale_logs) == 2

    refused = CliRunner().invoke(app, ["coach-prototype", str(analysis)])
    assert refused.exit_code == 1
    assert "stale" in refused.stdout
    assert "--allow-stale" in refused.stdout


def test_current_inputs_are_not_flagged(tmp_path: Path) -> None:
    analysis = tmp_path / "current"
    inputs = _old_trace_c(analysis)
    entry = index_entry(SAFE, scenario_id="death_episode:37.500", video_time=37.5)
    (inputs / "coaching_index.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "evidence_version": COACH_EVIDENCE_VERSION,
                "analysis_id": ANALYSIS_ID,
                "entries": [entry],
            }
        )
    )
    assert load_result(analysis).stale is False
    assert staleness_payload(inputs, analysis)["status"] == "current"
