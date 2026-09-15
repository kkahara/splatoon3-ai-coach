"""Low-ink detector + usable fusion + MAP_OVERLAY-style intervals."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config.models import (
    EventFusionConfig,
    LowInkDetectorConfig,
    VisionLanguage,
)
from splatoon3_ai_coach.vision.events import infer_events
from splatoon3_ai_coach.vision.lifecycle import extract_lifecycle_observation
from splatoon3_ai_coach.vision.low_ink import LowInkDetector
from splatoon3_ai_coach.vision.models import (
    DeathReading,
    DetectorResult,
    GameEventReason,
    GameEventSource,
    GameEventType,
    GameStateSnapshot,
    LowInkReading,
    VisionFrameResult,
)
from splatoon3_ai_coach.config.models import (
    ActiveGameplayDetectorConfig,
    DeathDetectorConfig,
    RespawnDetectorConfig,
)

_REPO = Path(__file__).resolve().parents[1]
_EN_TEMPLATE = _REPO / "calibration/templates/low_ink/en/en-low_ink.png"
_JA_TEMPLATE = _REPO / "calibration/templates/low_ink/ja/jp-low_ink.png"
_TEMPLATE_DIR = _REPO / "calibration/templates/low_ink"

# Provisional YAML ROI (normalized).
_ROI = (0.28, 0.70, 0.72, 0.82)


def _config(**overrides: object) -> LowInkDetectorConfig:
    raw: dict[str, object] = {
        "roi": _ROI,
        "template_dir": _TEMPLATE_DIR,
        "match_threshold": 0.70,
        "min_usable_confidence": 0.50,
    }
    raw.update(overrides)
    return LowInkDetectorConfig.model_validate(raw)


def _blank_frame(height: int = 1080, width: int = 1920) -> np.ndarray:
    return np.full((height, width, 3), 40, dtype=np.uint8)


def _paste_template(
    frame: np.ndarray,
    template_path: Path,
    *,
    roi: tuple[float, float, float, float] = _ROI,
) -> np.ndarray:
    """Paste a BGR template into the ROI (scaled to fit)."""
    out = frame.copy()
    tpl = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
    assert tpl is not None
    h, w = out.shape[:2]
    x1, y1, x2, y2 = roi
    left, top = int(x1 * w), int(y1 * h)
    right, bottom = int(x2 * w), int(y2 * h)
    box_w, box_h = max(1, right - left), max(1, bottom - top)
    th, tw = tpl.shape[:2]
    scale = min(box_w / tw, box_h / th, 1.0)
    new_w, new_h = max(1, int(tw * scale)), max(1, int(th * scale))
    scaled = cv2.resize(tpl, (new_w, new_h), interpolation=cv2.INTER_AREA)
    y0 = top + (box_h - new_h) // 2
    x0 = left + (box_w - new_w) // 2
    out[y0 : y0 + new_h, x0 : x0 + new_w] = scaled
    return out


def test_en_template_present() -> None:
    assert _EN_TEMPLATE.is_file()
    det = LowInkDetector(_config(), language=VisionLanguage.EN)
    reading, conf = det.detect(_paste_template(_blank_frame(), _EN_TEMPLATE))
    assert reading.present is True
    assert reading.template_score >= 0.70
    assert conf >= 0.70


def test_ja_template_present() -> None:
    assert _JA_TEMPLATE.is_file()
    det = LowInkDetector(_config(), language=VisionLanguage.JA)
    reading, conf = det.detect(_paste_template(_blank_frame(), _JA_TEMPLATE))
    assert reading.present is True
    assert reading.template_score >= 0.70
    assert conf >= 0.70


def test_blank_roi_usable_absent() -> None:
    det = LowInkDetector(_config(), language=VisionLanguage.EN)
    reading, conf = det.detect(_blank_frame())
    assert reading.present is False
    assert conf >= 0.50  # usable negative via max(min_usable, 1 - score)


def test_no_templates_confidence_zero() -> None:
    det = LowInkDetector(
        _config(template_dir=_REPO / "calibration/templates/low_ink/missing"),
        language=VisionLanguage.EN,
    )
    reading, conf = det.detect(_blank_frame())
    assert reading.present is False
    assert conf == 0.0


def _frame_with_low_ink(
    *,
    present: bool,
    confidence: float,
    timestamp: float = 1.0,
) -> VisionFrameResult:
    return VisionFrameResult(
        frame_id=f"f:{timestamp}",
        timestamp=timestamp,
        source="cadence",
        detections=[
            DetectorResult(
                id=f"low:{timestamp}",
                detector_name="low_ink",
                detector_version="low_ink@test",
                confidence=confidence,
                reading=LowInkReading(present=present, template_score=confidence),
            ),
            DetectorResult(
                id=f"death:{timestamp}",
                detector_name="death",
                detector_version="death@test",
                confidence=0.9,
                reading=DeathReading(),
            ),
        ],
    )


def test_fusion_usable_true_false_and_unusable_none() -> None:
    death = DeathDetectorConfig()
    respawn = RespawnDetectorConfig()
    active = ActiveGameplayDetectorConfig()
    low = _config()

    obs_true = extract_lifecycle_observation(
        _frame_with_low_ink(present=True, confidence=0.85),
        death_config=death,
        respawn_config=respawn,
        active_config=active,
        low_ink_config=low,
    )
    assert obs_true.low_ink_present is True

    obs_false = extract_lifecycle_observation(
        _frame_with_low_ink(present=False, confidence=0.55),
        death_config=death,
        respawn_config=respawn,
        active_config=active,
        low_ink_config=low,
    )
    assert obs_false.low_ink_present is False

    obs_none = extract_lifecycle_observation(
        _frame_with_low_ink(present=False, confidence=0.20),
        death_config=death,
        respawn_config=respawn,
        active_config=active,
        low_ink_config=low,
    )
    assert obs_none.low_ink_present is None


def test_low_ink_interval_from_fused_state() -> None:
    snaps = [
        GameStateSnapshot(timestamp=1.0, low_ink_present=False, quality="observed"),
        GameStateSnapshot(
            timestamp=2.0,
            low_ink_present=True,
            quality="observed",
            evidence_ids=["low:2"],
        ),
        GameStateSnapshot(timestamp=2.5, low_ink_present=True, quality="observed"),
        GameStateSnapshot(timestamp=3.0, low_ink_present=False, quality="observed"),
        # True → None closes under MAP_OVERLAY is-True semantics
        GameStateSnapshot(timestamp=4.0, low_ink_present=True, quality="observed"),
        GameStateSnapshot(timestamp=4.5, low_ink_present=None, quality="observed"),
    ]
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    lows = [e for e in events if e.event_type is GameEventType.LOW_INK]
    assert len(lows) == 2
    assert lows[0].source is GameEventSource.STATE
    assert lows[0].reason is GameEventReason.LOW_INK_PRESENT
    assert lows[0].start_time == 2.0
    assert lows[0].end_time == 3.0
    assert lows[1].start_time == 4.0
    assert lows[1].end_time == 4.5
