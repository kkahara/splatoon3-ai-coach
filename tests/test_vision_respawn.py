"""Unit tests for RespawnDetector decision order."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config.models import RespawnDetectorConfig, VisionLanguage
from splatoon3_ai_coach.vision.respawn import RespawnDetector

_RESPAWN_ROOT = Path("calibration/templates/respawn")
_JA_CROP = _RESPAWN_ROOT / "ja" / "jp_fukkatsu_02.png"


def _blank_frame() -> np.ndarray:
    return np.full((1080, 1920, 3), 40, dtype=np.uint8)


def _roi_box() -> tuple[int, int, int, int]:
    """Pixel box for the configured respawn ROI on a 1080p frame."""
    x1, y1, x2, y2 = 0.840, 0.825, 1.000, 0.950
    return int(x1 * 1920), int(y1 * 1080), int(x2 * 1920), int(y2 * 1080)


def _paste_native(image: np.ndarray, crop: np.ndarray) -> None:
    """Paste ``crop`` at the Japanese 復活 banner location inside the ROI."""
    th, tw = crop.shape[:2]
    x1 = int(0.876 * 1920)
    y1 = int(0.900 * 1080)
    x2 = min(1920, x1 + tw)
    y2 = min(1080, y1 + th)
    fitted = crop
    if fitted.shape[0] != (y2 - y1) or fitted.shape[1] != (x2 - x1):
        fitted = cv2.resize(crop, (x2 - x1, y2 - y1), interpolation=cv2.INTER_AREA)
    image[y1:y2, x1:x2] = fitted


def _ja_detector(**overrides: object) -> RespawnDetector:
    data: dict[str, object] = {
        "template_dir": _RESPAWN_ROOT,
        "match_threshold": 0.60,
    }
    data.update(overrides)
    return RespawnDetector(
        RespawnDetectorConfig(**data),  # type: ignore[arg-type]
        language=VisionLanguage.JA,
    )


def test_template_match_detects_respawn_banner() -> None:
    assert _JA_CROP.exists(), "expected Japanese respawn banner template"
    crop = cv2.imread(str(_JA_CROP))
    assert crop is not None

    image = _blank_frame()
    _paste_native(image, crop)

    detector = _ja_detector()
    reading, confidence = detector.detect(image, timestamp=1.0)
    assert reading is not None
    assert reading.detected
    assert reading.evidence_type == "template"
    assert reading.template_score >= 0.60
    assert confidence >= 0.60


def test_templates_loaded_block_heuristic_solo_trip() -> None:
    """Yellow-only BR ink must not detect when templates are available."""
    image = _blank_frame()
    image[int(0.830 * 1080) : int(0.869 * 1080), int(0.850 * 1920) : 1920] = (
        0,
        220,
        255,
    )
    detector = _ja_detector()
    reading, confidence = detector.detect(image, timestamp=1.0)
    assert reading is not None
    assert not reading.detected
    assert confidence >= 0.50  # absent must remain usable for lifecycle


def test_structure_plus_soft_template_is_supporting_hit() -> None:
    """Plate structure with mid template score uses the heuristic support path."""
    crop = cv2.imread(str(_JA_CROP))
    assert crop is not None
    image = _blank_frame()
    x1, y1, x2, y2 = _roi_box()
    image[y1:y2, x1:x2] = (20, 20, 20)
    fitted = cv2.resize(
        crop,
        (min(crop.shape[1], x2 - x1), min(crop.shape[0], y2 - y1)),
        interpolation=cv2.INTER_AREA,
    )
    py = y1 + (y2 - y1 - fitted.shape[0]) // 2
    px = x1 + (x2 - x1 - fitted.shape[1]) // 2
    image[py : py + fitted.shape[0], px : px + fitted.shape[1]] = fitted
    blend = cv2.addWeighted(
        image[y1:y2, x1:x2], 0.45, np.full_like(image[y1:y2, x1:x2], 20), 0.55, 0
    )
    image[y1:y2, x1:x2] = blend

    detector = _ja_detector(support_match_threshold=0.38)
    reading, confidence = detector.detect(image, timestamp=1.0)
    assert reading is not None
    assert reading.detected
    assert reading.evidence_type in {"template", "heuristic"}
    assert confidence >= 0.50


def test_absent_confidence_usable_without_banner() -> None:
    detector = _ja_detector()
    reading, confidence = detector.detect(_blank_frame(), timestamp=1.0)
    assert reading is not None
    assert not reading.detected
    assert confidence >= 0.50


def test_template_hit_holds_through_landing_frames() -> None:
    """After 「復活まであと」 drops, detected stays true briefly as hold."""
    crop = cv2.imread(str(_JA_CROP))
    assert crop is not None
    banner = _blank_frame()
    _paste_native(banner, crop)
    detector = _ja_detector(hold_seconds=2.0)
    first, _ = detector.detect(banner, timestamp=10.0)
    held, _ = detector.detect(_blank_frame(), timestamp=11.5)
    expired, _ = detector.detect(_blank_frame(), timestamp=12.1)
    assert first is not None and first.detected
    assert first.evidence_type == "template"
    assert held is not None and held.detected
    assert held.evidence_type == "hold"
    assert expired is not None and not expired.detected


def test_primary_hit_skips_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Template primary_hit must not invoke OCR (ocr_text/countdown stay empty)."""
    crop = cv2.imread(str(_JA_CROP))
    assert crop is not None
    image = _blank_frame()
    _paste_native(image, crop)

    calls = {"n": 0}

    def boom(*_args: object, **_kwargs: object) -> str:
        calls["n"] += 1
        raise AssertionError("OCR must not run on primary_hit")

    monkeypatch.setattr("splatoon3_ai_coach.vision.respawn._ocr_text", boom)
    detector = _ja_detector()
    reading, _ = detector.detect(image, timestamp=1.0)
    assert reading is not None
    assert reading.detected
    assert reading.evidence_type == "template"
    assert reading.ocr_text is None
    assert reading.countdown_value is None
    assert calls["n"] == 0


