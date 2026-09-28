"""Library re-scoring report: comparison, tallies, and markdown output."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from importance_report import (
    ROSTER_MISSING,
    ROSTER_SAMPLE,
    ROSTER_STALE,
    UNRECORDED,
    FactorTally,
    MatchComparison,
    RankedDeath,
    SnapshotCoverage,
    build_report,
    find_analyses,
    read_old_ranking,
    roster_status,
    snapshot_coverage,
)

from splatoon3_ai_coach.coach.claim_catalog import RosterSample
from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.config.paths import default_config_path


@pytest.fixture(scope="module")
def app_config() -> AppConfig:
    return load_config(default_config_path())


def _death(
    cid: str,
    *,
    selected: bool,
    factors=(),
    roster=None,
    status=None,
    gap=None,
) -> RankedDeath:
    if status is None:
        status = ROSTER_SAMPLE if roster else ROSTER_MISSING
    return RankedDeath(
        candidate_id=cid,
        score=float(len(factors)),
        selected=selected,
        factors=tuple(factors),
        roster=roster,
        roster_status=status,
        roster_gap_seconds=gap,
    )


def _match(old: list[RankedDeath], new: list[RankedDeath]) -> MatchComparison:
    return MatchComparison(
        name="m1",
        old_hash="aaaa",
        new_hash="bbbb",
        old={d.candidate_id: d for d in old},
        new={d.candidate_id: d for d in new},
    )


def test_entered_and_left_top_n() -> None:
    match = _match(
        [
            _death("d1", selected=True),
            _death("d2", selected=True),
            _death("d3", selected=False),
        ],
        [
            _death("d1", selected=True),
            _death("d2", selected=False),
            _death("d3", selected=True),
        ],
    )
    assert match.entered() == ["d3"]
    assert match.left() == ["d2"]


def test_factor_tally_counts_active_and_top_n() -> None:
    tally = FactorTally()
    tally.add(
        {
            "d1": _death(
                "d1", selected=True, factors=["death_while_outnumbered"], roster="3v4"
            ),
            "d2": _death("d2", selected=False, factors=["death_while_outnumbered"]),
        }
    )
    assert tally.deaths == 2
    assert tally.with_roster == 1
    assert tally.active["death_while_outnumbered"] == 2
    assert tally.top_n["death_while_outnumbered"] == 1


def test_report_names_both_config_hashes_and_movement(app_config: AppConfig) -> None:
    match = _match(
        [_death("d1", selected=True), _death("d2", selected=False)],
        [
            _death("d1", selected=False),
            _death(
                "d2", selected=True, factors=["death_while_outnumbered"], roster="3v4"
            ),
        ],
    )
    report = build_report([match], "bbbb", app_config)
    assert "Current config hash: `bbbb`" in report
    assert "before `aaaa`, now `bbbb`" in report
    assert "Entered top N: `d2`" in report
    assert "roster before death 3v4" in report
    assert "Left top N: `d1`" in report
    assert "| `death_while_outnumbered` | 1 | 1 | 0 | 0 |" in report


def test_report_without_ranking_on_disk_lists_current_top_n(
    app_config: AppConfig,
) -> None:
    match = MatchComparison(
        name="fresh",
        old_hash=UNRECORDED,
        new_hash="bbbb",
        old={},
        new={"d1": _death("d1", selected=True)},
    )
    report = build_report([match], "bbbb", app_config)
    assert "No ranking on disk. Top N now:" in report
    assert "- Top N: `d1`" in report
    assert "Every death is in the top N" in report


def test_report_roster_coverage_by_match_and_death(app_config: AppConfig) -> None:
    match = MatchComparison(
        name="m1",
        old_hash=UNRECORDED,
        new_hash="bbbb",
        old={},
        new={
            "d1": _death("d1", selected=True, roster="3v4"),
            "d2": _death("d2", selected=True, roster="4v4", status=ROSTER_STALE, gap=6.0),
            "d3": _death("d3", selected=False),
        },
        snapshots=SnapshotCoverage(in_match=200, with_counts=150),
    )
    empty = MatchComparison(
        name="m2", old_hash=UNRECORDED, new_hash="bbbb", old={}, new={}
    )
    report = build_report([match, empty], "bbbb", app_config)
    assert "Matches with roster counts on in-match snapshots: 1 of 2." in report
    assert "With a pre-death sample: 1. Stale" in report
    assert "No sample: 1." in report
    assert "| m1 | 150/200 (75%) | 3 | 1 | 1 | 1 |" in report
    assert "| m2 | 0/0 | 0 | 0 | 0 | 0 |" in report
    assert "roster before death 4v4 (stale, 6.0s gap)" in report


def test_report_names_baseline_root(app_config: AppConfig, tmp_path: Path) -> None:
    report = build_report([], UNRECORDED, app_config, baseline_root=tmp_path)
    assert f"Before = current config on the analyses under `{tmp_path}`." in report


def test_snapshot_coverage_counts_in_match_with_both_counts(tmp_path: Path) -> None:
    snaps = [
        {"match_phase": "intro", "ally_alive_count": 4, "opponent_alive_count": 4},
        {"match_phase": "in_match", "ally_alive_count": 4, "opponent_alive_count": 3},
        {"match_phase": "in_match", "ally_alive_count": 4, "opponent_alive_count": None},
        {"match_phase": "in_match"},
    ]
    (tmp_path / "vision_manifest.json").write_text(
        json.dumps({"state_snapshots": snaps}), encoding="utf-8"
    )
    assert snapshot_coverage(tmp_path) == SnapshotCoverage(in_match=3, with_counts=1)


def test_roster_status_flags_samples_held_across_observation_gaps(
    app_config: AppConfig,
) -> None:
    sample = RosterSample(
        video_time=40.0,
        ally_alive_count=3,
        opponent_alive_count=4,
        source_path="primary_context.players.trajectory[0]",
    )
    unit = SimpleNamespace(roster_before_death=sample)
    no_sample = SimpleNamespace(roster_before_death=None)

    def bundle(*times: float) -> SimpleNamespace:
        obs = tuple(SimpleNamespace(video_time=t) for t in times)
        return SimpleNamespace(player_count_clock=SimpleNamespace(observations=obs))

    fresh = roster_status(unit, 50.0, bundle(40.0, 49.0, 49.5, 51.0), app_config)
    assert fresh == (ROSTER_SAMPLE, 0.0)
    status, gap = roster_status(unit, 50.0, bundle(40.0, 43.5, 52.0), app_config)
    assert status == ROSTER_STALE
    assert gap == pytest.approx(6.0)
    assert roster_status(no_sample, 50.0, bundle(49.5), app_config) == (
        ROSTER_MISSING,
        None,
    )


def test_find_analyses_skips_excluded(tmp_path: Path) -> None:
    for folder in ("keep", "skip/inner"):
        path = tmp_path / folder
        path.mkdir(parents=True)
        for name in ("scenarios.json", "scenario_contexts.json", "vision_manifest.json"):
            (path / name).write_text("[]", encoding="utf-8")
    found = find_analyses(tmp_path, exclude=(tmp_path / "skip",))
    assert found == [(tmp_path / "keep").resolve()]


def test_find_analyses_and_read_old_ranking(tmp_path: Path) -> None:
    match_dir = tmp_path / "a" / "match"
    inputs = match_dir / "coach_inputs"
    inputs.mkdir(parents=True)
    for name in ("scenarios.json", "scenario_contexts.json", "vision_manifest.json"):
        (match_dir / name).write_text("[]", encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "scenarios.json").write_text("[]", encoding="utf-8")
    (inputs / "coaching_index.json").write_text(
        json.dumps(
            [
                {
                    "candidate_id": "death_episode:10.000",
                    "importance_score": 2.5,
                    "selected_for_llm": True,
                    "active_factors": ["death_last_ally_alive"],
                }
            ]
        ),
        encoding="utf-8",
    )
    (inputs / "coach_inputs_meta.json").write_text(
        json.dumps({"importance_config_hash": "cafe"}), encoding="utf-8"
    )
    assert find_analyses(tmp_path) == [match_dir.resolve()]
    old_hash, deaths = read_old_ranking(match_dir)
    assert old_hash == "cafe"
    assert deaths["death_episode:10.000"].selected is True
    assert read_old_ranking(tmp_path / "b") == (UNRECORDED, {})
