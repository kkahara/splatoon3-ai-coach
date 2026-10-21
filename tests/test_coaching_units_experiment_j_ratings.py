"""Experiment J absolute ratings: validation rules and the one-time seal
(synthetic sheets only; the real filled sheet is never read here)."""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

import coaching_units_experiment_j_ratings as xs
import pytest

ROWS = 4


def _csv(rows):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(xs.HEADER)
    writer.writerows(rows)
    return buf.getvalue()


def _blank_rows():
    return [
        [f"o{n:03d}", f"rec {n}.mp4", "2:49", "169.0", "~/Movies/x.mp4", f"Text {n}.", ""]
        + [""] * len(xs.SCORES)
        + ["", ""]
        for n in range(1, ROWS + 1)
    ]


def _filled_rows(**overrides):
    rows = []
    for raw in _blank_rows():
        row = dict(zip(xs.HEADER, raw, strict=True))
        row.update(
            usefulness_1to5="3",
            actionable_1to5="4",
            specific_to_this_death_1to5="2",
            supported_by_footage_yes_no="yes",
        )
        row.update(overrides.get(row["output_id"], {}))
        rows.append([row[c] for c in xs.HEADER])
    return rows


def _problems(rows, blank=None):
    return xs.sheet_problems(_csv(rows), blank or _csv(_blank_rows()), ROWS)


def test_complete_sheet_passes():
    assert _problems(_filled_rows()) == []


def test_byte_order_mark_and_normalized_yes_no_pass():
    rows = _filled_rows(o001={"supported_by_footage_yes_no": " Yes "})
    text = "\ufeff" + _csv(rows)
    assert xs.sheet_problems(text, _csv(_blank_rows()), ROWS) == []


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("usefulness_1to5", ""),
        ("usefulness_1to5", "6"),
        ("actionable_1to5", "0"),
        ("specific_to_this_death_1to5", "3.5"),
        ("supported_by_footage_yes_no", "maybe"),
        ("supported_by_footage_yes_no", ""),
    ],
)
def test_missing_or_out_of_range_values_fail_without_echoing_them(column, value):
    problems = _problems(_filled_rows(o002={column: value}))
    assert problems == [f"o002: {column} invalid"]


def test_notes_are_optional_free_text():
    assert _problems(_filled_rows(o003={"notes": "anything, even 7"})) == []


def test_edited_fixed_cell_fails():
    problems = _problems(_filled_rows(o004={"death_time": "02:49:00"}))
    assert problems == ["o004: fixed column death_time changed"]


def test_reordered_rows_fail():
    rows = _filled_rows()
    rows[0], rows[1] = rows[1], rows[0]
    assert _problems(rows) == ["output_id set or order differs from the blank sheet"]


def test_missing_row_fails():
    problems = _problems(_filled_rows()[:-1])
    assert problems[0] == f"{ROWS - 1} rows, expected {ROWS}"


def test_extra_column_fails():
    text = _csv(_filled_rows()).replace("notes\n", "notes,extra\n", 1)
    assert xs.sheet_problems(text, _csv(_blank_rows()), ROWS) == [
        "header differs from the frozen header"
    ]


def test_stray_cell_fails():
    rows = _filled_rows()
    rows[2].append("stray")
    assert _problems(rows) == ["o003: stray or missing cells"]


# --- seal -----------------------------------------------------------------------


def _layout(tmp_path, monkeypatch, filled_rows):
    out = tmp_path / "j"
    out.mkdir()
    blank = _csv(_blank_rows())
    digest = hashlib.sha256(blank.encode()).hexdigest()
    (out / "stop_point_4.sha256").write_text(f"{digest}  rating_sheet.csv\n")
    (out / "SHA256SUMS").write_text("")
    (out / "rating_sheet.csv").write_text(_csv(filled_rows), encoding="utf-8")
    monkeypatch.setattr(xs, "blank_text", lambda out=None, commit=None: blank)
    return out


def test_seal_hashes_the_file_as_is_and_runs_once(tmp_path, monkeypatch):
    out = _layout(tmp_path, monkeypatch, _filled_rows())
    before = (out / "rating_sheet.csv").read_bytes()
    record = xs.seal(out, ROWS)
    assert (out / "rating_sheet.csv").read_bytes() == before
    assert record["rating_sheet_sha256"] == hashlib.sha256(before).hexdigest()
    sums = (out / "SHA256SUMS").read_text().splitlines()
    assert len(sums) == 1 and sums[0].endswith("  absolute_ratings.sha256")
    seal_lines = (out / "absolute_ratings.sha256").read_text().splitlines()
    assert [line.split()[1] for line in seal_lines] == [
        "rating_sheet.csv",
        "absolute_ratings_seal.json",
    ]
    with pytest.raises(SystemExit, match="already sealed"):
        xs.seal(out, ROWS)


def test_seal_refuses_an_invalid_sheet(tmp_path, monkeypatch):
    rows = _filled_rows(o001={"usefulness_1to5": ""})
    out = _layout(tmp_path, monkeypatch, rows)
    with pytest.raises(SystemExit, match="not sealed"):
        xs.seal(out, ROWS)
    assert not (out / "absolute_ratings.sha256").exists()
    assert (out / "SHA256SUMS").read_text() == ""


def test_seal_refuses_a_blank_that_is_not_the_sealed_one(tmp_path, monkeypatch):
    out = _layout(tmp_path, monkeypatch, _filled_rows())
    (out / "stop_point_4.sha256").write_text(f"{'0' * 64}  rating_sheet.csv\n")
    with pytest.raises(SystemExit, match="stop point 4 seal"):
        xs.seal(out, ROWS)


# --- real sealed blank (never the filled sheet) ----------------------------------


def test_real_blank_at_the_commit_is_the_sealed_one():
    blank = xs.blank_text()
    assert xs.blank_problems(blank) == []
    head, rows = xs._table(blank)
    assert tuple(head) == xs.HEADER and len(rows) == xs.EXPECTED_ROWS


def test_ratings_tool_never_opens_key_or_hidden_artifacts():
    source = Path(xs.__file__).read_text(encoding="utf-8")
    for name in (
        "rating_key",
        "automatic_",
        "ablations",
        "relevance_labels",
        "populations.json",
        "ideal_coaching_note",
    ):
        assert name not in source