def test_non_primary_still_runs_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    """When template misses, OCR still runs and can decide via keyword+digit."""
    calls = {"n": 0}

    def fake_ocr(*_args: object, **_kwargs: object) -> str:
        calls["n"] += 1
        return "Respawn in 3"

    monkeypatch.setattr("splatoon3_ai_coach.vision.respawn._ocr_text", fake_ocr)
    monkeypatch.setattr(
        "splatoon3_ai_coach.vision.respawn._best_template_score",
        lambda *_a, **_k: 0.10,
    )
    detector = _ja_detector(support_match_threshold=0.55)
    reading, _ = detector.detect(_blank_frame(), timestamp=1.0)
    assert reading is not None
    assert calls["n"] == 1
    assert reading.detected
    assert reading.evidence_type == "ocr"
    assert reading.countdown_value == 3
    assert reading.ocr_text == "Respawn in 3"


def test_prepared_templates_match_uncached_scores() -> None:
    """Scale-pyramid cache must not change the full-search template score."""
    from splatoon3_ai_coach.vision.respawn import (
        _best_template_score,
        prepare_respawn_templates,
    )
    from splatoon3_ai_coach.vision.roi import crop_roi

    detector = _ja_detector()
    crop = cv2.imread(str(_JA_CROP))
    assert crop is not None
    image = _blank_frame()
    _paste_native(image, crop)
    roi = crop_roi(image, detector.config.roi)
    prepared = prepare_respawn_templates(
        detector._templates, (roi.shape[0], roi.shape[1])
    )
    uncached = _best_template_score(
        roi, detector._templates, prepared=None, early_exit_score=None
    )
    cached = _best_template_score(
        roi, detector._templates, prepared=prepared, early_exit_score=None
    )
    assert cached == pytest.approx(uncached, abs=1e-9)
    assert detector._prepared_templates((roi.shape[0], roi.shape[1])) is (
        detector._prepared_templates((roi.shape[0], roi.shape[1]))
    )


def test_strong_score_early_exit_still_primary_hit() -> None:
    """Early exit at the strong floor still yields template primary_hit."""
    from splatoon3_ai_coach.vision.respawn import _STRONG_TEMPLATE_FLOOR

    crop = cv2.imread(str(_JA_CROP))
    assert crop is not None
    image = _blank_frame()
    _paste_native(image, crop)
    detector = _ja_detector()
    reading, _ = detector.detect(image, timestamp=1.0)
    assert reading is not None
    assert reading.detected
    assert reading.evidence_type == "template"
    assert reading.template_score >= _STRONG_TEMPLATE_FLOOR
