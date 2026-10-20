"""Experiment I post-result diagnostic: path classification, schema, ratings."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "diagnostics"))

import experiment_i_postmortem as pm  # noqa: E402
import pytest  # noqa: E402

VIEW = {
    "map_awareness": {"seconds_back_in_play": 16.0, "map_checks_since_back_in_play": 0},
    "special": {
        "state_at_anchor": "ready",
        "nearest_before_anchor": {
            "state": "ready",
            "source_path": "primary_context.special.nearest_before_anchor",
        },
    },
    "clock": {
        "display": "4:35",
        "seconds_remaining": 275,
        "source_path": "game_clock_samples[label=death]",
    },
    "related": [{"clock": {"display": "4:35"}}, {"clock": {"display": "4:35"}}],
    "a": {"x": {"v": 1}},
    "b": {"x": {"v": 1}},
}


def _c(text: str) -> dict:
    path = text.split(" ")[0]
    return pm.classify(VIEW, text, path)


def test_prefix_mismatch_is_a() -> None:
    c = _c("primary_context.map_awareness.seconds_back_in_play = 16.0")
    assert (c["category"], c["mapping"]) == ("A", "prefix")
    assert c["equal_candidates"] == {"map_awareness.seconds_back_in_play": 16.0}
    assert _c('primary_context.special.state_at_anchor = "ready"')["category"] == "A"


def test_label_block_field_is_a() -> None:
    c = _c('game_clock_samples[label=death].observation.display = "4:35"')
    assert (c["category"], c["mapping"]) == ("A", "label_block")
    assert c["equal_candidates"] == {"clock.display": "4:35"}


def test_wrong_value_is_not_a_and_missing_is_c() -> None:
    assert (
        _c("primary_context.map_awareness.seconds_back_in_play = 30")["category"] == "D"
    )
    assert _c("primary_context.enemy.flank_route = left")["category"] == "C"


def test_multiple_candidates_is_b() -> None:
    c = pm.classify({"a": {"x": {"v": 1}}}, "ns.a.x.v = 1", "ns.a.x.v")
    assert c["category"] == "A"
    c = pm.classify({"a": {"x": {"v": 1}}, "x": {"v": 1}}, "ns.a.x.v = 1", "ns.a.x.v")
    assert c["category"] == "B"
    c = pm.classify({"x": {"v": 1}, "v": 1}, "ns.x.v = 1", "ns.x.v")
    assert c["category"] == "B"


def test_coachinput_schema_field() -> None:
    assert pm.coachinput_field_exists("primary_context.special.state_at_anchor")
    assert pm.coachinput_field_exists(
        "game_clock_samples[label=death].observation.display"
    )
    assert pm.coachinput_field_exists(
        "primary_context.timeline.time_since_previous_death"
    )
    assert not pm.coachinput_field_exists(
        "primary_context.timeline.time_since_previous_death.value"
    )
    assert not pm.coachinput_field_exists("primary_context.map_awareness.eligible")


def test_note_template_and_patterns() -> None:
    assert pm.note_template("Clock 4:35, 16.0s back  in play") == (
        "Clock <clock>, <n>s back in play"
    )
    rows = [
        {
            "arm": "M",
            "group": "eligible",
            "match": "m",
            "cid": "d",
            "repeat": r,
            "notes": "same",
            "usefulness_1to5": "4",
            "redundancy_1to5": "2",
            "anchor_shows_situation_1to5": "4",
            "missing_context_1to5": "3",
            "supported_yes_no": "yes",
            "map_use_correct_incorrect_not_mentioned": "correct",
        }
        for r in ("1", "2")
    ]
    diag = pm.ratings_diagnostic(rows)
    assert diag["repeat_pairs_identical_pattern"] == 1
    assert diag["distinct_patterns_overall"] == 1
    assert diag["usefulness_by_map_use"]["correct"]["mean"] == pytest.approx(4)


def test_prompt_examples_extracted_verbatim() -> None:
    text = (
        'x\nEVIDENCE CITATION RULE\n\n"a.b = 1"\nprose\n'
        '"related[0].c = \\"d\\""\nOutput format\n"z = 2"\n'
    )
    assert pm.prompt_examples(text) == ["a.b = 1", 'related[0].c = "d"']


def test_sums_problems_detects_change(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("x")
    digest = pm._sha(tmp_path / "f.txt")
    (tmp_path / "SHA256SUMS").write_text(f"{digest}  f.txt\n")
    assert pm.sums_problems(tmp_path) == []
    (tmp_path / "f.txt").write_text("y")
    (tmp_path / "g.txt").write_text("z")
    assert len(pm.sums_problems(tmp_path)) == 2
