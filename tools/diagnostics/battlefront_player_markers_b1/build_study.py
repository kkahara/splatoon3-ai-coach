"""Build an isolated, provenance-pinned M08 player-marker study."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "analysis/battlefront_player_markers_b1"
PHASE1_STUDY = ROOT / "analysis/battle_frontline_phase1/study.json"
FRAME_INDICES = {
    7200: "development",
    7800: "development",
    8400: "development",
    9000: "held_out",
    9600: "held_out",
}


def sha256(path: Path) -> str:
    """Hash a file's exact bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_video_metadata(video: Path) -> dict[str, Any]:
    """Read frame timestamps and stream metadata without inferring from fps."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,avg_frame_rate,time_base,duration",
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp,pkt_duration",
            "-of",
            "json",
            str(video),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def extract_frame(video: Path, index: int, path: Path) -> None:
    """Extract an exact decoded frame index using FFmpeg's frame selector."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(video),
            "-vf",
            f"select='eq(n,{index})'",
            "-frames:v",
            "1",
            str(path),
        ],
        check=True,
    )


def _point_in_polygon(point: tuple[float, float], polygon: list[list[float]]) -> bool:
    """Check a normalized point against the registered map polygon."""
    contour = np.asarray(polygon, dtype=np.float32).reshape((-1, 1, 2))
    return cv2.pointPolygonTest(contour, point, False) >= 0


