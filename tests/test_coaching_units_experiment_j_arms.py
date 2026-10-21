"""Experiment J stop point 3: populations, arms, prompts, ablation and calls
conform exactly to the frozen design and the sealed stop point 2 artifacts."""

from __future__ import annotations

import json
import re

import coaching_units_experiment_j as xj
import coaching_units_experiment_j_arms as xa
import coaching_units_experiment_j_census as xc
import coaching_units_experiment_j_dry_run as xr
import pytest

from splatoon3_ai_coach.coach.claim_basis import RECOMMENDATION_TEMPLATES
from splatoon3_ai_coach.coach.prompts import load_system_prompt

DESIGN = json.loads((xj.OUT_DIR / "design.json").read_text(encoding="utf-8"))
DESIGN_MD = (xj.OUT_DIR / "DESIGN.md").read_text(encoding="utf-8")
POPS = xa.load_json(xa.POPULATIONS)
CENSUS = xa.load_json(xc.CENSUS)
KEY = xa.load_json(xc.WORKSHEET_KEY)
SEALED = {"P1": 35, "P2": 5, "P3": 20, "C": 6}


def test_sealed_artifacts_verify():
    assert xr.sums_problems(xr.SEALS, xj.OUT_DIR) == []


# --- frozen constants ------------------------------------------------------------


def test_experiment_claim_and_templates_match_design_json():
    claim = DESIGN["experiment_claim"]
    assert xa.MAP_CLAIM_ID == claim["claim_id"]
    assert xa.MAP_CLAIM_FRAME == claim["frame"]
    assert list(xa.MAP_EVIDENCE_PATHS) == claim["evidence_paths"]
    assert xa.MAP_TEMPLATE == claim["rec_template_id"]
    c = DESIGN["c_diagnostic"]
    assert [list(m) for m in xa.C_MENU] == [
        [m["claim_id"], m["rec_template_id"]] for m in c["menu_priority"]
    ]
    assert xa.REPOSITION_TEMPLATE == c["experiment_template"]["template_id"]
    assert xa.REPOSITION_FRAME == c["experiment_template"]["frame"]
    assert xa.REPOSITION_PATTERN.pattern == c["experiment_template"]["pattern"]
    assert xa.MIN_C == c["min_natural_deaths_to_run"]
    assert xa.SPLITTER.pattern == DESIGN["ablation"]["sentence_splitter"]
    assert xa.REPEATS == DESIGN["repeats"]


def test_reposition_pattern_matches_no_production_template_frame():
    for template in RECOMMENDATION_TEMPLATES.values():
        assert not xa.REPOSITION_PATTERN.search(template.frame)
    assert xa.REPOSITION_PATTERN.search(xa.REPOSITION_FRAME)


def _design_text_blocks():
    part = DESIGN_MD.split("## Prompt sections", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"```text\n(.*?)```", part, re.DOTALL)


def test_sections_are_the_frozen_design_wording():
    base, m_lines = _design_text_blocks()
    assert xa.section("C") == base
    head, rest = base.split("- extra_point.claim_id", 1)
    assert xa.section("M") == head + m_lines + "- extra_point.claim_id" + rest


def test_system_prompts_are_production_plus_section():
    production = load_system_prompt(roster_evidence=False)
    assert xa.system_prompt("A") == production
    for arm in ("M", "C"):
        assert xa.system_prompt(arm) == production.rstrip("\n") + "\n\n" + xa.section(arm)
    assert "map_awareness" not in xa.system_prompt("C")


# --- populations and calls ---------------------------------------------------------


def test_populations_are_the_sealed_stop_point_2_populations():
    deaths = xa.population_deaths(POPS, CENSUS)
    assert {k: len(v) for k, v in deaths.items()} == SEALED
    ids = {k: {xc.death_id(d) for d in v} for k, v in deaths.items()}
    first50 = set(KEY["first50"])
    assert ids["P1"] | ids["P2"] <= first50 and not ids["P1"] & ids["P2"]
    assert not (ids["P1"] | ids["P2"]) & set(POPS["unclear"])
    assert len(ids["P1"] | ids["P2"]) + len(POPS["unclear"]) == 50
    assert ids["P3"] == set(KEY["p3"]) and not ids["P3"] & first50
    assert ids["C"] <= ids["P1"] | ids["P2"]
    by_id = {xc.death_id(d): d for d in CENSUS["deaths"]}
    natural = {i for i in ids["P1"] | ids["P2"] if by_id[i]["c_point"]}
    assert ids["C"] == natural
    assert all(by_id[i]["eligible"] for i in ids["P1"] | ids["P2"])
    assert not any(by_id[i]["eligible"] for i in ids["P3"])


