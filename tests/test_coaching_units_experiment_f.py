"""Experiment F reworded catalog (tools/reports)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "reports"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import coaching_units_experiment_e as xe  # noqa: E402
import coaching_units_experiment_f as xf  # noqa: E402

from splatoon3_ai_coach.coach import claim_basis as cb  # noqa: E402
from splatoon3_ai_coach.coach import claim_integrity  # noqa: E402


def test_reworded_claims_change_only_wording() -> None:
    for old, new in zip(xe.EXPERIMENT_CLAIMS, xf.REWORDED_CLAIMS, strict=True):
        assert (new.claim_id, new.level, new.evidence_paths, new.eligible) == (
            old.claim_id,
            old.level,
            old.evidence_paths,
            old.eligible,
        )
        assert new.frame != old.frame
        assert new.rec_template_ids != old.rec_template_ids


def test_reworded_frames_quote_only_carried_values() -> None:
    placeholders = {"process_after_own_splat": "time_since_previous_splat"}
    placeholders["process_final30_respawn_cost"] = "display"
    for claim in xf.REWORDED_CLAIMS:
        assert "{" + placeholders[claim.claim_id] + "}" in claim.frame


@pytest.mark.parametrize("template", xf.REWORDED_TEMPLATES, ids=lambda t: t.template_id)
def test_reworded_templates_match_own_frame_not_reset(
    template: cb.RecommendationTemplate,
) -> None:
    assert template.pattern.search(template.frame)
    assert not cb.RECOMMENDATION_TEMPLATES["rec_redeath_reset"].pattern.search(
        template.frame
    )


def test_reworded_text_trips_no_integrity_rule() -> None:
    for claim, template in zip(xf.REWORDED_CLAIMS, xf.REWORDED_TEMPLATES, strict=True):
        frame = claim.frame.format(time_since_previous_splat=1.5, display="0:20")
        assessment = {"assessment": frame, "recommendations": [template.frame]}
        assert claim_integrity.claim_integrity_hits(assessment, None) == []


def test_reworded_catalog_is_restored() -> None:
    before = (cb.CLAIM_DEFINITIONS, dict(cb.RECOMMENDATION_TEMPLATES))
    with xe.arm_catalog("reworded", xf.CATALOGS):
        assert "rec_after_splat_slow_down" in claim_integrity.RECOMMENDATION_TEMPLATES
    assert (cb.CLAIM_DEFINITIONS, dict(cb.RECOMMENDATION_TEMPLATES)) == before


def test_sheet_needs_both_experiments(tmp_path: Path) -> None:
    (tmp_path / "calls").mkdir()
    with pytest.raises(SystemExit, match="both"):
        xf.sheet_records(tmp_path, tmp_path)