def detect_candidates(
    image: np.ndarray,
    homography: np.ndarray,
    polygon: list[list[float]],
    ally_hue: float,
    opponent_hue: float,
) -> list[dict[str, Any]]:
    """Emit compact team-color blob proposals, never asserted player identities."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    eligible = (saturation >= 70) & (value >= 40)
    records: list[dict[str, Any]] = []
    for side, center in (("ally_color", ally_hue), ("opponent_color", opponent_hue)):
        distance = np.abs(hue.astype(np.float32) - center)
        distance = np.minimum(distance, 180.0 - distance)
        mask = ((distance <= 15.0) & eligible).astype(np.uint8)
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        for label in range(1, count):
            x, y, width, height, area = (int(v) for v in stats[label])
            if area < 35 or area > 1200 or width < 4 or height < 4:
                continue
            if width > 42 or height > 42 or max(width / height, height / width) > 3.0:
                continue
            cx, cy = (float(v) for v in centroids[label])
            point = np.asarray(
                [[[cx / image.shape[1], cy / image.shape[0]]]], dtype=np.float32
            )
            mapped = cv2.perspectiveTransform(point, homography)[0, 0]
            if not _point_in_polygon((float(mapped[0]), float(mapped[1])), polygon):
                continue
            compactness = min(1.0, area / max(1.0, width * height))
            color_distance = float(np.mean(distance[labels == label]))
            score = max(
                0.0, min(1.0, 0.45 * compactness + 0.55 * (1.0 - color_distance / 15.0))
            )
            records.append(
                {
                    "candidate_id": f"c{len(records) + 1:04d}",
                    "bbox_px": [x, y, width, height],
                    "center_px": [round(cx, 3), round(cy, 3)],
                    "image_xy": [
                        round(cx / image.shape[1], 7),
                        round(cy / image.shape[0], 7),
                    ],
                    "map_xy": [round(float(mapped[0]), 7), round(float(mapped[1]), 7)],
                    "visible_class": "compact_team-colored_blob_candidate",
                    "observed_color_side": side,
                    "team_association": "unknown",
                    "entity_hypothesis": "unknown",
                    "detection_score_heuristic": round(score, 5),
                    "score_is_calibrated_probability": False,
                }
            )
    records.sort(key=lambda item: (item["center_px"][1], item["center_px"][0]))
    for index, record in enumerate(records, start=1):
        record["candidate_id"] = f"c{index:04d}"
    return records


def build(output: Path = OUT) -> None:
    """Build frames, manifest and immutable raw detector proposals."""
    if (output / "raw_detector_outputs.json").exists():
        raise FileExistsError(
            f"raw detector outputs already exist under {output}; "
            "use a new study directory"
        )
    study = json.loads(PHASE1_STUDY.read_text(encoding="utf-8"))
    shared = study["shared"]
    video = Path(shared["source_recording"])
    if sha256(video) != shared["source_recording_sha256"]:
        raise ValueError(
            "M08 source recording hash does not match the reviewed Phase 1 source"
        )
    if not shared["registration"].get("reviewed") or not shared["colors"].get(
        "reviewed_same_match"
    ):
        raise ValueError("registration and color calibration must be reviewed")
    metadata = read_video_metadata(video)
    stream = metadata["streams"][0]
    frames = metadata["frames"]
    if stream.get("width") != 1920 or stream.get("height") != 1080:
        raise ValueError("unexpected frame dimensions for reviewed registration")
    screenshot_points = np.asarray(
        [[p["x"], p["y"]] for p in shared["registration"]["screenshot_points"]],
        dtype=np.float32,
    )
    overlay_points = np.asarray(
        [[p["x"], p["y"]] for p in shared["registration"]["overlay_points"]],
        dtype=np.float32,
    )
    homography = cv2.getPerspectiveTransform(screenshot_points, overlay_points)
    polygon_path = ROOT / shared["stage_polygon"]
    polygon_doc = json.loads(
        json.dumps(__import__("yaml").safe_load(polygon_path.read_text()))
    )
    polygon = polygon_doc["polygon"]
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "study_id": "m08_scorch_gorge_player_marker_candidates_v1",
        "purpose": "inspection_only_player_marker_localization",
        "source_recording": {
            "filename": video.name,
            "sha256": shared["source_recording_sha256"],
            "path": str(video),
            "width": stream["width"],
            "height": stream["height"],
            "r_frame_rate": stream["r_frame_rate"],
            "avg_frame_rate": stream["avg_frame_rate"],
            "time_base": stream["time_base"],
            "duration_seconds": float(stream["duration"]),
        },
        "stage": {
            "stage_id": shared["stage_id"],
            "identity_status": "verified_for_recording_from_reviewed_phase1_source",
            "identity_source": "analysis/battle_frontline_phase1/study.json",
            "polygon_path": str(polygon_path.relative_to(ROOT)),
            "polygon_sha256": sha256(polygon_path),
            "registration": shared["registration"],
            "registration_status": "reviewed_same_recording",
        },
        "color_reference": {
            **shared["colors"],
            "source_study_path": "analysis/battle_frontline_phase1/study.json",
            "scope": "same_recording_only",
        },
        "timing_policy": "ffprobe frame PTS and frame index; no fps-derived timestamp",
        "split_policy": (
            "development 120-140s; held-out 150-160s; excludes frozen "
            "M08 frames at 105s and 172s"
        ),
        "frames": [],
    }
    raw_outputs: list[dict[str, Any]] = []
    image_dir = output / "source_frames"
    for index, split in FRAME_INDICES.items():
        image_path = image_dir / f"m08_f{index:05d}.png"
        extract_frame(video, index, image_path)
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"failed to decode extracted frame {index}")
        frame_info = frames[index]
        pts = int(frame_info["best_effort_timestamp"])
        timestamp = pts * _time_base(stream["time_base"])
        output_record = {
            "frame_id": f"m08_f{index:05d}",
            "split": split,
            "frame_index": index,
            "source_pts": pts,
            "source_time_base": stream["time_base"],
            "video_time_seconds": round(timestamp, 9),
            "image_path": str(image_path.relative_to(output)),
            "image_sha256": sha256(image_path),
            "stage_id": shared["stage_id"],
            "map_registration_verified": True,
            "frame_excluded_from_frozen_baseline": True,
        }
        manifest["frames"].append(output_record)
        raw_outputs.append(
            {
                **output_record,
                "detector_version": "compact_team_color_components_v1",
                "detector_parameters": {
                    "hue_tolerance": 15.0,
                    "saturation_min": 70,
                    "value_min": 40,
                    "component_area_px": [35, 1200],
                    "component_dimension_px": [4, 42],
                },
                "candidates": detect_candidates(
                    image,
                    homography,
                    polygon,
                    float(shared["colors"]["ally_h"]),
                    float(shared["colors"]["opponent_h"]),
                ),
            }
        )
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (output / "raw_detector_outputs.json").write_text(
        json.dumps(raw_outputs, indent=2) + "\n", encoding="utf-8"
    )


def _time_base(value: str) -> float:
    """Parse FFmpeg rational time base."""
    numerator, denominator = value.split("/", 1)
    return int(numerator) / int(denominator)


def main() -> None:
    """Build the isolated B0/B1 study artifacts."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()
