"""Frozen GAME! tape rule and held-out evaluation (tools/diagnostics)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "diagnostics"))

import game_tape_validate as gv  # noqa: E402


def _rows(spec: list[tuple[float, float, float]]) -> list[dict[str, float]]:
    return [{"t": t, "fill": f, "contrast": c} for t, f, c in spec]


def test_frozen_constants() -> None:
    assert (gv.FILL_MIN, gv.CONTRAST_MIN, gv.CONFIRM_FRAMES) == (0.73, 0.35, 2)
    assert gv.ONSET_TOLERANCE == 1.0


def test_detect_runs_requires_two_consecutive_positive_frames() -> None:
    rows = _rows(
        [
            (10.0, 0.9, 0.5),
            (10.5, 0.2, 0.1),
            (20.0, 0.94, 0.08),
            (30.0, 0.8, 0.5),
            (30.5, 0.9, 0.6),
            (31.0, 0.9, 0.6),
            (32.0, 0.9, 0.6),
        ]
    )
    runs = gv.detect_runs(rows)
    assert [(r.start, r.end, r.frames) for r in runs] == [(30.0, 31.0, 3)]


def test_language_from_prefix_only() -> None:
    assert gv.language("en-2026-10-18 20-23-41.mov") == "en"
    assert gv.language("en_SW2--2026-10-19 23-13-06.mov") == "en"
    assert gv.language("ja-2026-09-05 09-20-18.mov") == "ja"
    assert gv.language("jp_2026-09-20 23-34-42.mov") == "ja"
    assert gv.language("jn_robo_rom_en_2026-09-15 21-19-51.mov") == "unknown"
    assert gv.language("2026-07-05 20-28-22.mov") == "unknown"


def _det(video: str, lang: str, starts: list[float]) -> dict:
    return {"video": video, "language": lang, "runs": [{"start": s} for s in starts]}


def test_evaluate_on_time_late_false_and_missed() -> None:
    detections = [
        _det("a", "en", [100.0]),
        _det("b", "en", [205.0]),
        _det("c", "ja", [50.0]),
        _det("d", "ja", []),
    ]
    truth = {
        "a": {"true_onsets": "99.5"},
        "b": {"true_onsets": "200.0"},
        "c": {"true_onsets": ""},
        "d": {"true_onsets": "300.0"},
    }
    result = gv.evaluate(detections, truth)
    assert result["total"] == {"endings": 3, "detected": 2, "on_time": 1}
    assert result["false_runs"] == [("c", 50.0)]
    assert result["missed"] == [("d", 300.0)]
    assert result["passed"] is False


def test_evaluate_passes_clean_set() -> None:
    detections = [_det(f"v{i}", "en", [100.0]) for i in range(12)]
    truth = {f"v{i}": {"true_onsets": "100.0"} for i in range(12)}
    result = gv.evaluate(detections, truth)
    assert result["recall_overall"] == 1.0 and result["on_time_rate"] == 1.0
    assert result["passed"] is True


def test_grid_sample_includes_every_video_without_a_run() -> None:
    detections = [_det(f"r{i}", "en", [1.0]) for i in range(30)] + [
        _det("none1", "en", []),
        _det("none2", "ja", []),
    ]
    sample = gv.grid_sample(detections)
    assert {"none1", "none2"} <= set(sample)
    assert len(sample) == 2 + gv.GRID_SAMPLE
    assert sample == gv.grid_sample(detections)


def test_ending_type_from_clock_slack() -> None:
    assert gv.ending_type(None, 300.0) == "unknown"
    assert gv.ending_type((299.5, 1.0), 300.0) == "time"
    assert gv.ending_type((209.5, 7.0), 216.5) == "time"
    assert gv.ending_type((167.0, 148.0), 168.0) == "knockout"
    assert gv.ending_type((300.0, 1.0), 320.0) == "overtime"


def test_hue_name_bins() -> None:
    assert gv.hue_name(None) == "unknown"
    assert [gv.hue_name(h) for h in (0, 14, 28, 50, 90, 110, 140, 157, 175)] == [
        "red",
        "orange",
        "yellow",
        "lime",
        "teal",
        "blue",
        "purple",
        "pink",
        "red",
    ]
