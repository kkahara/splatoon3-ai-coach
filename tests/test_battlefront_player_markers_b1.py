"""Tests for the isolated player-marker review metrics."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

MODULE_PATH = (
    Path(__file__).parents[1]
    / "tools/diagnostics/battlefront_player_markers_b1/review_server.py"
)
SPEC = importlib.util.spec_from_file_location("b1_review_server", MODULE_PATH)
assert SPEC and SPEC.loader
REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW)


def test_metrics_wait_for_exhaustive_review(tmp_path: Path) -> None:
    """Do not report accuracy while references are incomplete or absent."""
    outputs = [
        {
            "frame_id": "m08_f07200",
            "split": "development",
            "candidates": [{"candidate_id": "c0001"}],
        }
    ]
    result = REVIEW.metrics(outputs, tmp_path)
    assert result["development"]["status"] == "awaiting_complete_human_labels"
    assert result["development"]["precision"] is None
    assert result["held_out"]["frames_total"] == 0


def test_metrics_use_reviewed_candidates_and_missed_markers(tmp_path: Path) -> None:
    """Compute split metrics from complete, explicit human decisions."""
    outputs = [
        {
            "frame_id": "m08_f07200",
            "split": "development",
            "candidates": [
                {"candidate_id": "c0001", "observed_color_side": "ally_color"},
                {"candidate_id": "c0002", "observed_color_side": "opponent_color"},
            ],
        },
        {
            "frame_id": "m08_f09000",
            "split": "held_out",
            "candidates": [],
        },
    ]
    tmp_path.joinpath("m08_f07200.r0001.json").write_text(
        json.dumps(
            {
                "review_complete": True,
                "candidate_decisions": [
                    {
                        "candidate_id": "c0001",
                        "decision": "accept",
                        "reviewer_team": "ally",
                    },
                    {
                        "candidate_id": "c0002",
                        "decision": "reject",
                        "reviewer_team": "unknown",
                    },
                ],
                "missed_markers": [{"reviewer_team": "opponent"}],
            }
        ),
        encoding="utf-8",
    )
    tmp_path.joinpath("m08_f09000.r0001.json").write_text(
        json.dumps(
            {"review_complete": True, "candidate_decisions": [], "missed_markers": []}
        ),
        encoding="utf-8",
    )
    result = REVIEW.metrics(outputs, tmp_path)
    development = result["development"]
    assert development["status"] == "measured"
    assert (development["tp"], development["fp"], development["fn"]) == (1, 1, 1)
    assert development["precision"] == 0.5
    assert development["recall"] == 0.5
    assert development["color_side_accuracy"] == 1.0
    assert development["team_association_accuracy"] is None
    assert development["team_prediction_unknown"] == 1
    assert result["held_out"]["status"] == "measured"


def test_map_coordinates_require_registered_polygon() -> None:
    """Review points outside the registered stage remain without map coordinates."""
    identity_points = [
        {"x": 0.2, "y": 0.2},
        {"x": 0.8, "y": 0.2},
        {"x": 0.8, "y": 0.9},
        {"x": 0.2, "y": 0.9},
    ]
    polygon_path = (
        Path(__file__).parents[1] / "configs/stage_maps/scorch_gorge/stage_mask.yaml"
    )
    manifest = {
        "stage": {
            "registration_status": "reviewed_same_recording",
            "registration": {
                "screenshot_points": identity_points,
                "overlay_points": identity_points,
            },
            "polygon_path": "configs/stage_maps/scorch_gorge/stage_mask.yaml",
            "polygon_sha256": REVIEW.file_sha256(polygon_path),
        }
    }
    assert REVIEW.image_to_map_point(manifest, [0.5, 0.5]) == [0.5, 0.5]
    assert REVIEW.image_to_map_point(manifest, [0.5, 0.05]) is None


def test_comparison_separates_false_positive_miss_and_uncertain_marker() -> None:
    """Compare clear points and keep identity-uncertain marks from hard errors."""
    frame = {
        "candidates": [
            {"center_px": [110.0, 105.0], "observed_color_side": "ally_color"},
            {"center_px": [300.0, 300.0], "observed_color_side": "opponent_color"},
        ]
    }
    annotation = {
        "frame_complete": True,
        "annotations": [
            {
                "image_xy": [100 / 1920, 100 / 1080],
                "team": "orange",
                "identity_uncertain": False,
            },
            {
                "image_xy": [302 / 1920, 301 / 1080],
                "team": "unknown",
                "identity_uncertain": True,
            },
        ],
    }
    result = REVIEW.compare_annotations(frame, annotation)
    assert result["matched_markers"] == 1
    assert result["false_positives"] == 0
    assert result["missed_markers"] == 0
    assert result["ambiguous_proposals_near_uncertain_markers"] == 1
    assert result["color_side_accuracy"] == 1.0
    assert result["localization_errors_pixels"]["count"] == 1


def test_review_queue_selects_clear_dev_frame_and_preserves_pins() -> None:
    """Record the clean initial frame separately from the original pinned set."""
    analysis_dir = Path(__file__).parents[1] / "analysis/battlefront_player_markers_b1"
    queue = json.loads((analysis_dir / "review_queue_v2.json").read_text())
    manifest = json.loads((analysis_dir / "manifest.json").read_text())
    original = {frame["frame_id"]: frame for frame in manifest["frames"]}
    selected = queue["frames"][0]
    assert selected["frame_id"] == "m08_f07800"
    assert original[selected["frame_id"]]["split"] == "development"
    assert (
        selected["source_frame_sha256"] == original[selected["frame_id"]]["image_sha256"]
    )
    assert {frame["frame_id"] for frame in manifest["frames"]} == {
        "m08_f07200",
        "m08_f07800",
        "m08_f08400",
        "m08_f09000",
        "m08_f09600",
    }
