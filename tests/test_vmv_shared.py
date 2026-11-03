"""Shared manifest loader and media helpers used by the React VMV site."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from vision_manifest_viewer.loader import load_manifest_view
from vision_manifest_viewer.server import parse_byte_range, review_video_content_type


def _write_manifest(tmp_path: Path) -> Path:
    """Write the smallest persisted manifest needed by the shared loader."""
    path = tmp_path / "vision_manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "video_identity": "shared-test",
                "analysis": {"language": "ja"},
                "timing": {"video_duration_seconds": 12.0, "cadence_frame_count": 1},
                "frame_results": [
                    {
                        "frame_id": "f5",
                        "timestamp": 5.0,
                        "source": "cadence",
                        "source_pts": 900,
                        "source_time_base_num": 1,
                        "source_time_base_den": 180,
                        "detections": [
                            {
                                "id": "rain-count",
                                "detector_name": "rainmaker_score",
                                "confidence": 0.9,
                                "reading": {"pods": [{"value": 72, "usable": True}]},
                            }
                        ],
                    }
                ],
                "state_snapshots": [],
                "game_events": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_shared_loader_preserves_observation_provenance(tmp_path: Path) -> None:
    view = load_manifest_view(_write_manifest(tmp_path))
    assert view.summary.language == "ja"
    observation = view.observations[0]
    assert observation.id == "rain-count"
    assert observation.positive is True
    assert observation.frame_id == "f5"
    assert observation.source_pts == 900
    assert observation.source_time_base_den == 180


def test_shared_loader_passes_exported_objective_context_to_react(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    (tmp_path / "scenarios.json").write_text(
        json.dumps(
            [
                {
                    "scenario_id": "death_episode:5.000",
                    "scenario_type": "death_episode",
                    "start_time": 5.0,
                    "end_time": 7.0,
                    "event_ids": [],
                    "confidence": 1.0,
                    "outcome": "recovered",
                }
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "scenario_contexts.json").write_text(
        json.dumps(
            [
                {
                    "scenario_id": "death_episode:5.000",
                    "objective": {
                        "mode": "rainmaker",
                        "counts": {
                            "at_anchor": {"video_time": 5.0, "ally_remaining": 72}
                        },
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    card = load_manifest_view(manifest).scenario_evidence[0]
    assert card.objective == {
        "mode": "rainmaker",
        "counts": {"at_anchor": {"video_time": 5.0, "ally_remaining": 72}},
    }


@pytest.mark.parametrize(
    ("header", "size", "expected"),
    [
        ("bytes=0-9", 100, (0, 9)),
        ("bytes=90-", 100, (90, 99)),
        ("bytes=-10", 100, (90, 99)),
        (None, 100, None),
        ("bytes=500-600", 100, None),
        ("bytes=9-2", 100, None),
    ],
)
def test_react_media_range_helper(
    header: str | None,
    size: int,
    expected: tuple[int, int] | None,
) -> None:
    assert parse_byte_range(header, size) == expected


def test_react_media_content_type_is_preserved(tmp_path: Path) -> None:
    assert review_video_content_type(tmp_path / "clip.mov") == "video/quicktime"
    assert review_video_content_type(tmp_path / "clip.mp4") == "video/mp4"
