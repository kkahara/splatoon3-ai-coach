"""Experiment J stop point 2A: candidate pool, recording rules, frozen order."""

from __future__ import annotations

import json

import coaching_units_experiment_j as xj
import pytest


def _files(movies, names, size=10):
    movies.mkdir(parents=True, exist_ok=True)
    for name in names:
        (movies / name).write_bytes(b"x" * size)


def _report(tmp_path, folders):
    report = tmp_path / "report"
    for folder, text in folders.items():
        (report / folder).mkdir(parents=True)
        (report / folder / "notes.md").write_text(text, encoding="utf-8")
    return report


def _build(tmp_path, heldout_names, live=(), folders=None, tests=None):
    movies = tmp_path / "Movies"
    _files(movies, [*heldout_names, *live])
    heldout = [{"video": n, "bytes": 10} for n in heldout_names]
    report = _report(tmp_path, folders or {"coaching_units_experiment_h": ""})
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    for name, text in (tests or {}).items():
        (tests_dir / name).write_text(text, encoding="utf-8")
    return xj.build_census_order(
        heldout, movies, report, tests_dir, identity=lambda p: p.name
    )


def test_timestamp_and_language_rules():
    assert (
        xj.recording_timestamp("en_SW2--2026-10-19 23-20-28.mov") == "2026-10-19 23-20-28"
    )
    assert xj.recording_timestamp("2026-10-10_23-43-31") == "2026-10-10 23-43-31"
    assert xj.recording_timestamp("clip.mov") is None
    assert [xj.language(n) for n in ("en-a", "ja_b", "jp-c", "jn-d", "2026-x")] == [
        "en",
        "ja",
        "ja",
        "unknown",
        "unknown",
    ]


def test_order_is_timestamp_then_full_path(tmp_path):
    names = [
        "ja_b_2026-09-16 19-49-36.mov",
        "en-2026-09-01 10-00-00.mov",
        "ja_a_2026-09-16 19-49-36.mp4",
    ]
    doc = _build(tmp_path, names)
    assert [r["video"] for r in doc["recordings"]] == [
        "en-2026-09-01 10-00-00.mov",
        "ja_a_2026-09-16 19-49-36.mp4",
        "ja_b_2026-09-16 19-49-36.mov",
    ]
    assert [r["order"] for r in doc["recordings"]] == [1, 2, 3]
    assert doc["recordings"][0]["video_identity"] == "en-2026-09-01 10-00-00.mov"


def test_recording_level_exclusions(tmp_path):
    names = [
        "en-2026-10-11 18-18-00.mov",
        "en-review-2026-09-01 10-00-00.mov",
        "2026-07-04 16-06-41.mp4",
        "en-no-time.mov",
        "en-2026-10-10 23-43-31.mov",
        "en-2026-08-01 10-00-00.mov",
    ]
    doc = _build(
        tmp_path,
        names,
        folders={"coaching_units_experiment_h": "2026-10-11 18-18-00"},
        tests={"test_citation_contract.py": '"2026-10-10_23-43-31"'},
    )
    reasons = {e["video"]: e["reasons"] for e in doc["excluded"]}
    assert reasons["en-2026-10-11 18-18-00.mov"] == ["used_in:experiment_h"]
    assert reasons["en-review-2026-09-01 10-00-00.mov"] == ["review_source_name"]
    assert reasons["2026-07-04 16-06-41.mp4"] == ["unknown_ui_language"]
    assert reasons["en-no-time.mov"] == ["no_parseable_timestamp"]
    assert reasons["en-2026-10-10 23-43-31.mov"] == ["used_in:citation_contract_fixtures"]
    assert [r["video"] for r in doc["recordings"]] == ["en-2026-08-01 10-00-00.mov"]


def test_other_references_annotate_without_excluding(tmp_path):
    doc = _build(
        tmp_path,
        ["en-2026-09-21 23-13-21.mov"],
        folders={
            "coaching_units_experiment_h": "",
            "coaching_units_experiment_d": "2026-09-21 23-13-21",
        },
    )
    assert doc["recordings"][0]["other_prior_references"] == [
        "coaching_units_experiment_d"
    ]
    assert doc["counts"]["ordered_with_other_prior_references"] == 1


def test_live_recordings_join_the_pool_and_size_is_checked(tmp_path):
    movies = tmp_path / "Movies"
    _files(
        movies,
        ["en-2026-09-01 10-00-00.mov", "en-live-playerview-2026-10-13 10-00-00.mov"],
    )
    heldout = [{"video": "en-2026-09-01 10-00-00.mov", "bytes": 99}]
    report = _report(tmp_path, {"coaching_units_experiment_h": ""})
    (tmp_path / "tests").mkdir()
    doc = xj.build_census_order(
        heldout, movies, report, tmp_path / "tests", identity=lambda p: ""
    )
    assert [r["sources"] for r in doc["recordings"]] == [["live_playerview"]]
    assert doc["excluded"][0]["reasons"] == ["size_differs_from_heldout_list"]


def test_prior_judgment_categories():
    assert xj.prior_judgment_category(["roster_evidence"]) is None
    assert xj.prior_judgment_category(["final30_score_gate", "roster_evidence"]) is None
    assert xj.prior_judgment_category(["rescore_v2", "roster_evidence"]) == (
        "death_importance"
    )
    assert xj.prior_judgment_category(["rescore_v2", "coaching_units_experiment_d"]) == (
        "coaching_output"
    )
    assert xj.prior_judgment_category([]) is None


def test_census_exclusions_keep_sealed_order_positions():
    order_doc = {
        "recordings": [
            {
                "order": 1,
                "video": "a",
                "video_identity": "x1",
                "other_prior_references": [],
            },
            {
                "order": 2,
                "video": "b",
                "video_identity": "x2",
                "other_prior_references": ["coaching_units_rating_sheet.csv"],
            },
            {
                "order": 3,
                "video": "c",
                "video_identity": "x3",
                "other_prior_references": ["roster_evidence"],
            },
            {
                "order": 4,
                "video": "d",
                "video_identity": "x4",
                "other_prior_references": ["1hz_experiment"],
            },
        ]
    }
    doc = xj.build_census_exclusions(order_doc)
    assert [(e["order"], e["category"]) for e in doc["excluded"]] == [
        (2, "coaching_output"),
        (4, "death_importance"),
    ]
    assert [s["order"] for s in doc["sequence_2b"]] == [1, 3]


def test_display_path_hides_the_home_directory():
    home = xj.Path.home()
    assert xj.display_path(home / "Movies" / "a.mov") == "~/Movies/a.mov"
    assert xj.display_path(xj.Path("/tmp/a.mov")) == "/tmp/a.mov"


def test_census_order_is_write_once(tmp_path):
    out = tmp_path / "census_order.json"
    xj.write_census_order({"a": 1}, out)
    assert json.loads(out.read_text()) == {"a": 1}
    with pytest.raises(SystemExit):
        xj.write_census_order({"a": 2}, out)
