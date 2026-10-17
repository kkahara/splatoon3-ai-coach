"""Coaching gap study: frozen draw, blind worksheet, one category per fact, rule."""

from __future__ import annotations

import csv
import io
import json

import coaching_gap_study as gs
import pytest

DESIGN = gs.H_DIR / "design.json"
HAVE_H = DESIGN.is_file() and (gs.H_DIR / "outputs" / "calls").is_dir()


def _study(seed: int = gs.SEED) -> dict:
    return {
        "seed": seed,
        "n_unselected": 2,
        "population": {
            "selected": [["m1", "death_episode:1.000"]],
            "unselected": [
                ["m1", "death_episode:5.000"],
                ["m2", "death_episode:2.000"],
                ["m2", "death_episode:9.000"],
                ["m3", "death_episode:4.000"],
            ],
        },
    }


def test_draw_is_reproducible_and_one_per_match() -> None:
    picked = gs.draw(_study())
    assert picked == gs.draw(_study())
    assert picked[0] == ("m1", "death_episode:1.000")
    unselected = picked[1:]
    assert len(unselected) == 2
    assert len({m for m, _ in unselected}) == 2


@pytest.mark.skipif(not HAVE_H, reason="Experiment H artifacts not present")
def test_population_and_real_draw() -> None:
    design = json.loads(DESIGN.read_text(encoding="utf-8"))
    pop = gs.population(design)
    assert len(pop["selected"]) == 6 and len(pop["unselected"]) == 16
    study = {"seed": gs.SEED, "n_unselected": gs.N_UNSELECTED, "population": pop}
    deaths = gs.draw(study)
    assert len(deaths) == 10
    assert [list(d) for d in deaths[:6]] == pop["selected"]
    assert len({m for m, _ in deaths[6:]}) == 4


@pytest.mark.skipif(not HAVE_H, reason="Experiment H artifacts not present")
def test_sheet_has_no_pipeline_evidence_columns() -> None:
    design = json.loads(DESIGN.read_text(encoding="utf-8"))
    pop = gs.population(design)
    deaths = [tuple(pop["selected"][0]), tuple(pop["unselected"][0])]
    rows = gs.sheet_rows(deaths, {tuple(pop["selected"][0])})
    header = next(csv.reader(io.StringIO(gs.sheet_csv(rows))))
    assert header[-len(gs.USER_COLUMNS) :] == list(gs.USER_COLUMNS)
    banned = ("roster", "special", "evidence", "category", "arm", "score")
    assert not [h for h in header if any(b in h for b in banned)]
    assert {r["selected_in_production"] for r in rows} == {"Y", "N"}
    assert all(r["current_coaching"] for r in rows)


def _fact(death: str, cat: str, theme: str, loc: str = "") -> dict:
    return {
        "death": death,
        "fact": "f",
        "category": cat,
        "a_location": loc,
        "theme": theme,
        "evidence": "e",
    }


def test_validate_requires_one_primary_category() -> None:
    deaths = {"d1"}
    assert gs.validate([_fact("d1", "A", "special", "view")], deaths) == []
    assert gs.validate([_fact("d1", "A/B", "special", "view")], deaths)
    assert gs.validate([_fact("d1", "A", "special")], deaths)
    assert gs.validate([_fact("d1", "B", "special", "view")], deaths)
    assert gs.validate([_fact("d2", "C", "nonsense")], deaths)


def test_decision_rule_and_ranking_threshold() -> None:
    selected = {"s1": True, "s2": True, "u1": False}
    rows = [
        _fact("u1", "B", "special"),
        _fact("u1", "A", "special", "pipeline"),
        _fact("u1", "B", "special"),
        _fact("s1", "C", "opponent"),
        _fact("s2", "D", "teammate"),
    ]
    doc = gs.decide(rows, selected)
    assert doc["path"] == "A/B" and doc["themes"] == ["special"]
    assert doc["kind"] == "B" and doc["next"] == "fusion feature"
    assert doc["ab_per_death_unselected"] == 3.0
    assert doc["ab_per_death_selected"] == 0.0
    assert doc["ranking_review"] is True
    c_heavy = [_fact("s1", "C", "opponent"), _fact("s2", "C", "opponent")]
    c_heavy.append(_fact("u1", "A", "map", "view"))
    doc = gs.decide(c_heavy, selected)
    assert doc["path"] == "C" and doc["themes"] == ["opponent"]
    tie = [_fact("s1", "A", "map", "view"), _fact("u1", "C", "opponent")]
    assert gs.decide(tie, selected)["path"] == "A/B"


def test_ranking_review_needs_a_full_fact_gap() -> None:
    selected = {"s1": True, "u1": False}
    rows = [_fact("u1", "A", "map", "view"), _fact("s1", "C", "opponent")]
    assert gs.decide(rows, selected)["ranking_review"] is True
    rows.append(_fact("s1", "B", "map"))
    assert gs.decide(rows, selected)["ranking_review"] is False
