#!/usr/bin/env python3
"""Crop unclassified event-lane icon candidates for template calibration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import yaml

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.config.paths import resolve_config_path
from splatoon3_ai_coach.review.lane_glyphs import (
    LaneGlyphCandidate,
    compute_row_responses,
    detect_lane_glyph_candidates,
    load_lane_templates,
)


def main() -> int:
    """Harvest candidate crops and write deterministic source metadata."""
    args = _parse_args()
    config = load_config(resolve_config_path(args.config)).review
    manifest = _load_manifest(args.manifest)
    entries = _accepted_entries(args.frames, manifest)
    args.out.mkdir(parents=True, exist_ok=True)
    metadata: list[dict[str, object]] = []
    candidate_index = 0
    for image_path, source in entries:
        image = cv2.imread(str(image_path))
        if image is None:
            continue
        for candidate in _candidates(image, image_path.name, config):
            candidate_index += 1
            crop_name = f"candidate_{candidate_index:06d}.png"
            cv2.imwrite(str(args.out / crop_name), candidate.crop)
            metadata.append(
                {
                    "source_frame": image_path.name,
                    "source_timestamp": source.get("source_video_time"),
                    "lane_x_coordinate": candidate.center_x,
                    "normalized_x_coordinate": candidate.x_norm,
                    "row_id": candidate.row_id,
                    "player_slot": candidate.slot_id,
                    "crop_bounding_box": list(candidate.bbox),
                    "crop_path": crop_name,
                }
            )
    (args.out / "candidates.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Harvested {len(metadata)} Review event-lane candidates")
    return 0


def _candidates(
    image: np.ndarray, sample_id: str, config: ReviewTimelineConfig
) -> list[LaneGlyphCandidate]:
    """Identity-free glyph positions on the configured icon rows."""
    templates = load_lane_templates(
        config.special_template_dir,
        image.shape[0],
        config.event_glyph_representation,
        config.special_template_scales,
    )
    responses = compute_row_responses(image, config, templates)
    return detect_lane_glyph_candidates(image, sample_id, config, responses)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frames", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    return parser.parse_args()


def _load_manifest(path: Path | None) -> dict[str, object]:
    if path is None:
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _accepted_entries(
    directory: Path, manifest: dict[str, object]
) -> list[tuple[Path, dict[str, object]]]:
    entries = manifest.get("entries")
    samples = manifest.get("video_samples")
    source_by_name = (
        {
            str(item.get("image_path")): item
            for item in samples
            if isinstance(item, dict) and item.get("image_path")
        }
        if isinstance(samples, list)
        else {}
    )
    if isinstance(entries, list):
        result = []
        for item in entries:
            if not isinstance(item, dict) or not item.get("image_path"):
                continue
            name = str(item["image_path"])
            result.append((directory / name, source_by_name.get(name, item)))
        return result
    paths = sorted(
        path
        for path in directory.iterdir()
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"}
    )
    return [(path, {}) for path in paths]


if __name__ == "__main__":
    raise SystemExit(main())
