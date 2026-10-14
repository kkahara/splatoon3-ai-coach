"""Experiment G sharpened catalog (tools/reports)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "reports"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import coaching_units_experiment_e as xe  # noqa: E402
import coaching_units_experiment_g as xg  # noqa: E402

from splatoon3_ai_coach.coach import claim_basis as cb  # noqa: E402
from splatoon3_ai_coach.coach import claim_integrity  # noqa: E402

_CLOCK = {"seconds_remaining": 7, "display": "0:07"}


def _input(has_active_again: bool, out_of_play: float | None) -> Any:
    span = (
        None if out_of_play is None else SimpleNamespace(value=out_of_play, clock="video")
    )
    return SimpleNamespace(
        primary_context=SimpleNamespace(
            death_episode=SimpleNamespace(has_active_again=has_active_again)
        ),
        durations=SimpleNamespace(death_to_active_again=span),
    )


@pytest.fixture
def final30(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xg, "_FINAL30", lambda _ci, _unit: dict(_CLOCK))


@pytest.mark.usefixtures("final30")
@pytest.mark.parametrize(
    ("has_active_again", "out_of_play", "case", "back_with"),
    [
        (True, 3.0, "back_with_time", 4.0),
        (True, 8.5, "not_back_by_zero", None),
        (True, 7.0, "not_back_by_zero", None),
        (False, None, "no_return_observed", None),
    ],
)
def test_final30_cases(
    has_active_again: bool, out_of_play: float | None, case: str, back_with: float | None
) -> None:
    values = xg.final30_case(_input(has_active_again, out_of_play), None)
    assert values is not None
    assert values["case"] == case
    assert values.get("back_with") == back_with


@pytest.mark.usefixtures("final30")
def test_final30_needs_verified_play_time() -> None:
    assert xg.final30_case(_input(True, None), None) is None


@pytest.mark.usefixtures("final30")
def test_final30_claims_are_mutually_exclusive() -> None:
    claims = [c for c in xg.SHARPENED_CLAIMS if c.claim_id.startswith("process_final30")]
    for args in ((True, 3.0), (True, 8.5), (False, None)):
        eligible = [c for c in claims if c.eligible(_input(*args), None) is not None]
        assert len(eligible) == 1


def test_final30_needs_the_final30_factor() -> None:
    unit = SimpleNamespace(factors=[])
    assert xg.final30_case(_input(False, None), unit) is None


def test_next_move_template_matches_own_frame_not_reset() -> None:
    frame = xg.NEXT_MOVE_TEMPLATE.frame
    assert xg.NEXT_MOVE_TEMPLATE.pattern.search(frame)
    assert not cb.RECOMMENDATION_TEMPLATES["rec_redeath_reset"].pattern.search(frame)


def test_sharpened_text_trips_no_integrity_rule() -> None:
    values = {"display": "0:19", "back_with": 10.5, "time_since_previous_splat": 1.5}
    for claim in xg.SHARPENED_CLAIMS:
        assessment = {
            "assessment": claim.frame.format(**values),
            "recommendations": [xg.NEXT_MOVE_TEMPLATE.frame],
        }
        assert claim_integrity.claim_integrity_hits(assessment, None) == []


def test_sharpened_catalog_is_restored() -> None:
    before = (cb.CLAIM_DEFINITIONS, dict(cb.RECOMMENDATION_TEMPLATES))
    with xe.arm_catalog("sharpened", xg.CATALOGS):
        assert "rec_after_splat_next_move" in claim_integrity.RECOMMENDATION_TEMPLATES
    assert (cb.CLAIM_DEFINITIONS, dict(cb.RECOMMENDATION_TEMPLATES)) == before
