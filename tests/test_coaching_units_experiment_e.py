"""Experiment E catalog patch and runner guards (tools/reports)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "reports"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import coaching_units_experiment_e as xe  # noqa: E402

from splatoon3_ai_coach.coach import claim_basis as cb  # noqa: E402
from splatoon3_ai_coach.coach import claim_integrity  # noqa: E402


def test_expanded_catalog_is_restored() -> None:
    before = (
        cb.CLAIM_DEFINITIONS,
        dict(cb.CLAIMS_BY_ID),
        dict(cb.RECOMMENDATION_TEMPLATES),
    )
    with xe.expanded_catalog():
        ids = {c.claim_id for c in cb.CLAIM_DEFINITIONS}
        assert {"process_after_own_splat", "process_final30_respawn_cost"} <= ids
        assert "rec_after_splat_check" in claim_integrity.RECOMMENDATION_TEMPLATES
    after = (
        cb.CLAIM_DEFINITIONS,
        dict(cb.CLAIMS_BY_ID),
        dict(cb.RECOMMENDATION_TEMPLATES),
    )
    assert after == before


def test_expanded_catalog_restores_on_error() -> None:
    n = len(cb.CLAIM_DEFINITIONS)
    with pytest.raises(RuntimeError), xe.expanded_catalog():
        raise RuntimeError
    assert len(cb.CLAIM_DEFINITIONS) == n
    assert "rec_final30_weigh_fight" not in cb.RECOMMENDATION_TEMPLATES


@pytest.mark.parametrize(
    ("template_id", "sentence"),
    [
        (
            "rec_after_splat_check",
            "After you get a splat, take a second to check your ink and surroundings.",
        ),
        (
            "rec_final30_weigh_fight",
            "In the last 30 seconds, weigh whether a fight is worth the respawn time.",
        ),
    ],
)
def test_template_patterns_match_their_frames(template_id: str, sentence: str) -> None:
    template = next(t for t in xe.EXPERIMENT_TEMPLATES if t.template_id == template_id)
    assert template.pattern.search(sentence)
    assert template.pattern.search(template.frame)


def test_experiment_frames_trip_no_integrity_rule() -> None:
    for claim in xe.EXPERIMENT_CLAIMS:
        assessment = {"assessment": claim.frame, "recommendations": []}
        assert claim_integrity.claim_integrity_hits(assessment, None) == []


def test_run_requires_model_and_confirm(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="--model and --confirm"):
        xe.main(["run", "--out", str(tmp_path)])
