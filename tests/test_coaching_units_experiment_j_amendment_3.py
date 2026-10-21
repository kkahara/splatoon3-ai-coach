"""Experiment J Amendment 3: the R5 map_absence window adjustment is Experiment
I's Amendment 1 rule, unchanged, applied to arm M's count only."""

from __future__ import annotations

import coaching_units_experiment_i as xi
import coaching_units_experiment_j as xj
import coaching_units_experiment_j_arms as xa
import pytest

from splatoon3_ai_coach.coach.coach import annotate_claim_flags
from splatoon3_ai_coach.coach.llm_client import CoachingAssessment

SECONDS = 24.5


def _record(checks=0, seconds=SECONDS, observable=True):
    return {
        "back_in_play_source": "previous_respawn",
        "seconds_back_in_play": seconds,
        "map_checks_since_back_in_play": checks,
        "seconds_since_alive_map_check": 3.0 if checks else None,
        "eligible": checks == 0 and seconds >= 10.0,
        "map_observable": observable,
    }


def _view(**record):
    base = {"map": {"map_check_before_death": {"status": "observed"}}, "claim_basis": []}
    return xa.m_view(base, _record(**record))


def _validation(sentence, view):
    assessment = CoachingAssessment.model_validate({"assessment": sentence})
    return annotate_claim_flags(assessment, dict(view))


def _i_block(view):
    """The same values in Experiment I's block shape."""
    if view["map_awareness"] is None:
        return None
    return {
        "map_checks_since_back_in_play": view["map_awareness"][
            "map_checks_since_back_in_play"
        ],
        "seconds_back_in_play": view["back_in_play"]["seconds_back_in_play"],
    }


SENTENCES = {
    "frozen_frame": (
        f"No map check was observed in the {SECONDS}s since the player was back in play."
    ),
    "respawn_anchor": "You did not check the map after you respawned.",
    "return_anchor": "No map check was observed after your return.",
    "came_back": "No map check was observed after you came back.",
    "seconds_floor": "No map check in the last 24s.",
    "seconds_ceil": "No map check in the last 25s.",
    "seconds_half": "No map check in the last 24.5 seconds.",
    "seconds_off_by_one": "No map check in the last 26s.",
    "unanchored": "No map check was observed before this death.",
}
QUALIFYING = {
    "frozen_frame",
    "respawn_anchor",
    "return_anchor",
    "came_back",
    "seconds_floor",
    "seconds_ceil",
    "seconds_half",
}


@pytest.mark.parametrize("name", sorted(SENTENCES))
def test_qualifying_and_non_qualifying_sentences(name):
    view = _view()
    validation = _validation(SENTENCES[name], view)
    rules = [(h["rule"], h["check"]) for h in validation["integrity_hits"]]
    assert ("R5", "map_absence") in rules
    expected = 1 if name in QUALIFYING else 0
    assert xa.r5_window_exemption("M", validation, view) == expected


@pytest.mark.parametrize("name", sorted(SENTENCES))
@pytest.mark.parametrize(
    "record",
    [{}, {"checks": 1}, {"seconds": 8.0}, {"observable": False}, {"seconds": 40.0}],
)
def test_parity_with_experiment_i_rule(name, record):
    view = _view(**record)
    validation = _validation(SENTENCES[name], view)
    assert xa.r5_window_exemption("M", validation, view) == len(
        xi.r5_window_exempt(validation, _i_block(view))
    )


def test_alive_check_or_null_block_never_qualifies():
    sentence = SENTENCES["frozen_frame"]
    for view in (_view(checks=1), _view(observable=False)):
        assert xa.r5_window_exemption("M", _validation(sentence, view), view) == 0


def test_not_eligible_with_zero_checks_qualifies_as_in_experiment_i():
    view = _view(seconds=8.0)
    assert view["map_awareness"]["eligible"] is False
    sentence = "No map check was observed in the 8s since you were back in play."
    assert xa.r5_window_exemption("M", _validation(sentence, view), view) == 1


def test_only_arm_m_is_adjusted():
    view = _view()
    validation = _validation(SENTENCES["frozen_frame"], view)
    assert xa.r5_window_exemption("A", validation, view) == 0
    assert xa.r5_window_exemption("C", validation, view) == 0


def test_other_rules_and_checks_are_never_removed():
    view = _view()
    anchored = SENTENCES["frozen_frame"]
    validation = {
        "integrity_hits": [
            {"rule": "R5", "check": "map_recency", "text": anchored},
            {"rule": "R10", "check": "no_template", "text": anchored},
            {"rule": "R7", "check": "value_mismatch", "text": anchored},
        ]
    }
    assert xa.r5_window_exemption("M", validation, view) == 0


def test_output_checks_report_production_and_adjusted_counts():
    view = _view()
    output = {
        "assessment": f"{SENTENCES['frozen_frame']} Nothing else.",
        "claims": [{"claim_id": xa.MAP_CLAIM_ID, "sentence": SENTENCES["frozen_frame"]}],
        "recommendations": [],
    }
    checks = xa.output_checks("M", output, view)
    assert (checks["validator_hits"], checks["r5_window_exempt"]) == (2, 2)
    assert checks["adjusted_hits"] == 0 and checks["c_adjustment"] == 0
    unanchored = {**output, "assessment": SENTENCES["unanchored"]}
    checks = xa.output_checks("M", unanchored, view)
    assert checks["validator_hits"] - checks["r5_window_exempt"] == 1
    assert checks["adjusted_hits"] == 1


def test_amendment_3_is_sealed_after_stop_point_2():
    lines = (xj.OUT_DIR / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    names = [line.split()[1] for line in lines]
    assert names.index("AMENDMENT_3.md") > names.index("populations.json")
