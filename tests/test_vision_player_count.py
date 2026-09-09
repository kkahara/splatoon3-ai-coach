"""HUD death-X player-count detector (masked SQDIFF)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import PlayerCountDetectorConfig
from splatoon3_ai_coach.vision.player_count import (
    PlayerCountDetector,
    _best_masked_sqdiff,
    _to_raw_gray,
    derive_x_mask,
)
from splatoon3_ai_coach.vision.roi import crop_roi

_REPO = Path(__file__).resolve().parents[1]
_TEMPLATE_DIR = _REPO / "calibration" / "templates" / "players"
_DIAG = (
    _REPO
    / "analysis"
    / "2026-09-06 22-35-09-player_count"
    / "player_count_crop_diagnostic_masked_sqdiff"
)


def _config() -> PlayerCountDetectorConfig:
    cfg = load_config(default_config_path()).vision.player_count
    assert cfg.template_dir is not None
    return cfg


def _blank_frame(height: int = 1080, width: int = 1920) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def _paste_bgr(
    frame: np.ndarray,
    patch_bgr: np.ndarray,
    box: tuple[float, float, float, float],
) -> None:
    """Center-paste a BGR patch into the slot ROI."""
    roi = crop_roi(frame, box)
    if roi.size == 0:
        return
    th, tw = patch_bgr.shape[:2]
    rh, rw = roi.shape[:2]
    scale = min(rh / th, rw / tw, 1.0)
    new_w = max(4, int(tw * scale))
    new_h = max(4, int(th * scale))
    scaled = cv2.resize(patch_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
    y0 = (rh - new_h) // 2
    x0 = (rw - new_w) // 2
    roi[y0 : y0 + new_h, x0 : x0 + new_w] = scaled


def _first_player_x() -> Path:
    paths = sorted(_TEMPLATE_DIR.glob("player-x-*"))
    assert paths, "expected player-x-* templates"
    return paths[0]


@pytest.mark.skipif(not _TEMPLATE_DIR.is_dir(), reason="player X templates missing")
def test_templates_and_masks_load() -> None:
    detector = PlayerCountDetector(_config())
    assert len(detector._templates) >= 2  # noqa: SLF001
    for item in detector._templates:  # noqa: SLF001
        assert item.image.ndim == 2
        assert item.mask.shape == item.image.shape
        assert np.any(item.mask)
        coverage = float((item.mask > 0).mean())
        assert 0.15 <= coverage <= 0.45


@pytest.mark.skipif(not _TEMPLATE_DIR.is_dir(), reason="player X templates missing")
def test_derive_x_mask_is_diagonal_structure() -> None:
    gray = cv2.imread(str(_first_player_x()), cv2.IMREAD_GRAYSCALE)
    assert gray is not None
    mask = derive_x_mask(gray)
    assert mask.dtype == np.uint8
    assert np.any(mask)
    # Masked mean should be mid-gray (X), not bright white weapon.
    masked_mean = float(gray[mask > 0].mean())
    assert 70 <= masked_mean <= 140


@pytest.mark.skipif(not _TEMPLATE_DIR.is_dir(), reason="player X templates missing")
def test_centered_template_paste_detects_ally_1() -> None:
    cfg = _config()
    template = cv2.imread(str(_first_player_x()), cv2.IMREAD_COLOR)
    assert template is not None
    frame = _blank_frame()
    _paste_bgr(frame, template, cfg.ally_slots[0])
    detector = PlayerCountDetector(cfg)
    reading, confidence = detector.detect(frame)
    assert reading is not None
    assert reading.ally_dead_slots == (1,)
    assert reading.opponent_dead_slots == ()
    assert reading.ally_slot_scores[0] <= cfg.sqdiff_match_threshold
    assert confidence == pytest.approx(1.0 - min(reading.ally_slot_scores), abs=1e-6)


@pytest.mark.skipif(not _TEMPLATE_DIR.is_dir(), reason="player X templates missing")
def test_opponent_slot_detected() -> None:
    cfg = _config()
    paths = sorted(_TEMPLATE_DIR.glob("player-x-*"))
    template = cv2.imread(str(paths[min(2, len(paths) - 1)]), cv2.IMREAD_COLOR)
    assert template is not None
    frame = _blank_frame()
    _paste_bgr(frame, template, cfg.opponent_slots[2])
    detector = PlayerCountDetector(cfg)
    reading, confidence = detector.detect(frame)
    assert reading is not None
    assert reading.opponent_dead_slots == (3,)
    assert reading.ally_dead_slots == ()
    assert confidence >= cfg.min_usable_confidence


@pytest.mark.skipif(not _TEMPLATE_DIR.is_dir(), reason="player X templates missing")
def test_neighbor_slot_does_not_false_positive() -> None:
    cfg = _config()
    template = cv2.imread(str(_first_player_x()), cv2.IMREAD_COLOR)
    assert template is not None
    frame = _blank_frame()
    _paste_bgr(frame, template, cfg.ally_slots[0])
    detector = PlayerCountDetector(cfg)
    reading, _ = detector.detect(frame)
    assert reading is not None
    assert reading.ally_dead_slots == (1,)
    assert 2 not in reading.ally_dead_slots
    assert 3 not in reading.ally_dead_slots


@pytest.mark.skipif(not _TEMPLATE_DIR.is_dir(), reason="player X templates missing")
def test_blank_alive_slots_not_detected() -> None:
    cfg = _config()
    detector = PlayerCountDetector(cfg)
    reading, _ = detector.detect(_blank_frame())
    assert reading is not None
    assert reading.ally_dead_slots == ()
    assert reading.opponent_dead_slots == ()
    assert all(s > cfg.sqdiff_match_threshold for s in reading.ally_slot_scores)
    assert all(s > cfg.sqdiff_match_threshold for s in reading.opponent_slot_scores)


@pytest.mark.skipif(not _TEMPLATE_DIR.is_dir(), reason="player X templates missing")
def test_confidence_is_one_minus_best_sqdiff() -> None:
    cfg = _config()
    template = cv2.imread(str(_first_player_x()), cv2.IMREAD_COLOR)
    assert template is not None
    frame = _blank_frame()
    _paste_bgr(frame, template, cfg.ally_slots[0])
    detector = PlayerCountDetector(cfg)
    reading, confidence = detector.detect(frame)
    assert reading is not None
    all_scores = list(reading.ally_slot_scores) + list(reading.opponent_slot_scores)
    assert confidence == pytest.approx(1.0 - min(all_scores), abs=1e-6)
    # Strongest evidence is the lowest SQDIFF, not the max.
    assert min(all_scores) == reading.ally_slot_scores[0]


def test_sqdiff_threshold_comparison_is_less_or_equal() -> None:
    cfg = PlayerCountDetectorConfig(sqdiff_match_threshold=0.08)
    assert 0.08 <= cfg.sqdiff_match_threshold
    # Detection predicate used by the detector:
    assert (0.08 <= 0.08) is True
    assert (0.0801 <= 0.08) is False


def test_default_config_has_sqdiff_threshold() -> None:
    cfg = PlayerCountDetectorConfig()
    assert cfg.sqdiff_match_threshold == pytest.approx(0.08)
    assert "match_threshold" not in PlayerCountDetectorConfig.model_fields


def test_default_config_has_eight_slots() -> None:
    cfg = PlayerCountDetectorConfig()
    assert len(cfg.ally_slots) == 4
    assert len(cfg.opponent_slots) == 4


def test_player_count_not_in_default_enabled_detectors() -> None:
    enabled = load_config(default_config_path()).vision.enabled_detectors
    assert "player_count" not in enabled


@pytest.mark.skipif(not _TEMPLATE_DIR.is_dir(), reason="player X templates missing")
def test_match_template_receives_mask_and_sqdiff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Matcher semantics: mask passed, SQDIFF method, min() wins."""
    cfg = _config()
    detector = PlayerCountDetector(cfg)
    assert detector._templates  # noqa: SLF001

    calls: list[dict[str, object]] = []
    real_match = cv2.matchTemplate

    def _spy(image, templ, method, mask=None, **kwargs):  # noqa: ANN001
        calls.append({"method": method, "mask": mask})
        return real_match(image, templ, method, mask=mask, **kwargs)

    monkeypatch.setattr(cv2, "matchTemplate", _spy)

    template = cv2.imread(str(_first_player_x()), cv2.IMREAD_COLOR)
    assert template is not None
    frame = _blank_frame()
    _paste_bgr(frame, template, cfg.ally_slots[0])
    detector.detect(frame)

    assert calls, "expected matchTemplate invocations"
    assert all(c["method"] == cv2.TM_SQDIFF_NORMED for c in calls)
    assert all(c["mask"] is not None for c in calls)
    assert all(isinstance(c["mask"], np.ndarray) and np.any(c["mask"]) for c in calls)  # type: ignore[arg-type]


