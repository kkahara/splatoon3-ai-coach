"""Splat Zones count-factor section of tools/reports/ranking_competition.py."""

from __future__ import annotations

from ranking_competition import Death, Match, splat_zones_section

from splatoon3_ai_coach.config import default_config_path, load_config


def _death(
    cid: str, observed: set[str], *, diff: int | None, selected: bool = False
) -> Death:
    return Death(
        match="m",
        cid=cid,
        t=float(len(cid)),
        factors=frozenset(),
        score=0.0,
        rank=1,
        selected=selected,
        observed=frozenset(observed),
        remaining_diff=diff,
    )


def test_section_counts_only_splat_zones_matches() -> None:
    sz = Match(
        name="sz",
        battle_mode_id="splat_zones",
        deaths=[
            _death("a", {"death_while_behind_in_count", "death_final_30s"}, diff=-40),
            _death("b", {"death_while_ahead_in_count"}, diff=12, selected=True),
            _death("c", set(), diff=None),
        ],
    )
    other = Match(
        name="tc",
        battle_mode_id="tower_control",
        deaths=[_death("d", {"death_while_behind_in_count"}, diff=-50)],
    )
    config = load_config(default_config_path())
    text = "\n".join(splat_zones_section([sz, other], config))
    assert "Splat Zones matches: 1 of 2; deaths: 3" in text
    assert "with both counts observed at the pre-death sample: 2" in text
    assert "| `death_while_behind_in_count` | yes | 1 | 0 | 0 | 1 |" in text
    assert "| `death_while_ahead_in_count` | yes | 1 | 0 | 1 | 1 |" in text
    assert "| `death_while_behind_in_count` | 1 | 1 | 0 | 0 | 0 | 0 |" in text
    assert "| <= -30 | 1 |" in text
    assert "| 10..29 | 1 |" in text
