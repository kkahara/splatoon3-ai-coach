"""Unit tests for RespawnDetector decision order."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import RespawnDetectorConfig
from splatoon3_ai_coach.vision.respawn import RespawnDetector


def _blank_frame() -> np.ndarray:
    return np.full((1080, 1920, 3), 40, dtype=np.uint8)


def test_template_match_detects_respawn_banner() -> None:
    template_dir = Path("calibration/templates/respawn")
    templates = sorted(template_dir.glob("*.png"))
    assert templates, "expected respawn banner templates"
    crop = cv2.imread(str(templates[0]))
    assert crop is not None

    image = _blank_frame()
    # Place the crop into the configured BR ROI.
    y1, y2 = int(0.90 * 1080), int(0.995 * 1080)
    x1, x2 = int(0.78 * 1920), int(0.995 * 1920)
    roi_h, roi_w = y2 - y1, x2 - x1
    fitted = cv2.resize(crop, (roi_w, roi_h), interpolation=cv2.INTER_AREA)
    image[y1:y2, x1:x2] = fitted

    detector = RespawnDetector(
        RespawnDetectorConfig(template_dir=template_dir, match_threshold=0.60)
    )
    reading, confidence = detector.detect(image, timestamp=1.0)
    assert reading is not None
    assert reading.detected
    assert reading.evidence_type == "template"
    assert reading.template_score >= 0.60
    assert confidence >= 0.60


def test_templates_loaded_block_heuristic_solo_trip() -> None:
    """Yellow-only BR ink must not detect when templates are available."""
    image = _blank_frame()
    image[972:1074, 1498:1910] = (0, 220, 255)
    detector = RespawnDetector(
        RespawnDetectorConfig(
            template_dir=Path("calibration/templates/respawn"),
            match_threshold=0.60,
        )
    )
    reading, confidence = detector.detect(image, timestamp=1.0)
    assert reading is not None
    assert not reading.detected
    assert confidence >= 0.50  # absent must remain usable for lifecycle


def test_structure_plus_soft_template_is_supporting_hit() -> None:
    """Plate structure with mid template score uses the heuristic support path."""
    template_dir = Path("calibration/templates/respawn")
    crop = cv2.imread(str(sorted(template_dir.glob("*.png"))[0]))
    assert crop is not None
    image = _blank_frame()
    y1, y2 = int(0.90 * 1080), int(0.995 * 1080)
    x1, x2 = int(0.78 * 1920), int(0.995 * 1920)
    # Dark plate + bright glyphs approximating structure_ok.
    image[y1:y2, x1:x2] = (20, 20, 20)
    image[y1 + 20 : y2 - 20, x1 + 20 : x2 - 20] = (255, 255, 255)
    # Overlay a downscaled template so score is soft but non-zero.
    fitted = cv2.resize(crop, (x2 - x1, y2 - y1), interpolation=cv2.INTER_AREA)
    blend = cv2.addWeighted(image[y1:y2, x1:x2], 0.35, fitted, 0.65, 0)
    image[y1:y2, x1:x2] = blend

    detector = RespawnDetector(
        RespawnDetectorConfig(
            template_dir=template_dir,
            match_threshold=0.60,
            support_match_threshold=0.38,
        )
    )
    reading, confidence = detector.detect(image, timestamp=1.0)
    assert reading is not None
    assert reading.detected
    assert reading.evidence_type in {"template", "heuristic"}
    assert confidence >= 0.50


def test_absent_confidence_usable_without_banner() -> None:
    detector = RespawnDetector(
        RespawnDetectorConfig(
            template_dir=Path("calibration/templates/respawn"),
            match_threshold=0.60,
        )
    )
    reading, confidence = detector.detect(_blank_frame(), timestamp=1.0)
    assert reading is not None
    assert not reading.detected
    assert confidence >= 0.50
