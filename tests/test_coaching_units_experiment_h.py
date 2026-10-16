"""Experiment H design: arm isolation, death classification, frozen output checks."""

from __future__ import annotations

from pathlib import Path

import coaching_units_experiment_h as xh
import pytest

from splatoon3_ai_coach.coach.prompts import load_system_prompt
from splatoon3_ai_coach.config import default_config_path, load_config

MATCH = "en-live-playerview-2026-10-11 22-57-30"
HAVE_DATA = (xh.REFRESHED_ROOT / MATCH).is_dir() and (
    xh.PRE_REFRESH_ROOT / MATCH
).is_dir()


def _dump_without_switch(config) -> dict:
    data = config.model_dump(mode="json")
    data["coach"].pop("roster_evidence")
    return data


def test_arm_configs_differ_only_in_the_production_switch() -> None:
    base = load_config(default_config_path())
    assert base.coach.roster_evidence is False
    assert xh.arm_config(base, "A").model_dump() == base.model_dump()
    for arm in ("B", "C"):
        config = xh.arm_config(base, arm)
        assert config.coach.roster_evidence is True
        assert _dump_without_switch(config) == _dump_without_switch(base)


def test_arm_system_prompts_are_the_production_prompts() -> None:
    assert xh.arm_system_prompt("A") == load_system_prompt(roster_evidence=False)
    assert "ROSTER GUIDANCE" not in xh.arm_system_prompt("A")
    assert xh.arm_system_prompt("B") == xh.arm_system_prompt("C")
    assert xh.arm_system_prompt("C") == load_system_prompt(roster_evidence=True)


def test_switched_config_text_changes_only_the_switch(tmp_path: Path) -> None:
    text = default_config_path().read_text(encoding="utf-8")
    on = xh.switched_config_text(text, True)
    assert on.replace("roster_evidence: true", "roster_evidence: false") == text
    path = tmp_path / "on.yaml"
    path.write_text(on, encoding="utf-8")
    assert load_config(path).coach.roster_evidence is True
    with pytest.raises(SystemExit):
        xh.switched_config_text(text + "\nroster_evidence: false\n", True)


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        ([], xh.UNCHANGED),
        ([".importance.rank", ".importance.selected_for_llm"], xh.RANK_ONLY),
        ([".roster.player_count_window[].ally_alive_count"], xh.WINDOW_ONLY),
        (
            [".importance.rank", ".roster.player_count_window[].numbers_state"],
            xh.WINDOW_ONLY,
        ),
        ([".roster.before_death.ally_alive_count"], xh.AT_OR_BEFORE_DEATH),
        (
            [".death.numbers_state_at_death", ".roster.player_count_window[].x"],
            xh.AT_OR_BEFORE_DEATH,
        ),
        ([".supporting_evidence[].value"], xh.AT_OR_BEFORE_DEATH),
    ],
)
def test_classify(paths: list[str], expected: str) -> None:
    assert xh.classify(paths) == expected


def test_diff_paths_marks_list_items() -> None:
    a = {"roster": {"w": [{"n": 1}, {"n": 2}]}, "importance": {"rank": 1}}
    b = {"roster": {"w": [{"n": 1}, {"n": 3}]}, "importance": {"rank": 2}}
    assert sorted(xh.diff_paths(a, b)) == [".importance.rank", ".roster.w[].n"]


def test_draw_controls_is_seeded_and_stratified() -> None:
    pool = [("m", f"d{i:02d}") for i in range(20)]
    selected = {key: int(key[1][1:]) < 8 for key in pool}
    first = xh.draw_controls(pool, selected, 3, 5)
    assert first == xh.draw_controls(list(reversed(pool)), selected, 3, 5)
    assert sum(selected[k] for k in first) == 3 and len(first) == 8
    assert first != xh.draw_controls(pool, selected, 3, 5, seed=1)
    with pytest.raises(SystemExit):
        xh.draw_controls(pool, selected, 9, 0)


ROSTER_VIEW = {
    "roster": {
        "before_death": {"ally_alive_count": 3, "opponent_alive_count": 4},
        "at_death": {"ally_alive_count": 2, "opponent_alive_count": 4},
        "player_count_window": [
            {"ally_alive_count": 4, "opponent_alive_count": 4},
            {"ally_alive_count": None, "opponent_alive_count": None},
        ],
    }
}


def test_roster_mention_and_cited_values() -> None:
    text = "Your team had 3 players alive against 4 just before you were splatted."
    assert xh.mentions_roster(text)
    assert xh.cited_pairs(text) == {(3, 4)}
    assert xh.cited_pairs("It was 2v4 at the death.") == {(2, 4)}
    assert xh.cited_states("Your team was outnumbered.") == {"disadvantage"}
    assert xh.cited_states("numbers were even") == {"even"}
    assert not xh.mentions_roster("You were splatted 12 seconds after respawning.")


def test_count_fidelity_uses_the_arms_own_roster_block() -> None:
    assert xh.count_fidelity("3 players alive against 4", ROSTER_VIEW)
    assert xh.count_fidelity("2v4 at the death; numbers were even earlier", ROSTER_VIEW)
    assert not xh.count_fidelity("4 against 3 before the death", ROSTER_VIEW)
    assert not xh.count_fidelity("You had a numbers advantage.", ROSTER_VIEW)
    assert not xh.count_fidelity("3v4", {"roster": None})
    assert xh.count_fidelity("You respawned quickly.", {"roster": None})


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        ("You were outnumbered, so you should have retreated.", ["verdict"]),
        ("You took an outnumbered fight near the zone.", ["fight_framing"]),
        (
            "With 3 alive, a teammate died and you traded.",
            ["teammate_or_trade_inference"],
        ),
        ("You were at a disadvantage for 5.0 seconds.", ["continuous_duration"]),
        ("Your team had 3 players alive against 4. Reset before re-engaging.", []),
        ("You should have retreated after the splat.", []),
    ],
)
def test_roster_violations(text: str, codes: list[str]) -> None:
    assert xh.roster_violations(text) == codes


def test_divergence() -> None:
    assert xh.divergence("same text", "same text") == 0.0
    assert 0.0 < xh.divergence("abc", "abd") < 1.0


@pytest.mark.skipif(
    not HAVE_DATA, reason="refreshed and pre-refresh analyses not present"
)
def test_arm_views_match_production_cli_and_isolate_roster(tmp_path: Path) -> None:
    base_path = default_config_path()
    base = load_config(base_path)
    views = {arm: xh.arm_views(base, arm, (MATCH,)) for arm in xh.ARMS}
    for arm, (root, roster) in xh.ARMS.items():
        cfg = tmp_path / f"{arm}.yaml"
        cfg.write_text(
            xh.switched_config_text(base_path.read_text(encoding="utf-8"), roster),
            encoding="utf-8",
        )
        cli = xh._cli_views(xh.ROOTS[root] / MATCH, cfg, tmp_path / arm)
        assert {cid: v.view for (_, cid), v in views[arm].items()} == cli
    for death in views["A"].values():
        assert death.view["roster"] is None and death.view["roster_evidence"] is False
        assert not any(
            "number" in f for f in death.view["importance"]["active_factor_ids"]
        )
        assert death.view["death"]["numbers_state_at_death"] is None
    rows = {r["cid"]: r for r in xh.classify_deaths(views)}
    assert rows["death_episode:308.000"]["change_type"] == xh.AT_OR_BEFORE_DEATH
    assert rows["death_episode:142.500"]["change_type"] == xh.RANK_ONLY
    for row in rows.values():
        assert row["prompts_identical_b_c"] is (row["change_type"] == xh.UNCHANGED)
