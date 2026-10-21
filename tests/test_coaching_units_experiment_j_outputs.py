"""Experiment J stop point 4 outputs: sealed-corpus check, frozen checks per
output, ablated versions, blind absolute sheet (no model calls, no labels)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

import coaching_units_experiment_j_dry_run as xr
import coaching_units_experiment_j_outputs as xo
import coaching_units_experiment_j_run as xrun
import pytest

GOOD = json.dumps(
    {
        "assessment": "Fact.",
        "claims": [],
        "evidence_used": [],
        "limitations": [],
        "recommendations": ["Do a thing."],
        "selected_claim_ids": [],
    }
)
FORBIDDEN = ("arm", "repeat", "population", "map_relevant", "ideal_coaching_note")


class Provider:
    def generation_settings(self):
        return {}

    def input_hashes(self, system, prompt):
        return {}

    def complete(self, system, prompt):
        return GOOD


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cell(pop, arm, repeat, n):
    death = f"{n:03d}|death_episode:{n * 10}.000"
    return xrun.Cell(
        call_id=f"{pop}_{arm}__{n:03d}__r{repeat}",
        population=pop,
        arm=arm,
        repeat=repeat,
        death_id=death,
        file=f"{arm}/{n:03d}.user_prompt.txt",
        system=f"system_prompt_{arm}.txt",
    )


def _layout():
    cells = [
        _cell(pop, arm, r, n)
        for pop, arms, deaths in (
            ("P1", "AM", (1, 2)),
            ("P2", "AM", (3,)),
            ("P3", "M", (4,)),
        )
        for n in deaths
        for arm in arms
        for r in (1, 2)
    ] + [_cell("C", "C", r, 1) for r in (1, 2)]
    deaths = {
        f"{n:03d}|death_episode:{n * 10}.000": {
            "order": n,
            "video": f"rec {n}.mp4",
            "death_time": n * 10.0,
        }
        for n in range(1, 5)
    }
    records = {
        c.call_id: {
            "assessment": {"assessment": f"Text {c.call_id}.", "recommendations": []}
        }
        for c in cells
    }
    return cells, deaths, records


def _sheet(seed=7):
    cells, deaths, records = _layout()
    sheet, key = xo.blind_sheet(cells, records, deaths, seed, movies=Path("/m"))
    rows = list(csv.DictReader(io.StringIO(sheet)))
    keys = list(csv.DictReader(io.StringIO(key)))
    return cells, rows, keys


# --- blind absolute sheet ---------------------------------------------------------


def test_sheet_rows_are_p1_p2_a_m_and_c_outputs_only():
    cells, rows, keys = _sheet()
    expected = {c.call_id for c in cells if c.population != "P3"}
    assert {k["call_id"] for k in keys} == expected
    assert len(rows) == len(keys) == len(expected)
    assert [r["output_id"] for r in rows] == [
        f"o{n:03d}" for n in range(1, len(rows) + 1)
    ]
    assert [k["output_id"] for k in keys] == [r["output_id"] for r in rows]


def test_sheet_shows_no_arm_repeat_population_label_or_note():
    _, rows, keys = _sheet()
    header = list(rows[0])
    assert header == [*xo.SHEET_FIXED, *xo.RATING_COLUMNS]
    assert not any(f in h for h in header for f in FORBIDDEN)
    assert all(r[c] == "" for r in rows for c in xo.RATING_COLUMNS)
    assert {k["arm"] for k in keys} == {"A", "M", "C"}


def test_sheet_order_comes_only_from_the_seed():
    _, first, keys_a = _sheet(seed=7)
    _, again, keys_b = _sheet(seed=7)
    _, other, keys_c = _sheet(seed=8)
    assert first == again and keys_a == keys_b
    assert [k["call_id"] for k in keys_a] != [k["call_id"] for k in keys_c]


def test_sheet_row_matches_its_key_death_and_output():
    cells, rows, keys = _sheet()
    _, deaths, records = _layout()
    for row, key in zip(rows, keys, strict=True):
        death = deaths[key["death_id"]]
        assert row["recording"] == death["video"]
        assert float(row["death_seconds"]) == death["death_time"]
        assert row["assessment"] == records[key["call_id"]]["assessment"]["assessment"]


def test_real_absolute_seed_is_the_frozen_one():
    assert xo.absolute_seed() == 2026102104


# --- summary ----------------------------------------------------------------------


def _row(pop, arm, **kw):
    base = {
        "population": pop,
        "arm": arm,
        "parsed": True,
        "validator_hits": 2,
        "c_adjustment": 0,
        "r5_window_exempt": 1,
        "adjusted_hits": 1,
        "blocked": False,
        "any_recommendation": True,
    }
    return {**base, **kw}


def test_summary_groups_counts_and_applies_no_rule():
    rows = [
        _row("P1", "A"),
        _row(
            "P1",
            "M",
            uptake=True,
            map_rule_violations=["causal"],
            value_fidelity_errors=[],
            ablation={"ablatable": False, "reasons": ["unparsed"]},
        ),
        _row("P1", "M", parsed=False, uptake=False),
        _row("C", "C", c_adjustment=1, adjusted_hits=0, r5_window_exempt=0),
    ]
    doc = xo.summarize(rows)
    assert (
        doc["decision_rule_applied"] is False
        and "c_intended_point_uptake" in doc["not_built"]
    )
    m = doc["groups"]["P1 M"]
    assert (m["calls"], m["parsed"], m["uptake"]) == (2, 1, 1)
    assert m["validator_hits_production"] == 2 and m["validator_hits_adjusted"] == 1
    assert m["map_rule_violation_codes"] == {"causal": 1}
    assert (m["ablation_applicable"], m["ablatable"]) == (1, 0)
    assert doc["groups"]["C C"]["c_adjustment"] == 1
    assert doc["groups"]["all M"]["calls"] == 2
    assert "P3 M" not in doc["groups"]
    assert "decision rule not applied" in xo.summary_markdown(doc)


# --- checks on the real sealed inputs --------------------------------------------


def test_every_sealed_prompt_yields_its_view():
    cells = xrun.expected_cells(xrun.load_manifest())
    views = {c.file: xo.sealed_view(xo.OUT, c) for c in cells}
    assert len(views) == 106
    m_eligible = [f for f, v in views.items() if f.startswith("M/") and v["extra_point"]]
    assert len(m_eligible) == 40


def test_check_row_uses_the_sealed_checks_and_keeps_ablated_text_out():
    cell = next(
        c
        for c in xrun.expected_cells(xrun.load_manifest())
        if (c.population, c.arm) == ("P1", "M")
    )
    view = xo.sealed_view(xo.OUT, cell)
    record = {"assessment": xr.compliant(view)}
    row = xo.check_row(cell, record, view)
    assert row["uptake"] and row["adjusted_hits"] == 0
    assert row["ablation"]["ablatable"] and "assessment" not in row["ablation"]
    ablated = xo.ablation_row(cell, record, view)
    assert ablated["ablatable"] and ablated["call_id"] == cell.call_id


def test_ablation_row_only_for_m_on_eligible_deaths():
    cells = xrun.expected_cells(xrun.load_manifest())
    p3 = next(c for c in cells if c.population == "P3")
    a = next(c for c in cells if c.arm == "A")
    for cell in (p3, a):
        view = xo.sealed_view(xo.OUT, cell)
        assert xo.ablation_row(cell, {"assessment": None}, view) is None


# --- sealed corpus ------------------------------------------------------------------


def _corpus(tmp_path):
    out = tmp_path / "j"
    files = {"system_prompt_A.txt": "s", "A/001.user_prompt.txt": "p"}
    call = {
        "call_id": "A__001__r1",
        "population": "P1",
        "arm": "A",
        "repeat": 1,
        "death_id": "001|death_episode:10.000",
        "file": "A/001.user_prompt.txt",
        "system": "system_prompt_A.txt",
    }
    files["manifest.json"] = json.dumps({"calls": [call], "repeats": 1})
    for rel, text in files.items():
        (out / "inputs" / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / "inputs" / rel).write_text(text, encoding="utf-8")
    lines = [f"{_sha(t.encode())}  inputs/{rel}" for rel, t in sorted(files.items())]
    (out / "inputs.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    meta = {"provider": "p", "model": "m", "seal_commit": "s", "run_commit": "r"}
    cell = xrun.Cell(**call)
    xrun.run_cells(out, [cell], Provider(), meta, "run")
    xrun.seal_outputs(out)
    digest = _sha((out / "outputs.sha256").read_bytes())
    (out / "SHA256SUMS").write_text(f"{digest}  outputs.sha256\n", encoding="utf-8")
    return out


def test_corpus_check_passes_on_a_sealed_corpus_and_fails_on_tampering(tmp_path):
    out = _corpus(tmp_path)
    assert xo.corpus_problems(out) == []
    record = out / "outputs" / "calls" / "A__001__r1.json"
    record.write_text(record.read_text() + " ", encoding="utf-8")
    assert any("differs" in p for p in xo.corpus_problems(out))


def test_corpus_check_rejects_an_unsealed_extra_file(tmp_path):
    out = _corpus(tmp_path)
    (out / "outputs" / "calls" / "stray.txt").write_text("x", encoding="utf-8")
    assert xo.corpus_problems(out)


def test_build_never_overwrites(tmp_path):
    out = _corpus(tmp_path)
    (out / "rating_sheet.csv").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit, match="never overwritten"):
        xo.build(out)


def test_real_corpus_verifies():
    assert xo.corpus_problems() == []


def test_outputs_tool_never_opens_labels_or_notes():
    source = Path(xo.__file__).read_text(encoding="utf-8")
    assert "relevance_labels" not in source and "LABELS" not in source
    assert "ideal_coaching_note" not in source
