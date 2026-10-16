"""Tests for the held-out roster reach validation tool (tools/diagnostics)."""

from __future__ import annotations

from pathlib import Path

import pytest
import roster_heldout_reach as heldout


def _row(video: str, t: float, a1: tuple[str, str], o3=("alive", "alive")) -> dict:
    return {
        "video": video,
        "t": t,
        "slots": {
            "a1": {"production": a1[0], "candidate": a1[1]},
            "o3": {"production": o3[0], "candidate": o3[1]},
        },
    }


def test_candidate_is_frozen() -> None:
    assert heldout.CANDIDATE == {"a1": 16, "o3": 8}
    assert heldout.right_of("candidate", "a1") == 16
    assert heldout.right_of("candidate", "o3") == 8
    assert heldout.right_of("candidate", "o4") == 0
    assert heldout.right_of("production", "a1") == 0


def test_video_date_handles_prefixes() -> None:
    assert heldout.video_date("en-2026-09-20 22-38-46.mov") == "2026-09-20"
    assert heldout.video_date("2026-09-04 23-02-31.mov") == "2026-09-04"
    assert heldout.video_date("ja_lumira_hub_inkjet_2026-09-16 19-33-24.mov") == (
        "2026-09-16"
    )


def test_discordant_runs_split_on_gap_video_and_slot_set() -> None:
    same = ("alive", "x")
    rows = [
        _row("a.mov", 0.5, same),
        _row("a.mov", 1.5, same),
        _row("a.mov", 3.5, same),
        _row("a.mov", 4.5, ("alive", "alive"), o3=same),
        _row("b.mov", 5.5, ("alive", "alive"), o3=same),
        _row("b.mov", 6.5, ("x", "x")),
    ]
    runs = heldout.discordant_runs(rows)
    assert [[(r["video"], r["t"]) for r in run] for run in runs] == [
        [("a.mov", 0.5), ("a.mov", 1.5)],
        [("a.mov", 3.5)],
        [("a.mov", 4.5)],
        [("b.mov", 5.5)],
    ]


def test_select_samples_caps_and_tags() -> None:
    rows = [
        _row(f"v{v}.mov", t + 0.5, ("alive", "x") if t == 3 else ("alive", "alive"))
        for v in range(120)
        for t in range(10)
    ]
    samples = heldout.select_samples(rows, seed=1)
    random_ = [s for s in samples if "random" in s["strata"]]
    discordant = [s for s in samples if "discordant" in s["strata"]]
    assert len(random_) == heldout.RANDOM_N
    per_video: dict[str, int] = {}
    for sample in random_:
        per_video[sample["video"]] = per_video.get(sample["video"], 0) + 1
    assert max(per_video.values()) <= heldout.RANDOM_PER_VIDEO
    assert len(discordant) == heldout.DISCORDANT_RUNS
    assert all(s["t"] == 3.5 for s in discordant)
    assert len({(s["video"], s["t"]) for s in samples}) == len(samples)
    assert [s["sample_id"] for s in samples][:2] == ["H001", "H002"]
    assert heldout.select_samples(rows, seed=1) == samples


def test_paired_transitions() -> None:
    rows = [
        {"human": "x", "production": "x", "candidate": "x"},
        {"human": "x", "production": "alive", "candidate": "x"},
        {"human": "alive", "production": "alive", "candidate": "x"},
        {"human": "alive", "production": "x", "candidate": "x"},
    ]
    assert heldout.paired(rows) == {
        "correct->correct": 1,
        "wrong->correct": 1,
        "correct->wrong": 1,
        "wrong->wrong": 1,
    }


def _reach_row(sample_id: str, slot: str, center: float, human: str = "x") -> dict:
    return {
        "sample_id": sample_id,
        "slot": slot,
        "strata": ["random"],
        "human": human,
        "free_template": "player-x-1",
        "free_score": 0.01,
        "free_center_x": center,
        "production": "alive",
        "candidate": "x",
    }


def test_x_reach_flags_production_and_candidate() -> None:
    reach = {
        "intervals": {"player-x-1": [500.0, 595.0]},
        "winner": {"production": "player-x-1", "candidate": "player-x-1"},
    }
    rows = [
        _reach_row("H001", "a1", 590.0),
        _reach_row("H002", "a1", 607.5),
        _reach_row("H003", "a1", 615.0),
        _reach_row("H004", "a2", 650.0),
        _reach_row("H005", "a1", 600.0, human="alive"),
    ]
    reaches = {(r["sample_id"], r["slot"]): reach for r in rows}
    out = heldout.x_reach(rows, reaches)
    assert [
        (r["reachable_production_any_template"], r["reachable_candidate_any_template"])
        for r in out
    ] == [(True, True), (False, True), (False, False)]
    assert out[1]["candidate_free_template_reach"] == [500.0, 611.0]


def test_x_reach_uses_the_winning_template_not_only_the_free_one() -> None:
    row = {**_reach_row("H010", "o3", 1279.6), "free_template": "player-x-3"}
    reach = {
        "intervals": {"player-x-3": [1234.6, 1269.6], "player-x-1": [1240.0, 1273.1]},
        "winner": {"production": "player-x-3", "candidate": "player-x-1"},
    }
    (item,) = heldout.x_reach([row], {("H010", "o3"): reach})
    assert item["reachable_candidate_free_template"] is False
    assert item["reachable_candidate_any_template"] is True
    assert item["candidate_winner_template"] == "player-x-1"
    assert item["candidate_winner_reach"] == [1240.0, 1281.1]
    assert item["reachable_candidate_winner_template"] is True
    assert item["reachable_production_any_template"] is False


def test_read_labels_rejects_unknown_values(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    header = "sample_id,hud_visible,a1,a2,a3,a4,o1,o2,o3,o4,notes\n"
    path.write_text(header + "H001,visible,x,alive,?,alive,alive,alive,x,alive,\n")
    assert heldout.read_labels(path)["H001"]["a3"] == "?"
    path.write_text(header + "H001,visible,X,alive,?,alive,alive,alive,x,alive,\n")
    with pytest.raises(ValueError):
        heldout.read_labels(path)