def test_c_is_dropped_below_five_natural_deaths():
    pops = {**POPS, "c_diagnostic": {"natural_deaths": POPS["P1"][:4]}}
    assert xa.population_deaths(pops, CENSUS)["C"] == []


def test_call_plan_arms_repeats_and_counts_follow_the_design():
    deaths = xa.population_deaths(POPS, CENSUS)
    calls = xa.call_plan(deaths)
    arms = {(c["population"], c["arm"]) for c in calls}
    assert arms == {
        ("P1", "A"),
        ("P1", "M"),
        ("P2", "A"),
        ("P2", "M"),
        ("P3", "M"),
        ("C", "C"),
    }
    assert {c["repeat"] for c in calls} == {1, 2}
    assert len({c["call_id"] for c in calls}) == len(calls)
    expected = {"P1": 35 * 2 * 2, "P2": 5 * 2 * 2, "P3": 20 * 2, "C": 6 * 2}
    assert xa.call_counts(calls) == {**expected, "total": 212}
    bound = DESIGN["calls_upper_bound"]
    assert expected["P1"] + expected["P2"] <= bound["p1_p2"]
    assert expected["P3"] <= bound["p3"] and 212 <= bound["total_approx_max"]
    assert xr.call_budget(calls)["within_bound"] is True


# --- views -----------------------------------------------------------------------


def _record(eligible=True, observable=True, c_point=None):
    return {
        "order": 7,
        "cid": "death_episode:100.000",
        "back_in_play_source": "previous_respawn",
        "seconds_back_in_play": 24.5,
        "map_checks_since_back_in_play": 0 if eligible else 1,
        "seconds_since_alive_map_check": None if eligible else 6.0,
        "eligible": eligible,
        "map_observable": observable,
        "c_point": c_point,
    }


def _view():
    return {
        "map": {"map_check_before_death": {"status": "observed"}},
        "claim_basis": [
            {
                "claim_id": "death_own_splat_before",
                "level": "A",
                "frame": "You got a splat {time_since_previous_splat}s before you "
                "were splatted.",
                "evidence_paths": ["death.time_since_previous_splat.video_seconds"],
                "values": {"time_since_previous_splat": 4.0},
                "rec_template_ids": [],
                "rec_frames": [],
            }
        ],
    }


def test_m_view_on_eligible_and_not_eligible_deaths():
    view = xa.m_view(_view(), _record())
    assert view["back_in_play"] == {
        "source": "previous_respawn",
        "seconds_back_in_play": 24.5,
    }
    assert view["map_awareness"]["eligible"] is True
    assert view["map_awareness"]["respawn_grace_seconds"] == 2.0
    assert view["map_awareness"]["threshold_seconds"] == 10.0
    assert view["extra_point"] == {
        "claim_id": xa.MAP_CLAIM_ID,
        "rec_template_id": xa.MAP_TEMPLATE,
    }
    assert view["claim_basis"][-1]["claim_id"] == xa.MAP_CLAIM_ID
    p3 = xa.m_view(_view(), _record(eligible=False))
    assert p3["extra_point"] is None and p3["map_awareness"]["eligible"] is False
    assert [e["claim_id"] for e in p3["claim_basis"]] == ["death_own_splat_before"]
    assert xa.m_view(_view(), _record(observable=False))["map_awareness"] is None


def test_c_view_adds_template_only_to_its_claim_and_no_map_block():
    view = xa.c_view(_view(), _record(c_point="death_own_splat_before"))
    entry = view["claim_basis"][0]
    assert entry["rec_template_ids"] == [xa.REPOSITION_TEMPLATE]
    assert entry["rec_frames"] == [xa.REPOSITION_FRAME]
    assert "map_awareness" not in view
    assert view["extra_point"]["claim_id"] == "death_own_splat_before"
    assert xa.arm_view("A", _view(), _record()) == _view()


def test_compliant_outputs_resolve_their_literal_paths():
    view = xa.m_view(_view(), _record())
    output = xr.compliant(view)
    assert output["evidence_used"] == [
        "back_in_play.seconds_back_in_play = 24.5",
        "map_awareness.map_checks_since_back_in_play = 0",
    ]
    assert xa.ablate(output)["ablatable"] is True


# --- ablation ----------------------------------------------------------------------

S = "No map check was observed in the 24.5s since the player was back in play."
REC = "After you respawn, take a quick look at the map before rejoining."


