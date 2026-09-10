"""Unit tests for SpecialGaugeDetector (representative survey crops)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config.models import SpecialGaugeDetectorConfig
from splatoon3_ai_coach.vision.special_gauge import SpecialGaugeDetector

REP = Path("analysis/special_gauge_survey/representatives")


def _crop_detector() -> SpecialGaugeDetector:
    """Detector configured for dial crops (ROI = full image)."""
    return SpecialGaugeDetector(
        SpecialGaugeDetectorConfig(
            roi=(0.0, 0.0, 1.0, 1.0),
            prompt_roi=(0.72, 0.0, 1.0, 0.55),
        )
    )


def _load_crop(name: str) -> np.ndarray:
    path = REP / name
    if not path.is_file():
        pytest.skip(f"missing representative crop: {path}")
    image = cv2.imread(str(path))
    if image is None:
        pytest.skip(f"unreadable crop: {path}")
    return image


def test_low_fill_not_ready() -> None:
    """Low lit rim → visible, low fill, not ready."""
    reading, confidence = _crop_detector().detect(
        _load_crop("partial_low_00143.0_crop.jpg"), timestamp=143.0
    )
    assert reading is not None
    assert reading.visible is True
    assert reading.fill_fraction is not None
    assert 0.0 <= reading.fill_fraction <= 0.35
    assert reading.ready is False
    assert reading.timestamp == 143.0
    assert confidence >= reading.dial_score * 0.5


def test_partial_fill_trizooka() -> None:
    """Partial fill example stays not ready."""
    reading, _ = _crop_detector().detect(
        _load_crop("curated_partial_trizooka_055.5_crop.jpg")
    )
    assert reading.visible is True
    assert reading.fill_fraction is not None
    assert 0.10 <= reading.fill_fraction <= 0.55
    assert reading.ready is False


def test_partial_fill_70pct() -> None:
    """~70% fill is high-partial, not ready."""
    reading, _ = _crop_detector().detect(
        _load_crop("curated_partial_70pct_062.0_crop.jpg")
    )
    assert reading.visible is True
    assert reading.fill_fraction is not None
    assert reading.fill_fraction >= 0.55
    assert reading.ready is False


def test_high_near_ready_fill_conservative() -> None:
    """Ready-ish high fill without strong prompt stays not ready."""
    reading, _ = _crop_detector().detect(
        _load_crop("curated_ready_ish_075.5_crop.jpg")
    )
    assert reading.visible is True
    assert reading.fill_fraction is not None
    assert reading.fill_fraction >= 0.65
    assert reading.ready is False


def test_strong_ready_inkstorm() -> None:
    """Strong ready example (activation chrome) → ready=True."""
    reading, confidence = _crop_detector().detect(
        _load_crop("curated_ready_inkstorm_049.0_crop.jpg")
    )
    assert reading.visible is True
    assert reading.ready is True
    assert reading.ready_prompt_score >= 0.50
    assert confidence > 0.0


def test_unusable_post_match_finish() -> None:
    """Finish! / post-match crop → not visible, no fabricated fill."""
    reading, confidence = _crop_detector().detect(
        _load_crop("low_00212.0_crop.jpg")
    )
    assert reading.visible is False
    assert reading.fill_fraction is None
    assert reading.ready is False
    assert confidence < 0.40


def test_ambiguous_blank_frame() -> None:
    """Non-gauge content → not visible."""
    blank = np.zeros((180, 260, 3), dtype=np.uint8)
    reading, confidence = _crop_detector().detect(blank)
    assert reading.visible is False
    assert reading.fill_fraction is None
    assert reading.ready is False
    assert confidence < 0.35


def test_noise_frame_not_ready() -> None:
    """Random noise is not a usable dial."""
    rng = np.random.default_rng(0)
    noise = rng.integers(0, 255, size=(180, 260, 3), dtype=np.uint8)
    reading, _ = _crop_detector().detect(noise)
    assert reading.visible is False
    assert reading.fill_fraction is None
    assert reading.ready is False


def test_fill_fraction_bounds_when_visible() -> None:
    """Visible readings keep fill in [0, 1]."""
    reading, _ = _crop_detector().detect(
        _load_crop("curated_partial_trizooka_185.5_crop.jpg")
    )
    assert reading.visible is True
    assert reading.fill_fraction is not None
    assert 0.0 <= reading.fill_fraction <= 1.0


def test_confidence_low_when_invisible() -> None:
    """Invisible frames report low confidence."""
    reading, confidence = _crop_detector().detect(
        _load_crop("low_00212.0_crop.jpg")
    )
    assert reading.visible is False
    assert confidence <= reading.dial_score + 1e-6
    assert confidence < 0.40


def test_fill_ordering_low_partial_high() -> None:
    """Coarse fill ordering: low < mid-partial < near-high."""
    det = _crop_detector()
    low, _ = det.detect(_load_crop("partial_low_00143.0_crop.jpg"))
    mid, _ = det.detect(_load_crop("curated_partial_trizooka_055.5_crop.jpg"))
    high, _ = det.detect(_load_crop("curated_partial_70pct_062.0_crop.jpg"))
    assert low.fill_fraction is not None
    assert mid.fill_fraction is not None
    assert high.fill_fraction is not None
    assert low.fill_fraction <= mid.fill_fraction <= high.fill_fraction + 0.05
    assert high.fill_fraction > low.fill_fraction
