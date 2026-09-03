"""Shared test fixtures."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config.models import (
    ExtractionConfig,
    HudRegions,
    TimerDetectorConfig,
    VisionConfig,
)
from splatoon3_ai_coach.vision.glyphs import GLYPH_HEIGHT, GLYPH_WIDTH

FRAME_SIZE = (320, 180)
SOURCE_FPS = 30


@pytest.fixture
def extraction_config() -> ExtractionConfig:
    return ExtractionConfig(
        analysis_fps=6,
        scene_threshold=0.72,
        ssim_threshold=0.80,
        motion_threshold=0.75,
        hud_threshold=0.20,
        min_event_gap_seconds=0.75,
        context_before_seconds=1.5,
        context_after_seconds=1.0,
        max_frames_per_minute=90,
        save_jpeg_quality=92,
        hud=HudRegions(
            killfeed=(0.68, 0.02, 0.99, 0.30),
            special_gauge=(0.35, 0.00, 0.65, 0.12),
            objective_timer=(0.40, 0.00, 0.60, 0.15),
            death_text=(0.20, 0.25, 0.80, 0.70),
        ),
    )


@pytest.fixture
def timer_template_dir(tmp_path: Path) -> Path:
    """Minimal calibrated templates for timer detector tests."""
    template_dir = tmp_path / "templates"
    for symbol in [*(str(d) for d in range(10)), "colon"]:
        symbol_dir = template_dir / symbol
        symbol_dir.mkdir(parents=True)
        glyph = np.zeros((GLYPH_HEIGHT, GLYPH_WIDTH), dtype=np.uint8)
        cv2.putText(
            glyph,
            symbol if symbol != "colon" else ":",
            (4, GLYPH_HEIGHT - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            255,
            2,
            cv2.LINE_AA,
        )
        cv2.imwrite(str(symbol_dir / "sample.png"), glyph)
    return template_dir


@pytest.fixture
def vision_config(timer_template_dir: Path) -> VisionConfig:
    return VisionConfig(
        enabled_detectors=["timer"],
        hud_cadence_fps=2.0,
        timer=TimerDetectorConfig(
            roi=(0.40, 0.00, 0.60, 0.15),
            template_dir=timer_template_dir,
            match_threshold=0.55,
            min_usable_confidence=0.50,
        ),
    )


@pytest.fixture
def sample_video(tmp_path: Path) -> Path:
    """A short clip that is static for a second, then cuts to a new scene."""
    path = tmp_path / "sample.mp4"
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        SOURCE_FPS,
        FRAME_SIZE,
    )

    for index in range(SOURCE_FPS * 2):
        frame = np.zeros((FRAME_SIZE[1], FRAME_SIZE[0], 3), dtype=np.uint8)
        if index >= SOURCE_FPS:
            frame[:] = 255
            cv2.rectangle(frame, (220, 10), (315, 55), (0, 0, 0), -1)
        writer.write(frame)

    writer.release()
    return path