def _out(assessment, recs=(REC,), claims=((xa.MAP_CLAIM_ID, S),)):
    return {
        "assessment": assessment,
        "recommendations": list(recs),
        "claims": [{"claim_id": c, "sentence": s} for c, s in claims],
    }


def test_ablation_removes_only_the_map_unit_without_repair():
    out = _out(f"you got a splat 4s before. {S}", recs=(REC, "Refill your ink first."))
    result = xa.ablate(out)
    assert result == {
        "ablatable": True,
        "reasons": [],
        "assessment": "you got a splat 4s before.",
        "recommendations": ["Refill your ink first."],
        "kept_recommendation": True,
    }


@pytest.mark.parametrize(
    ("output", "reason"),
    [
        (None, "unparsed"),
        (_out(f"Other fact. {S}", claims=()), "map_claim_not_exactly_one"),
        (
            _out(f"Other. {S}", claims=((xa.MAP_CLAIM_ID, S), (xa.MAP_CLAIM_ID, S))),
            "map_claim_not_exactly_one",
        ),
        (
            _out("Other. You went 24.5s without a map check."),
            "map_sentence_not_exactly_one_segment",
        ),
        (_out(f"Other. {S} {S}"), "map_sentence_not_exactly_one_segment"),
        (_out(S), "no_other_segment"),
        (_out(f"Use the map more. {S}"), "map_in_remaining_segment"),
        (
            _out(f"Other. {S}", recs=("Check the map and refill your ink.",)),
            "map_recommendation_matches_non_map_template",
        ),
    ],
)
def test_ablation_refuses_outputs_that_fail_a_frozen_rule(output, reason):
    result = xa.ablate(output)
    assert result["ablatable"] is False and reason in result["reasons"]


def test_stored_experiment_i_outputs_reproduce_the_frozen_check():
    stored = xr.stored_ablation()
    assert (stored["ablatable"], stored["of"], stored["no_recommendation_left"]) == (
        27,
        28,
        27,
    )
    assert [f["call_id"] for f in stored["failures"]] == [
        "M__2026_10_11_22_31_53_death_episode_208_500__r1"
    ]


# --- automatic checks -------------------------------------------------------------


def test_uptake_needs_map_recommendation_and_stated_seconds():
    view = xa.m_view(_view(), _record())
    assert xa.uptake(_out(f"Other. {S}"), view) is True
    assert xa.uptake(_out(f"Other. {S}", recs=()), view) is False
    assert xa.uptake(_out("No map check since you were back in play."), view) is False
    assert xa.uptake(None, view) is False


def test_value_fidelity_and_c_adjustment():
    view = xa.m_view(_view(), _record())
    assert xa.value_fidelity_errors(_out(f"Other. {S}"), view) == []
    wrong = S.replace("24.5", "31")
    assert xa.value_fidelity_errors(_out(wrong, claims=((xa.MAP_CLAIM_ID, wrong),)), view)
    hits = {
        "integrity_hits": [
            {"rule": "R10", "check": "no_template", "text": xa.REPOSITION_FRAME},
            {"rule": "R10", "check": "no_template", "text": "Play safer."},
            {"rule": "R5", "check": "map_absence", "text": S},
        ]
    }
    assert xa.c_adjustment(hits) == 1


@pytest.mark.skipif(not xc.CENSUS_DIR.is_dir(), reason="census analyses are local only")
def test_real_arm_inputs_hold_every_invariant():
    from splatoon3_ai_coach.config import default_config_path, load_config

    config = load_config(default_config_path())
    config.coach.roster_evidence = False
    _, deaths, prod, arms, facts = xr.prepare(config)
    assert facts == []
    assert xr.invariant_problems(deaths, arms, prod) == []
    assert xr.path_problems(arms) == []
    assert {arm: len(v) for arm, v in arms.items()} == {"A": 40, "M": 60, "C": 6}
    files = xr.input_files(deaths, arms)
    manifest = json.loads(files["manifest.json"])
    assert manifest["call_counts"]["total"] == 212
    assert manifest["model"] == DESIGN["model"]["model"]
    for record in deaths["P1"] + deaths["P2"]:
        view = arms["M"][xc.death_id(record)]["view"]
        assert (
            view["back_in_play"]["seconds_back_in_play"] == record["seconds_back_in_play"]
        )
        assert view["map_awareness"]["map_checks_since_back_in_play"] == 0


def test_map_regex_is_experiment_i_mention_plus_bare_map():
    assert xa.MAP_REGEX.search("the map")
    assert xa.MAP_REGEX.search("map awareness")
    assert not xa.MAP_REGEX.search("mapping the lanes")