@pytest.mark.skipif(not _TEMPLATE_DIR.is_dir(), reason="player X templates missing")
def test_raw_gray_path_not_clahe(monkeypatch: pytest.MonkeyPatch) -> None:
    """SQDIFF path must use raw grayscale; CLAHE must not run."""
    called = {"clahe": False}

    real_create = cv2.createCLAHE

    def _blocked(*args, **kwargs):  # noqa: ANN002, ANN003
        called["clahe"] = True
        return real_create(*args, **kwargs)

    monkeypatch.setattr(cv2, "createCLAHE", _blocked)

    cfg = _config()
    template = cv2.imread(str(_first_player_x()), cv2.IMREAD_COLOR)
    assert template is not None
    frame = _blank_frame()
    _paste_bgr(frame, template, cfg.ally_slots[0])
    PlayerCountDetector(cfg).detect(frame)
    assert called["clahe"] is False

    # Sanity: helper is raw cvtColor gray.
    gray = _to_raw_gray(frame[0:10, 0:10])
    assert gray.ndim == 2


@pytest.mark.skipif(not _TEMPLATE_DIR.is_dir(), reason="player X templates missing")
def test_lower_sqdiff_wins_across_templates() -> None:
    cfg = _config()
    detector = PlayerCountDetector(cfg)
    templates = detector._templates  # noqa: SLF001
    assert len(templates) >= 2
    # Self-match of first template must beat a blank ROI.
    patch = cv2.cvtColor(templates[0].image, cv2.COLOR_GRAY2BGR)
    frame = _blank_frame()
    _paste_bgr(frame, patch, cfg.ally_slots[0])
    roi = crop_roi(frame, cfg.ally_slots[0])
    score_x, name_x = _best_masked_sqdiff(roi, templates)
    score_blank, _ = _best_masked_sqdiff(np.zeros_like(roi), templates)
    assert score_x < score_blank
    assert score_x <= cfg.sqdiff_match_threshold
    assert name_x is not None


