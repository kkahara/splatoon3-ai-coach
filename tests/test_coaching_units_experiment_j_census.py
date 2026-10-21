"""Experiment J stop point 2B: ordered walk, terminal recording, seeded draws."""

from __future__ import annotations

import csv

import coaching_units_experiment_j_census as xc
import pytest

SEEDS = {"sample": 2026102101, "shuffle": 2026102102, "p3": 2026102103}


def _death(order, t, eligible=False, observable=True, c=None):
    return {
        "order": order,
        "video": f"v{order}.mov",
        "cid": f"death_episode:{t:.3f}",
        "death_time": t,
        "eligible": eligible,
        "map_observable": observable,
        "c_point": c,
    }


def _fake(per_recording):
    """process_recording stand-in: deaths per order, records calls."""
    calls = []

    def process(rec, movies, root, config, analyze):
        calls.append(rec["order"])
        found = per_recording.get(rec["order"], [])
        status = {"order": rec["order"], "status": "analyzed", "eligible": 0}
        return status, found

    return process, calls


def test_walk_finishes_terminal_recording_and_never_starts_the_next(monkeypatch):
    per = {
        1: [_death(1, 10.0, True), _death(1, 20.0, True)],
        3: [_death(3, 5.0, True), _death(3, 9.0, True), _death(3, 30.0)],
        4: [_death(4, 1.0, True)],
    }
    process, calls = _fake(per)
    monkeypatch.setattr(xc, "process_recording", process)
    seq = [{"order": n, "video": f"v{n}.mov"} for n in (1, 3, 4)]
    census = xc.walk(seq, None, None, None, target=3)
    assert calls == [1, 3]
    assert census["terminal"]["order"] == 3
    assert len(census["deaths"]) == 5


def test_walk_without_reaching_target_reports_it(monkeypatch):
    process, calls = _fake({1: [_death(1, 10.0, True)]})
    monkeypatch.setattr(xc, "process_recording", process)
    census = xc.walk([{"order": 1, "video": "v1.mov"}], None, None, None, target=3)
    assert census["reached_target"] is False and census["terminal"] is None


def _census(n_eligible=55, n_frame=70):
    deaths = [_death(1 + i // 4, float(i), eligible=True) for i in range(n_eligible)]
    deaths += [_death(100 + i // 4, float(i)) for i in range(n_frame)]
    deaths += [_death(200, 1.0, observable=False)]
    return {"deaths": deaths}


def test_select_first50_frame_sample_and_p3():
    chosen = xc.select(_census(), SEEDS)
    assert len(chosen["first50"]) == 50 and len(chosen["eligible_after_first50"]) == 5
    assert chosen["first50"][0] == "001|death_episode:0.000"
    assert len(chosen["frame"]) == 70 and len(chosen["sample"]) == 50
    assert set(chosen["p3"]) <= set(chosen["sample"]) and len(chosen["p3"]) == 20
    assert len(chosen["rows"]) == 100
    assert [r["row_id"] for r in chosen["rows"]][:2] == ["r001", "r002"]
    groups = {r["group"] for r in chosen["rows"]}
    assert groups == {"eligible_first50", "not_eligible_sample"}


def test_select_is_deterministic_and_seed_dependent():
    a, b = xc.select(_census(), SEEDS), xc.select(_census(), SEEDS)
    assert a == b
    c = xc.select(_census(), {**SEEDS, "shuffle": 1})
    assert [r["cid"] for r in a["rows"]] != [r["cid"] for r in c["rows"]]


def test_small_frame_takes_everything():
    chosen = xc.select(_census(n_eligible=50, n_frame=12), SEEDS)
    assert len(chosen["sample"]) == 12 and len(chosen["p3"]) == 12


def test_worksheet_hides_eligibility_and_reason(tmp_path):
    chosen = xc.select(_census(), SEEDS)
    rows = xc.worksheet_rows(chosen["rows"], tmp_path)
    path = tmp_path / "sheet.csv"
    xc.write_worksheet(rows, path)
    header = next(csv.reader(path.open()))
    assert tuple(header) == xc.WORKSHEET_COLUMNS
    text = path.read_text()
    for hidden in ("eligible", "sample", "p3", "back_in_play", "c_point", "group"):
        assert hidden not in text
    with pytest.raises(SystemExit):
        xc.write_worksheet(rows, path)


def test_c_point_priority_and_language():
    assert xc.c_point({"death_own_splat_before", "death_low_ink"}) == "death_low_ink"
    assert xc.c_point({"death_final_30s"}) is None
    assert xc.ui_language("jp-2026-09-20 23-46-44.mov") == "ja"
    assert xc.ui_language("en_SW2--2026-10-19 23-13-06.mov") == "en"


def test_frozen_seeds_come_from_design_json():
    assert xc.seeds() == SEEDS
