"""Experiment I stop point 3: pre-call gate, run once, guarded recovery, sheet."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import coaching_units_experiment_i as xi
import coaching_units_experiment_i_inputs as xii
import coaching_units_experiment_i_results as xres
import coaching_units_experiment_i_run as xr
import pytest
from sealed_drift import undeclared_drift

from splatoon3_ai_coach.config import default_config_path, load_config

MANIFEST = xi.OUT_DIR / "inputs" / "manifest.json"
SEALED = (
    MANIFEST.is_file()
    and (xi.ANALYSIS_ROOT / f"{xi.MATCH_PREFIX}2026-10-11 18-18-00").is_dir()
)


def _cell(arm: str = "M", repeat: int = 1, cid: str = "death_episode:308.000") -> xr.Cell:
    return xr.Cell(
        match=f"{xi.MATCH_PREFIX}2026-10-11 22-57-30",
        cid=cid,
        eligible=True,
        arm=arm,
        repeat=repeat,
        file=f"{arm}/x.user_prompt.txt",
        system=f"system_prompt_{arm}.txt",
    )


def _record(cell: xr.Cell, **extra) -> dict:
    return {"call_id": cell.call_id, **cell.__dict__, "attempt": 1, **extra}


def _write(out: Path, record: dict) -> None:
    xr._write_json_atomic(out / "outputs" / "calls" / f"{record['call_id']}.json", record)


def _log(out: Path, cell: xr.Cell, error: str | None) -> None:
    xr._log(out, {"call_id": cell.call_id, "event": "end", "call_error": error})


def test_recovery_recalls_only_missing_or_provider_error(tmp_path: Path) -> None:
    ok, parse, error, missing = _cell("A"), _cell("M"), _cell("A", 2), _cell("M", 2)
    _write(tmp_path, _record(ok, call_error=None, assessment={"assessment": "x"}))
    _write(tmp_path, _record(parse, call_error=None, parse_error="ValueError"))
    _write(tmp_path, _record(error, call_error="Cursor request failed"))
    for cell, err in ((ok, None), (parse, None), (error, "Cursor request failed")):
        _log(tmp_path, cell, err)
    todo, problems = xr.recovery_plan(tmp_path, [ok, parse, error, missing])
    assert todo == [error, missing] and problems == []
    with pytest.raises(SystemExit, match="model reply"):
        xr.archive_failed(tmp_path, ok)
    xr.archive_failed(tmp_path, error)
    archived = tmp_path / "outputs" / "failed_attempts" / f"{error.call_id}.attempt1.json"
    assert json.loads(archived.read_text())["call_error"] == "Cursor request failed"


def test_recovery_refuses_when_ambiguous(tmp_path: Path) -> None:
    lost, other = _cell("A"), _cell("M")
    _log(tmp_path, lost, None)
    calls = tmp_path / "outputs" / "calls"
    _write(tmp_path, {**_record(other, call_error=None), "arm": "A"})
    (calls / "stranger.json").write_text("{}")
    (calls / f"{_cell('A', 2).call_id}.json").write_text("{not json")
    _, problems = xr.recovery_plan(tmp_path, [lost, other, _cell("A", 2)])
    joined = " ".join(problems)
    assert "log shows a reply but no record" in joined
    assert "identity mismatch" in joined
    assert "unexpected record stranger.json" in joined
    assert "unparseable record" in joined


def test_run_makes_no_call_when_gate_fails(tmp_path: Path, monkeypatch) -> None:
    failed = {"passed": False, "checks": [{"name": "x", "ok": False, "problems": ["p"]}]}
    monkeypatch.setattr(xr, "precall_gate", lambda *a, **k: failed)

    def no_provider(*_a, **_k):
        raise AssertionError("provider built after a failed gate")

    monkeypatch.setattr(xr.xhr, "_provider", no_provider)
    with pytest.raises(SystemExit, match="0 calls made"):
        xr.run(tmp_path, None, xi.PROVIDER, xi.MODEL)
    assert not (tmp_path / "outputs").exists()
    assert (tmp_path / "precall_gate_failed.json").is_file()


def test_outputs_check_and_recover_needs_passed_gate(tmp_path: Path) -> None:
    assert xr.outputs_absent_check(tmp_path)["ok"]
    (tmp_path / "outputs").mkdir()
    assert not xr.outputs_absent_check(tmp_path)["ok"]
    with pytest.raises(SystemExit, match="did not pass its gate"):
        xr.recover(tmp_path, None, xi.PROVIDER, xi.MODEL)


def _design_manifest(eligible: int, deaths: int = 22) -> tuple[dict, dict]:
    rows = [
        {"match": f"m{i}", "scenario_id": "death_episode:1.000", "eligible": i < eligible}
        for i in range(deaths)
    ]
    calls = [
        {
            "match": xi.MATCH_PREFIX + d["match"],
            "cid": d["scenario_id"],
            "eligible": d["eligible"],
            "arm": arm,
            "file": f"{arm}/{d['match']}",
            "system": f"system_prompt_{arm}.txt",
        }
        for d in rows
        for arm in xi.ARMS
    ]
    return {"deaths": rows}, {"arms": list(xi.ARMS), "repeats": 2, "calls": calls}


def test_count_problems() -> None:
    assert xr.count_problems(*_design_manifest(14)) == []
    assert xr.count_problems(*_design_manifest(15))
    assert xr.count_problems(*_design_manifest(14, deaths=21))


VALIDATION = {
    "blocked": False,
    "hit_count": 0,
    "causal_claim_hits": [],
    "unsupported_recommendation_hits": [],
    "unresolved_evidence_paths": [],
    "integrity_hits": [
        {
            "rule": "R5",
            "check": "map_absence",
            "text": "No map check was observed in the 20.5s since you were back in play.",
        }
    ],
}
BLOCK = {
    "back_in_play_source": "previous_respawn",
    "seconds_back_in_play": 20.5,
    "map_checks_since_back_in_play": 0,
    "seconds_since_alive_map_check": None,
    "respawn_grace_seconds": 2.0,
    "threshold_seconds": 10.0,
    "eligible": True,
}
ASSESSMENT = {
    "assessment": "No map check was observed in the 20.5s since you were back in play.",
    "recommendations": ["Take a quick look at the map before moving up."],
}


def test_check_record_keeps_production_and_adjusted_counts() -> None:
    m = xres.check_record(
        _record(_cell("M"), validation=VALIDATION, assessment=ASSESSMENT),
        {"map_awareness": BLOCK},
    )
    assert (m["validator_hits"], m["r5_window_exempt"], m["adjusted"]) == (1, 1, 0)
    assert m["mentions_map_check"] and not m["value_fidelity_errors"]
    assert m["map_rule_violations"] == []
    a = xres.check_record(
        _record(_cell("A"), validation=VALIDATION, assessment=ASSESSMENT), {}
    )
    assert (a["validator_hits"], a["adjusted"]) == (1, 1)
    assert a["map_rule_violations"] == ["advice_not_eligible"]
    failed = xres.check_record(_record(_cell(), call_error="boom"), {})
    assert failed["ok"] is False


def test_summary_groups_and_coverage() -> None:
    cells = [_cell("A"), _cell("M")]
    found = [_record(_cell("A")), _record(_cell("A")), _record(_cell("A", 2))]
    cover = xres.coverage(cells, found)
    assert cover["missing"] == [xres.cell_key(_cell("M"))]
    assert cover["duplicate"] == [xres.cell_key(_cell("A"))]
    assert cover["unexpected"] == [xres.cell_key(_cell("A", 2))]
    row = xres.check_record(
        _record(_cell("M"), validation=VALIDATION, assessment=ASSESSMENT),
        {"map_awareness": BLOCK},
    )
    doc = xres.summarize([row], cover)
    g = doc["arms"]["M"]["eligible"]
    assert g["validator_hits_production"] == 1 and g["validator_hits_adjusted"] == 0
    assert doc["decision_rule_applied"] is False
    assert doc["eligible_m_map_mentions"]["mentions"] == 1


def test_blind_sheet_hides_arm_and_is_seeded() -> None:
    records = [
        _record(
            _cell(arm, rep),
            assessment={"assessment": f"t {arm}{rep}", "recommendations": ["r"]},
        )
        for arm in xi.ARMS
        for rep in (1, 2)
    ]
    records.append(_record(_cell("A", cid="death_episode:9.000"), assessment=None))
    sheet, key = xres.blind_sheet(records)
    assert sheet == xres.blind_sheet(list(reversed(records)))[0]
    rows = list(csv.DictReader(io.StringIO(sheet)))
    assert len(rows) == 5
    assert not {"arm", "repeat", "eligible", "call_id"} & set(rows[0])
    assert "map_use_correct_incorrect_not_mentioned" in rows[0]
    key_rows = list(csv.DictReader(io.StringIO(key)))
    assert [r["output_id"] for r in key_rows] == [r["output_id"] for r in rows]


# Checks that compare the sealed inputs with *current* production; production
# changed after the seal (sealed_drift.VIEW_DRIFT), so these fail by design.
PRODUCTION_EQUALITY_CHECKS = {
    "rebuild_byte_equal_current_production",
    "frozen_invariants",
}


@pytest.mark.skipif(not SEALED, reason="sealed inputs or analyses not present")
def test_sealed_inputs_pass_the_gate_and_expand_to_88_cells() -> None:
    cells = xr.expected_cells(xr.load_manifest(xi.OUT_DIR))
    assert len(cells) == 88 and len({c.call_id for c in cells}) == 88
    assert sum(c.eligible for c in cells if c.arm == "M") == xi.UPTAKE_OF
    gate = xr.precall_gate(
        xi.OUT_DIR,
        load_config(default_config_path()),
        xi.PROVIDER,
        xi.MODEL,
        xr._check("outputs", []),
    )
    failing = {c["name"] for c in gate["checks"] if not c["ok"]}
    assert failing <= {"repository_state", *PRODUCTION_EQUALITY_CHECKS}
    invariants = next(c for c in gate["checks"] if c["name"] == "frozen_invariants")
    assert all(
        p.endswith(": A prompt differs from Experiment H arm A")
        for p in invariants["problems"]
    )
    assert xr.seal_problems(xi.OUT_DIR, seal="a82fcdc")


@pytest.mark.skipif(not SEALED, reason="sealed inputs or analyses not present")
def test_sealed_inputs_differ_from_production_only_by_declared_drift() -> None:
    design = xii.load_design(xi.OUT_DIR)
    _, arms = xii.arm_inputs(design, load_config(default_config_path()))
    inputs = xi.OUT_DIR / "inputs"
    drift = {}
    for rel, text in xii.input_files(design, arms).items():
        sealed = (inputs / rel).read_text(encoding="utf-8")
        if rel.endswith(".user_prompt.txt"):
            drift[rel] = undeclared_drift(sealed, text)
        elif sealed != text:
            drift[rel] = ["differs"]
    assert {rel: problems for rel, problems in drift.items() if problems} == {}
