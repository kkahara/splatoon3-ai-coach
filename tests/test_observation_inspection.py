"""Tests for the additive roster/map provenance sidecar."""

from __future__ import annotations

import json

from splatoon3_ai_coach.analysis.observation_inspection import (
    export_scenario_observation_evidence,
)
from splatoon3_ai_coach.config.models import ScenarioBuilderConfig


def _config() -> ScenarioBuilderConfig:
    return ScenarioBuilderConfig(
        context_lookback_seconds=8,
        context_lookforward_seconds=2,
        context_max_gap_seconds=1,
    )


def _write_analysis(tmp_path, *, hidden: bool) -> None:
    frames = []
    snapshots = []
    for index, time in enumerate((10.0, 10.5, 11.0, 11.5, 17.0, 17.5)):
        unavailable = hidden and 11.0 <= time <= 11.5
        frames.append(
            {
                "frame_id": f"frame:{index}",
                "timestamp": time,
                "source": "cadence",
                "detections": [
                    {
                        "id": f"pc:{index}",
                        "detector_name": "player_count",
                        "detector_version": "v1",
                        "confidence": 0 if unavailable else 1,
                        "reading": {
                            "kind": "player_count",
                            "roster_visible": not unavailable,
                            "ally_dead_slots": [],
                            "opponent_dead_slots": [],
                        },
                    }
                ],
            }
        )
        snapshots.append(
            {
                "timestamp": time,
                "ally_alive_count": None if unavailable else 3,
                "opponent_alive_count": None if unavailable else 3,
                "player_count_confidence": None if unavailable else 1,
            }
        )
    manifest = {"game_events": [], "frame_results": frames, "state_snapshots": snapshots}
    scenario = {
        "scenario_id": "death_episode:18.000",
        "scenario_type": "death_episode",
        "start_time": 18.0,
        "end_time": 18.0,
        "event_ids": [],
        "confidence": 1,
        "outcome": "incomplete",
    }
    maps = [
        {
            "video_time": 17.0,
            "stage_id": "mahi_mahi_resort",
            "ally_share_of_detected_ink": 0.4,
            "opponent_share_of_detected_ink": 0.5,
            "visible_ink_fraction": 0.9,
            "total_sample_pixels": 0,
        }
    ]
    for name, value in (
        ("vision_manifest.json", manifest),
        ("scenarios.json", [scenario]),
        ("map_observations.json", maps),
        ("scenario_contexts.json", []),
    ):
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")


def test_gap_ledger_distinguishes_hidden_from_continuous_and_preserves_map(
    tmp_path,
) -> None:
    _write_analysis(tmp_path, hidden=True)
    artifact = export_scenario_observation_evidence(tmp_path, _config())
    context = artifact.contexts[0]
    assert [sample.paired_counts for sample in context.roster_samples] == [
        "present",
        "present",
        "unavailable",
        "unavailable",
        "present",
        "present",
    ]
    assert len(context.roster_gaps) == 1
    assert context.roster_gaps[0].first_unavailable_video_time == 11.0
    assert context.roster_gaps[0].right_observation_video_time == 17.0
    assert context.map_samples[0].pixel_totals["total_sample_pixels"] == 0
    assert (tmp_path / "scenario_observation_evidence.json").is_file()
    assert (
        json.loads((tmp_path / "vision_manifest.json").read_text())["state_snapshots"][2][
            "ally_alive_count"
        ]
        is None
    )


def test_legacy_missing_map_fields_remain_null_and_sidecar_is_additive(tmp_path) -> None:
    _write_analysis(tmp_path, hidden=False)
    before = (tmp_path / "scenarios.json").read_bytes()
    artifact = export_scenario_observation_evidence(tmp_path, _config())
    record = artifact.contexts[0].map_samples[0]
    assert record.pixel_totals["classified_pixels"] is None
    assert record.sample_mask_source is None
    assert (tmp_path / "scenarios.json").read_bytes() == before
