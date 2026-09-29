"""Tests for observe-only Splat Zones score detector."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config.models import ScoreDetectorConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.vision.score import (
    ScoreDetector,
    read_penalty_side,
    read_score_frame,
)
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


def _penalty_config() -> ScoreDetectorConfig:
    return ScoreDetectorConfig(
        template_dir=TEMPLATES,
        left_penalty_roi=(0.424479, 0.196296, 0.481771, 0.240741),
        right_penalty_roi=(0.513021, 0.196296, 0.572917, 0.240741),
    )


@pytest.mark.skipif(not SAMPLES.is_dir(), reason="score survey samples missing")
@pytest.mark.parametrize(
    ("frame", "left_penalty", "right_penalty"),
    [
        ("en_hagglefish/t00134.0.png", 23, None),
        ("en_hagglefish/t00189.0.png", None, 10),
        ("ja_kraken/t00054.0.png", 10, 3),
        ("en_barnicle/t00020.5.png", None, None),
    ],
)
def test_penalty_reads_plus_n_per_side(
    frame: str, left_penalty: int | None, right_penalty: int | None
) -> None:
    """+N under each pod is read per screen side; empty pills read as None."""
    path = SAMPLES / frame
    if not path.is_file():
        pytest.skip(f"missing {path}")
    image = cv2.imread(str(path))
    reading, _ = ScoreDetector(_penalty_config()).detect(image)
    assert reading is not None
    assert reading.penalty_evaluated
    assert reading.left_penalty == left_penalty
    assert reading.right_penalty == right_penalty


def test_penalty_requires_plus_glyph() -> None:
    """White digits without a leading '+' are scene clutter, not a penalty."""
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    cv2.putText(
        image, "23", (840, 250), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 4
    )
    templates = load_templates(TEMPLATES)
    cfg = _penalty_config()
    side = read_penalty_side(image, cfg.left_penalty_roi, templates, match_threshold=0.55)
    assert side.value is None


def test_penalty_not_evaluated_without_rois() -> None:
    """Without penalty ROIs the reading says penalties were not inspected."""
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    templates = load_templates(TEMPLATES)
    cfg = ScoreDetectorConfig(template_dir=TEMPLATES)
    reading = read_score_frame(
        image,
        left_roi=cfg.left_roi,
        right_roi=cfg.right_roi,
        templates=templates,
        match_threshold=cfg.match_threshold,
    )
    assert not reading.penalty_evaluated
    assert reading.left_penalty is None and reading.right_penalty is None
