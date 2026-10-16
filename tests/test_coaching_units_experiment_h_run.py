"""Experiment H stop point 3: seal guard, run-once recovery, coverage, blind sheet."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import coaching_units_experiment_h as xh
import coaching_units_experiment_h_inputs as xi
import coaching_units_experiment_h_results as xres
import coaching_units_experiment_h_run as xr
import pytest

from splatoon3_ai_coach.config import default_config_path, load_config

MANIFEST = xh.OUT_DIR / "inputs" / "manifest.json"
SEALED = MANIFEST.is_file() and (xh.REFRESHED_ROOT / xh.MATCHES[0]).is_dir()


def _cell(arm: str = "C", repeat: int = 1, cid: str = "death_episode:85.000") -> xr.Cell:
    return xr.Cell(
        match="en-live-playerview-2026-10-11 18-25-16",
        cid=cid,
        set="treatment",
        change_type=xh.AT_OR_BEFORE_DEATH,
        arm=arm,
        repeat=repeat,
        file=f"{arm}/x.user_prompt.txt",
        system=f"system_prompt_{arm}.txt",
    )


def _record(cell: xr.Cell, **extra) -> dict:
    return {"call_id": cell.call_id, **cell.__dict__, "attempt": 1, **extra}


def _write(out: Path, record: dict) -> None:
    xr._write_json(out / "outputs" / "calls" / f"{record['call_id']}.json", record)


def test_retry_only_missing_or_provider_error_cells(tmp_path: Path) -> None:
    ok, parse, error = _cell("A"), _cell("B"), _cell("C")
    missing = _cell("C", repeat=2)
    _write(tmp_path, _record(ok, call_error=None, assessment={"assessment": "x"}))
    _write(tmp_path, _record(parse, call_error=None, parse_error="ValueError"))
    _write(tmp_path, _record(error, call_error="Cursor request failed"))
    todo = xr.retry_cells(tmp_path, [ok, parse, error, missing])
    assert todo == [error, missing]
    xr.archive_failed(tmp_path, error)
    xr.archive_failed(tmp_path, missing)
    assert not (tmp_path / "outputs" / "calls" / f"{error.call_id}.json").exists()
    archived = tmp_path / "outputs" / "failed_attempts" / f"{error.call_id}.attempt1.json"
    assert json.loads(archived.read_text())["call_error"] == "Cursor request failed"


def test_run_refuses_existing_outputs_and_unregistered_model(tmp_path: Path) -> None:
    (tmp_path / "outputs").mkdir()
    config = load_config(default_config_path())
    with pytest.raises(SystemExit, match="run once"):
        xr.run(tmp_path, config, xh.PROVIDER, xh.MODEL)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "manifest.json").write_text(
        json.dumps(
            {"provider": xh.PROVIDER, "model": xh.MODEL, "repeats": 2, "calls": []}
        )
    )
    (tmp_path / "outputs" / "calls").mkdir()
    with pytest.raises(SystemExit, match="pre-registered"):
        xr.recover(tmp_path, config, xh.PROVIDER, "another-model")


def test_coverage_reports_missing_duplicate_unexpected() -> None:
    cells = [_cell("A"), _cell("B")]
    found = [_record(_cell("A")), _record(_cell("A")), _record(_cell("C"))]
    cover = xr.coverage(cells, found)
    assert cover["missing"] == [xr.cell_key(_cell("B"))]
    assert cover["duplicate"] == [xr.cell_key(_cell("A"))]
    assert cover["unexpected"] == [xr.cell_key(_cell("C"))]
    assert not any(xr.coverage(cells, [_record(c) for c in cells]).values())


VALIDATION = {
    "blocked": False,
    "hit_count": 0,
    "causal_claim_hits": [],
    "unsupported_recommendation_hits": [],
    "unresolved_evidence_paths": [],
    "integrity_hits": [],
}
ROSTER_VIEW = {"roster": {"at_death": {"ally_alive_count": 2, "opponent_alive_count": 4}}}


def test_check_record_reads_visible_text_against_own_view() -> None:
    assessment = {
        "assessment": "You were outnumbered 2v4 when you were splatted.",
        "recommendations": ["Take advantage of your special next time."],
        "limitations": ["It was 4v4 earlier."],
    }
    record = _record(_cell(), validation=VALIDATION, assessment=assessment)
    checked = xres.check_record(record, ROSTER_VIEW)
    assert checked["mentions_roster"] and checked["count_fidelity"]
    assert checked["cited_pairs"] == [(2, 4)]
    assert not xres.check_record(record, {"roster": None})["count_fidelity"]
    failed = xres.check_record(_record(_cell(), call_error="boom"), ROSTER_VIEW)
    assert failed["ok"] is False and failed["call_error"] == "boom"


def test_blind_sheet_hides_arm_and_is_seeded() -> None:
    records = [
        _record(
            _cell(arm, repeat),
            assessment={"assessment": f"text {arm}{repeat}", "recommendations": ["r"]},
        )
        for arm in xh.ARMS
        for repeat in (1, 2)
    ]
    records.append(_record(_cell("A", cid="death_episode:9.000"), assessment=None))
    sheet, key = xres.blind_sheet(records)
    assert sheet == xres.blind_sheet(list(reversed(records)))[0]
    rows = list(csv.DictReader(io.StringIO(sheet)))
    assert len(rows) == 7
    assert not {"arm", "repeat", "set", "call_id"} & set(rows[0])
    assert "roster_use_correct_incorrect_not_mentioned" in rows[0]
    assert not any(c in r["assessment"] for r in rows for c in ("__", "attempt"))
    assert any(r["assessment"].startswith("(no rateable output") for r in rows)
    key_rows = list(csv.DictReader(io.StringIO(key)))
    assert [r["output_id"] for r in key_rows] == [r["output_id"] for r in rows]
    assert {(r["arm"], r["repeat"]) for r in key_rows} >= {("A", "1"), ("C", "2")}


def test_divergence_pairs_repeat_with_repeat() -> None:
    def rec(arm: str, repeat: int, text: str) -> dict:
        return _record(
            _cell(arm, repeat), assessment={"assessment": text, "recommendations": []}
        )

    rows = xres.divergence_rows(
        [rec("B", 1, "same"), rec("C", 1, "same"), rec("B", 2, "abc"), rec("C", 2, "xyz")]
    )
    assert rows[0]["pairs"] == 2
    assert rows[0]["divergence"] == pytest.approx(0.5)


@pytest.mark.skipif(not SEALED, reason="sealed inputs or analyses not present")
def test_sealed_inputs_verify_and_expand_to_132_cells() -> None:
    manifest = xi.load_manifest(MANIFEST.parent)
    cells = xr.expected_cells(manifest)
    assert len(cells) == 132 and len({c.call_id for c in cells}) == 132
    assert xr.verify_sealed(xh.OUT_DIR, load_config(default_config_path())) == []
    assert xr.seal_problems(xh.OUT_DIR, seal="ea48b5f")
