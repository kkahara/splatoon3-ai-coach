"""Timer template calibration from gameplay video."""

import json
from pathlib import Path

import cv2
from loguru import logger

from splatoon3_ai_coach.config.models import TimerDetectorConfig
from splatoon3_ai_coach.media.video import VideoLoader
from splatoon3_ai_coach.vision.glyphs import normalize_glyph
from splatoon3_ai_coach.vision.timer import crop_roi, segment_glyphs

MIN_GLYPH_SCORE = 0.15


def calibrate_timer(
    video: Path,
    output_dir: Path,
    config: TimerDetectorConfig,
    sample_fps: float = 1.0,
) -> Path:
    """Sample timer ROIs and write candidate glyph tiles for manual labeling."""
    tiles_dir = output_dir / "tiles"
    reject_dir = output_dir / "reject"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    reject_dir.mkdir(parents=True, exist_ok=True)

    step = 1.0 / sample_fps
    saved = 0
    rejected = 0
    last_sampled = -float("inf")

    with VideoLoader(video) as loader:
        for frame in loader.frames():
            if frame.timestamp - last_sampled < step:
                continue
            last_sampled = frame.timestamp

            roi = crop_roi(frame.image, config.roi)
            glyphs = segment_glyphs(roi)
            if not glyphs:
                rejected += 1
                cv2.imwrite(
                    str(reject_dir / f"{frame.timestamp:010.3f}_empty.jpg"),
                    roi,
                )
                continue

            for index, glyph in enumerate(glyphs):
                normalized = normalize_glyph(glyph)
                if normalized.max() == 0:
                    rejected += 1
                    continue
                path = tiles_dir / f"{frame.timestamp:010.3f}_{index:02d}.png"
                cv2.imwrite(str(path), normalized)
                saved += 1

    manifest = {
        "video": str(video),
        "sample_fps": sample_fps,
        "tiles_saved": saved,
        "rejects": rejected,
        "instructions": (
            "Move labeled tiles into templates/{0..9,colon}/ before running analyze."
        ),
    }
    manifest_path = output_dir / "calibration_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.info("Saved {} calibration tiles to {}", saved, tiles_dir)
    return manifest_path
