"""Tests for observe-only Splat Zones score detector."""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from splatoon3_ai_coach.config.models import ScoreDetectorConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.vision.score import ScoreDetector, read_score_frame
from splatoon3_ai_coach.vision.templates import load_templates

SAMPLES = PROJECT_ROOT / "analysis" / "score_survey" / "samples"
TEMPLATES = PROJECT_ROOT / "calibration" / "templates"


@pytest.mark.skipif(not SAMPLES.is_dir(), reason="score survey samples missing")
def test_score_detector_reads_opening_100_100() -> None:
    """Opening frame should read 100/100 on both sides."""
    path = SAMPLES / "en_barnicle" / "t00020.5.png"
    if not path.is_file():
        pytest.skip(f"missing {path}")
    image = cv2.imread(str(path))
    assert image is not None
    detector = ScoreDetector(
        ScoreDetectorConfig(template_dir=TEMPLATES),
    )
    reading, confidence = detector.detect(image)
    assert reading is not None
    assert confidence > 0.5
    assert reading.left.visible and reading.left.value == 100
    assert reading.right.visible and reading.right.value == 100
    assert reading.kind == "score"
    # Screen geometry only — no ally/opponent fields.
    assert not hasattr(reading, "ally_remaining")
    assert not hasattr(reading, "opponent_remaining")


@pytest.mark.skipif(not SAMPLES.is_dir(), reason="score survey samples missing")
def test_score_reader_handles_one_digit_vs_two() -> None:
    """Asymmetric widths: left 1-digit, right 2-digit."""
    path = SAMPLES / "en_hagglefish" / "t00189.0.png"
    if not path.is_file():
        pytest.skip(f"missing {path}")
    image = cv2.imread(str(path))
    assert image is not None
    templates = load_templates(TEMPLATES)
    cfg = ScoreDetectorConfig(template_dir=TEMPLATES)
    reading = read_score_frame(
        image,
        left_roi=cfg.left_roi,
        right_roi=cfg.right_roi,
        templates=templates,
        match_threshold=cfg.match_threshold,
    )
    assert reading.left.value == 2
    assert reading.right.value == 83
