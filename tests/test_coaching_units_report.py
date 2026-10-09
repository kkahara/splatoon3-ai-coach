"""Coaching-unit research report: grouping rules, zero-score rule, D inputs, blinding.

Synthetic matches only; the report itself runs on real analyses.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from coaching_units import (
    OBSERVATIONS_HEADER,
    POLICIES,
    MatchData,
    Obs,
    deferred_inputs,
    multi_scoring_episodes,
    policy_metrics,
    pool,
    rating_sheet,
    redundant_units,
    references,
    select,
    unit_prompt,
    write_files,
    zero_score_members,
)
from coaching_units_sections import _engagement_stats, _low_ink_stats, experiment_d
from ranking_episodes import Candidate, episodes

from splatoon3_ai_coach.coach import llm_client

GAP = 20.0
MODS = frozenset({"death_first_30s"})
REDEATH = "death_quick_redeath"
OWN = "death_own_splat_before"
FINAL = "death_final_30s"


def _obs(
    t: float,
    factors: set[str] = frozenset(),
    *,
    prev: float | None = None,
    templates: set[str] = frozenset(),
    match: str = "m1",
) -> Obs:
    weights = {REDEATH: 3.0, OWN: 2.0, FINAL: 1.0}
    body = json.dumps({"death_time_video": t}, sort_keys=True, separators=(",", ":"))
    return Obs(
        match=match,
        cid=f"death_episode:{t:.3f}",
        t=t,
        score=sum(weights[f] for f in factors),
        factors=frozenset(factors),
        prev_death_t=prev,
        prompt="PREAMBLE " + body,
        body=body,
        coachable=frozenset(factors),
        templates=frozenset(templates),
        statements=tuple(sorted(factors)),
    )


def _match(*deaths: Obs, name: str = "m1") -> MatchData:
    return MatchData(name=name, identity=f"id-{name}", deaths=list(deaths))


def _mixed_episode() -> MatchData:
    """Zero-score death, then own-splat and re-death deaths linked to it."""
    return _match(
        _obs(100.0),
        _obs(112.0, {OWN}, prev=100.0),
        _obs(125.0, {REDEATH}, prev=112.0, templates={"rec_redeath_reset"}),
    )


def _chain() -> MatchData:
    """Three re-deaths sharing one factor and template."""
    rec = {"rec_redeath_reset"}
    return _match(
        _obs(200.0, {REDEATH}, prev=185.0, templates=rec),
        _obs(215.0, {REDEATH}, prev=200.0, templates=rec),
        _obs(230.0, {REDEATH, FINAL}, prev=215.0, templates=rec),
        name="m2",
    )


def test_links_are_transitive_with_unbounded_span() -> None:
    match = _match(*(_obs(t) for t in (0.0, 15.0, 30.0, 45.0)))
    (group,) = episodes(match, GAP)
    assert [d.t for d in group] == [0.0, 15.0, 30.0, 45.0]
    assert group[-1].t - group[0].t > GAP


def test_zero_score_death_may_be_referenced_as_previous_death() -> None:
    match = _mixed_episode()
    pairs = references(match)
    assert [(d.t, p.t) for d, p in pairs] == [(112.0, 100.0)]
    assert match.deaths[1].prev_death_t == 100.0


@pytest.mark.parametrize("policy", POLICIES)
def test_zero_score_death_is_never_a_member_or_observation(policy: str) -> None:
    match = _mixed_episode()
    units = pool(match, policy, GAP, MODS)
    assert zero_score_members(units) == []
    scoring_members = {d.cid for u in units if u.score > 0 for d in u.members}
    assert "death_episode:100.000" not in scoring_members
    for unit in select(units, 3):
        assert "100.0" not in unit_prompt(unit)


def test_zero_score_guard_detects_a_promoted_member() -> None:
    zero, positive = _obs(100.0), _obs(112.0, {OWN})
    assert zero_score_members([Candidate([zero, positive])]) == [zero]


@pytest.mark.parametrize("policy", POLICIES)
def test_positive_deaths_are_observations_regardless_of_budget(policy: str) -> None:
    matches = [_mixed_episode(), _chain()]
    metrics = policy_metrics(matches, policy, GAP, 1, MODS)
    assert metrics["positive_total"] == 5
    assert metrics["positive_as_observations"] == 5
    assert metrics["positive_in_selected"] <= metrics["positive_as_observations"]
    assert metrics["zero_promoted"] == 0


def test_budget_effect_is_reported_separately_from_observations() -> None:
    metrics = policy_metrics([_chain()], "deaths", GAP, 1, MODS)
    assert metrics["positive_as_observations"] == 3
    assert metrics["positive_in_selected"] == 1


def test_episode_by_factor_splits_by_primary_factor_set() -> None:
    mixed = pool(_mixed_episode(), "episode_by_factor", GAP, MODS)
    assert sorted(len(u.members) for u in mixed if u.score > 0) == [1, 1]
    chain = pool(_chain(), "episode_by_factor", GAP, MODS)
    assert [len(u.members) for u in chain if u.score > 0] == [2, 1]
    episode = pool(_chain(), "episode", GAP, MODS)
    assert [len(u.members) for u in episode if u.score > 0] == [3]


def test_redundancy_and_distinct_opportunity_arithmetic() -> None:
    chain = _chain()
    picks = select(pool(chain, "deaths", GAP, MODS), 3)
    assert len(redundant_units(chain, picks, GAP, MODS)) == 2
    metrics = policy_metrics([chain, _mixed_episode()], "deaths", GAP, 3, MODS)
    # {re-death}, {re-death, final30} (final30 is not a modifier), {own}, {re-death}
    assert metrics["distinct_opportunities"] == 4
    assert metrics["anchors_lost_lead_only"] == 0
    grouped = policy_metrics([chain], "episode", GAP, 3, MODS)
    assert grouped["anchors_lost_lead_only"] == 2
    assert grouped["redundant_units"] == 0


def test_grouped_prompt_appends_member_views_verbatim() -> None:
    unit = Candidate(list(_chain().deaths))
    text = unit_prompt(unit)
    assert text.startswith(unit.lead.prompt + OBSERVATIONS_HEADER)
    for member in unit.members:
        if member is not unit.lead:
            assert member.body in text
    assert unit_prompt(Candidate([unit.lead])) == unit.lead.prompt


def test_deferred_inputs_cover_three_arms_and_are_byte_identical(
    tmp_path: Path,
) -> None:
    matches = [_mixed_episode(), _chain()]
    first = deferred_inputs(matches, GAP, MODS, "SYSTEM")
    assert first == deferred_inputs(matches, GAP, MODS, "SYSTEM")
    manifest = json.loads(first["manifest.json"])
    assert manifest["settings"]["llm_calls_made"] == 0
    assert manifest["settings"]["temperature"] == 0
    assert len(manifest["episodes"]) == sum(
        len(multi_scoring_episodes(m, GAP)) for m in matches
    )
    for episode in manifest["episodes"]:
        assert set(episode["arms"]) == {"individual", "episode", "episode_by_factor"}
    write_files(tmp_path / "a", first)
    write_files(tmp_path / "b", first)
    for rel in first:
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes()


def test_deferred_arms_exclude_zero_score_members_and_report_says_so() -> None:
    files = deferred_inputs([_mixed_episode()], GAP, MODS, "SYSTEM")
    zero_body = '"death_time_video":100.0'
    assert files
    assert not any(zero_body in text for text in files.values())
    section = "\n".join(experiment_d(files))
    assert "only the episode's positive-scoring deaths" in section
    assert "not how an episode's complete temporal context" in section


def test_no_llm_provider_is_constructed(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an LLM provider was constructed")

    for provider in (
        llm_client.OllamaProvider,
        llm_client.OpenAICompatibleProvider,
        llm_client.CursorProvider,
    ):
        monkeypatch.setattr(provider, "__init__", refuse)
    matches = [_mixed_episode(), _chain()]
    deferred_inputs(matches, GAP, MODS, "SYSTEM")
    rating_sheet(matches, GAP, 3, MODS)


def test_engagement_and_low_ink_feasibility_counts() -> None:
    match = _match(_obs(100.0))
    match.engagements = [
        {"start_time": 50.0, "end_time": 50.0, "context": {"splat_count": 1}},
        {
            "start_time": 90.0,
            "end_time": 96.0,
            "context": {"splat_count": 2, "following_death_id": "death:100.000"},
        },
    ]
    match.death_intervals = [(100.0, 108.5)]
    match.low_ink = [(40.0, 45.0), (98.0, 101.0), (108.5, 110.0)]
    eng = _engagement_stats([match])
    assert eng == {
        "count": 2,
        "median_duration": 3.0,
        "single_splat": 1,
        "linked_to_death": 1,
    }
    assert _low_ink_stats([match]) == {"count": 3, "without_death": 1}


def test_rating_sheet_is_blind_and_stable() -> None:
    matches = [_mixed_episode(), _chain()]
    sheet, key = rating_sheet(matches, GAP, 3, MODS)
    assert (sheet, key) == rating_sheet(matches, GAP, 3, MODS)
    rows = list(csv.DictReader(io.StringIO(sheet)))
    expected_ids = [f"unit_{n:03d}" for n in range(1, len(rows) + 1)]
    assert [r["unit_id"] for r in rows] == expected_ids
    lowered = sheet.lower()
    for leak in ("individual", "episode_by_factor", "policy", "model", "arm", "deaths,"):
        assert leak not in lowered
    key_rows = list(csv.DictReader(io.StringIO(key)))
    assert {r["unit_id"] for r in key_rows} == {r["unit_id"] for r in rows}
    assert all(r["policies"] for r in key_rows)