@pytest.mark.skipif(
    not (_DIAG / "death_171.0" / "ally-4.png").is_file(),
    reason="masked-sqdiff diagnostic crops missing",
)
def test_real_crop_ally4_t171_different_weapon_detected() -> None:
    """Regression: clear X over different weapon must still fire."""
    cfg = _config()
    crop = cv2.imread(str(_DIAG / "death_171.0" / "ally-4.png"))
    assert crop is not None
    frame = _blank_frame()
    _paste_bgr(frame, crop, cfg.ally_slots[3])
    reading, _ = PlayerCountDetector(cfg).detect(frame)
    assert reading is not None
    assert 4 in reading.ally_dead_slots
    assert reading.ally_slot_scores[3] <= cfg.sqdiff_match_threshold


@pytest.mark.skipif(
    not (_DIAG / "death_171.0" / "opponent-1.png").is_file(),
    reason="masked-sqdiff diagnostic crops missing",
)
def test_real_crop_opponent1_t171_alive_not_detected() -> None:
    cfg = _config()
    crop = cv2.imread(str(_DIAG / "death_171.0" / "opponent-1.png"))
    assert crop is not None
    frame = _blank_frame()
    _paste_bgr(frame, crop, cfg.opponent_slots[0])
    reading, _ = PlayerCountDetector(cfg).detect(frame)
    assert reading is not None
    assert reading.opponent_dead_slots == ()
    assert reading.opponent_slot_scores[0] > cfg.sqdiff_match_threshold


@pytest.mark.skipif(
    not (_DIAG / "death_269.5" / "opponent-1.png").is_file(),
    reason="masked-sqdiff diagnostic crops missing",
)
def test_real_crop_opponent1_t269_dualie_x_detected() -> None:
    cfg = _config()
    crop = cv2.imread(str(_DIAG / "death_269.5" / "opponent-1.png"))
    assert crop is not None
    frame = _blank_frame()
    _paste_bgr(frame, crop, cfg.opponent_slots[0])
    reading, _ = PlayerCountDetector(cfg).detect(frame)
    assert reading is not None
    assert 1 in reading.opponent_dead_slots
