"""Frozen blind sample and label scorer for gameplay-HUD roster evidence."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "diagnostics"))

import roster_label_sample as sample  # noqa: E402


def _candidate(
    index: int,
    stratum: sample.Stratum,
    source_class: sample.SourceClass = "live_gameplay",
) -> sample.Candidate:
    return sample.Candidate(
        analysis_dir=f"/analysis/m{index // 10}",
        video_path=f"/videos/m{index // 10}.mov",
        source_class=source_class,
        frame_index=index,
        frame_id=f"f{index}",
        video_time=float(index),
        source_pts=index,
        strata=[stratum],
        pre_death=stratum == "pre_death",
        raw_visible=True,
        raw_slots=["alive"] * 8,
        raw_avb=[4, 4],
        fused_avb=[4, 4],
        scores=[0.2] * 8,
        edge_energy=[50.0] * 8,
    )


def test_seeded_selection_freezes_equal_strata_without_duplicates() -> None:
    candidates = [
        _candidate(index * 10 + sub, stratum)
        for index, stratum in enumerate(sample.STRATA)
        for sub in range(5)
    ]
    one = sample.select_candidates(candidates, 24, 17)
    two = sample.select_candidates(candidates, 24, 17)
    assert [(item.frame_id, stratum) for item, stratum in one] == [
        (item.frame_id, stratum) for item, stratum in two
    ]
    assert len({sample._candidate_id(item) for item, _ in one}) == 24
    assert set(stratum for _, stratum in one) == set(sample.STRATA)


def test_selection_preserves_replay_quota_within_each_stratum() -> None:
    candidates = [
        _candidate(index * 100 + sub, stratum, source_class)
        for index, stratum in enumerate(sample.STRATA)
        for source_class in ("live_gameplay", "replay_derived")
        for sub in range(20)
    ]
    selected = sample.select_candidates(candidates, 120, 17, replay_n=24)
    assert len(selected) == 120
    assert sum(item.source_class == "replay_derived" for item, _ in selected) == 24
    for stratum in sample.STRATA:
        rows = [item for item, assigned in selected if assigned == stratum]
        assert len(rows) == 20
        assert sum(item.source_class == "replay_derived" for item in rows) == 4


def _write_labels(path: Path, *, ambiguous: bool = False) -> None:
    row = {
        "sample_id": "R001",
        "hud_visible": "visible",
        **{name: "alive" for name in sample.SLOT_NAMES},
        "notes": "",
    }
    if ambiguous:
        row["a1"] = "?"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sample.LABEL_FIELDS)
        writer.writeheader()
        writer.writerow(row)


def _write_key(path: Path) -> None:
    key = sample.SampleKey(
        seed=1,
        samples=[
            sample.KeySample(
                sample_id="R001",
                source_class="live_gameplay",
                stratum="pre_death",
                raw_visible=True,
                raw_slots=["alive"] * 8,
                raw_avb=[4, 4],
                fused_avb=[4, 4],
                pre_death=True,
                scores=[0.2] * 8,
                edge_energy=[50] * 8,
                decoded_video_time=1.0,
                image_sha256="a" * 64,
            )
        ],
    )
    path.write_text(json.dumps(key.model_dump(mode="json")), "utf-8")


def test_scorer_preserves_reliability_ladder(tmp_path: Path) -> None:
    labels, key = tmp_path / "labels.csv", tmp_path / "key.json"
    _write_labels(labels)
    _write_key(key)
    result = sample.score_labels(labels, key).overall
    assert result.human_determinable_rate == 1.0
    assert result.side_count_accuracy == 1.0
    assert result.raw_avb_accuracy == 1.0
    assert result.fused_avb_accuracy == 1.0
    assert result.pre_death_fused_avb_accuracy == 1.0


def test_question_mark_is_ambiguity_not_an_error(tmp_path: Path) -> None:
    labels, key = tmp_path / "labels.csv", tmp_path / "key.json"
    _write_labels(labels, ambiguous=True)
    _write_key(key)
    result = sample.score_labels(labels, key).overall
    assert result.human_ambiguous_slots == 1
    assert result.detector_alive_on_human_ambiguous == 1
    assert result.slot_fp_x == 0
    assert result.slot_fn_x == 0
    assert result.raw_avb_comparable == 0
    assert result.pre_death_fused_avb_comparable == 0
