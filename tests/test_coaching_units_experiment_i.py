"""Experiment I: derived map_awareness fact, arm M view, frozen output checks."""

from __future__ import annotations

import json

import coaching_units_experiment_i as xi
import coaching_units_experiment_i_inputs as xii
import pytest

MATCH = "en-live-playerview-2026-10-11 22-57-30"
HAVE_DATA = (xi.ANALYSIS_ROOT / MATCH).is_dir()
EPISODES = [(100.0, 118.0), (150.0, 165.0)]


def _view(status: str = "observed", episode: str = "sufficient") -> dict:
    return {
        "claim_basis": [{"claim_id": "death_out_of_play", "rec_template_ids": []}],
        "map": {
            "map_check_before_death": {
                "status": status,
                "coverage": {"episode": episode},
            }
        },
    }


def _block(seconds: float = 20.5, checks: int = 0, eligible: bool = True) -> dict:
    return {
        "back_in_play_source": "previous_respawn",
        "seconds_back_in_play": seconds,
        "map_checks_since_back_in_play": checks,
        "seconds_since_alive_map_check": None if checks == 0 else 7.0,
        "respawn_grace_seconds": 2.0,
        "threshold_seconds": 10.0,
        "eligible": eligible,
    }


def test_back_in_play_uses_previous_episode_end_and_grace() -> None:
    facts = xi.map_awareness_facts(EPISODES, [165.5, 166.9], 10.0, 185.5)
    assert facts["back_in_play_source"] == "previous_respawn"
    assert facts["seconds_back_in_play"] == 20.5
    assert facts["map_checks_since_back_in_play"] == 0
    assert facts["eligible"] is True


def test_alive_check_after_grace_counts() -> None:
    facts = xi.map_awareness_facts(EPISODES, [167.5], 10.0, 185.5)
    assert facts["map_checks_since_back_in_play"] == 1
    assert facts["seconds_since_alive_map_check"] == 18.0
    assert facts["eligible"] is False


def test_first_life_starts_at_match_start_and_threshold_is_inclusive() -> None:
    facts = xi.map_awareness_facts(EPISODES, [], 40.0, 50.0)
    assert facts["back_in_play_source"] == "match_start"
    assert facts["seconds_back_in_play"] == 10.0 and facts["eligible"] is True
    assert xi.map_awareness_facts(EPISODES, [], 40.5, 50.0)["eligible"] is False


def test_block_is_null_when_map_unobservable() -> None:
    facts = xi.map_awareness_facts(EPISODES, [], 10.0, 185.5)
    assert xi.awareness_block(facts, _view(episode="insufficient")) is None
    assert xi.awareness_block(facts, _view(status="unknown")) is None
    assert xi.awareness_block(facts, _view())["eligible"] is True


def test_m_view_adds_claim_only_when_eligible() -> None:
    eligible = xi.map_awareness_facts(EPISODES, [], 10.0, 185.5)
    view = _view()
    m = xi.m_view(view, eligible)
    assert m["map_awareness"]["seconds_back_in_play"] == 20.5
    claims = [e["claim_id"] for e in m["claim_basis"]]
    assert claims == ["death_out_of_play", xi.EXPERIMENT_CLAIM_ID]
    assert m["claim_basis"][-1]["rec_template_ids"] == [xi.MAP_REC_TEMPLATE]
    assert "map_awareness" not in view
    short = xi.map_awareness_facts(EPISODES, [], 10.0, 170.0)
    assert [e["claim_id"] for e in xi.m_view(view, short)["claim_basis"]] == [
        "death_out_of_play"
    ]
    assert xi.m_view(_view(episode="insufficient"), eligible)["map_awareness"] is None


def test_system_prompt_m_is_a_plus_section() -> None:
    a, m = xi.arm_system_prompt("A"), xi.arm_system_prompt("M")
    assert m.startswith(a.rstrip("\n")) and m.endswith(xi.MAP_SECTION)


def test_mentions_map_check() -> None:
    assert xi.mentions_map_check("Take a quick look at the map before moving up.")
    assert xi.mentions_map_check("No map check was observed.")
    assert xi.mentions_map_check("Work on map awareness.")
    assert not xi.mentions_map_check("You were out of play for 17.5s.")


def test_value_fidelity_rounding_and_errors() -> None:
    block = _block(seconds=20.5)
    ok = [
        "No map check in the 20.5s since you respawned.",
        "No map check for 21 seconds.",
    ]
    assert xi.value_fidelity_errors(ok, block) == []
    assert len(xi.value_fidelity_errors(["No map check in the 30s since."], block)) == 1
    assert xi.value_fidelity_errors(["You were out for 30s."], block) == []


def test_map_rule_violations() -> None:
    advice = {"recommendations": ["Check the map before moving up."]}
    assert xi.map_rule_violations(advice, _block()) == []
    assert xi.map_rule_violations(advice, _block(eligible=False)) == [
        "advice_not_eligible"
    ]
    assert xi.map_rule_violations(advice, None) == ["advice_not_eligible"]
    causal = {"assessment": "You died because you did not check the map."}
    assert "causal" in xi.map_rule_violations(causal, _block())
    hedged = {"assessment": "The missing map check is not shown to be why you died."}
    assert "causal" not in xi.map_rule_violations(hedged, _block())
    content = {"assessment": "A map check would have shown the flanker."}
    assert xi.map_rule_violations(content, _block()) == ["map_content"]


def _validation(*texts: str) -> dict:
    return {
        "hit_count": 0,
        "causal_claim_hits": [],
        "unsupported_recommendation_hits": [],
        "unresolved_evidence_paths": [],
        "integrity_hits": [
            {"rule": "R5", "check": "map_absence", "text": t, "counts_as_violation": True}
            for t in texts
        ],
    }


def test_r5_window_exemption_needs_zero_checks_and_window_anchor() -> None:
    validation = _validation(
        "No map check was observed in the 20.5s since you were back in play.",
        "You never checked the map.",
    )
    counts = xi.validator_counts(validation, _block())
    assert counts == {"validator_hits": 2, "r5_window_exempt": 1, "adjusted": 1}
    assert xi.validator_counts(validation, None)["adjusted"] == 2
    assert xi.validator_counts(validation, _block(checks=1))["adjusted"] == 2
    cited = _validation("No map check for 20.5 seconds.")
    assert xi.validator_counts(cited, _block())["adjusted"] == 0


def test_amendment_2_marks_one_null_block_death() -> None:
    death = {"match": "2026-10-11 20-33-31", "scenario_id": "death_episode:199.500"}
    assert xii.null_block_expected(death)
    assert not xii.effective_eligible({**death, "eligible": True})
    other = {"match": "2026-10-11 22-57-30", "scenario_id": "death_episode:308.000"}
    assert xii.effective_eligible({**other, "eligible": True})
    assert (xi.UPTAKE_MIN, xi.UPTAKE_OF) == (14, 28)


@pytest.mark.skipif(not HAVE_DATA, reason="local analysis artifacts not present")
def test_derived_facts_reproduce_frozen_design() -> None:
    design = json.loads((xi.OUT_DIR / "design.json").read_text(encoding="utf-8"))
    for death in design["deaths"]:
        if death["match"] != "2026-10-11 22-57-30":
            continue
        facts = xi.facts_for(xi.ANALYSIS_ROOT / MATCH, death["scenario_id"])
        assert facts["seconds_back_in_play"] == pytest.approx(
            death["seconds_back_in_play"]
        )
        assert facts["eligible"] == death["eligible"]
