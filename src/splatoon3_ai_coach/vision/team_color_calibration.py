"""Match-level team color calibration from early HUD roster slots.

Samples saturated pixels in ``player_count`` slot ROIs (geometry only — not
``PlayerCountDetector``). Latches ally/opponent hue centers after a few
corroborating frames, then stays immutable for the rest of the match.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from loguru import logger

from splatoon3_ai_coach.config.models import MapInkAnalyzerConfig, PlayerCountDetectorConfig
from splatoon3_ai_coach.types import NormalizedBox
from splatoon3_ai_coach.vision.roi import crop_roi

_MIN_PIXELS_PER_SLOT = 10
_MIN_PIXELS_SIDE = 40
_MAX_WITHIN_SIDE_SPREAD = 20.0
_SOURCE = "hud_roster_slots"


@dataclass(frozen=True)
class TeamColorCalibrationResult:
    """Immutable match-level ally/opponent hue binding."""

    ally_h: float
    opponent_h: float
    separation_degrees: float
    calibrated_at: float
    source: str = _SOURCE

    def to_dict(self) -> dict[str, float | str]:
        """JSON-friendly payload for ``match_identity.json``."""
        return {
            "ally_h": round(self.ally_h, 1),
            "opponent_h": round(self.opponent_h, 1),
            "separation_degrees": round(self.separation_degrees, 1),
            "calibrated_at": round(self.calibrated_at, 3),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> TeamColorCalibrationResult:
        """Rebuild from ``match_identity.json`` team_color_calibration."""
        return cls(
            ally_h=float(payload["ally_h"]),  # type: ignore[arg-type]
            opponent_h=float(payload["opponent_h"]),  # type: ignore[arg-type]
            separation_degrees=float(payload["separation_degrees"]),  # type: ignore[arg-type]
            calibrated_at=float(payload["calibrated_at"]),  # type: ignore[arg-type]
            source=str(payload.get("source") or _SOURCE),
        )


@dataclass(frozen=True)
class ColorProfile:
    """Hue-centric ink identity: H + S; V is a soft dark reject."""

    h_center: float
    h_tolerance: float
    s_min: int
    v_min: int

    def contains_hsv(self, h: float, s: float, v: float) -> bool:
        """True when pixel passes soft V, S gate, and circular H band."""
        if v < self.v_min or s < self.s_min:
            return False
        return circular_hue_distance(h, self.h_center) <= self.h_tolerance


def circular_hue_distance(a: float, b: float) -> float:
    """Shortest distance on OpenCV H circle ``[0, 180)``.

    Example: H 11 vs H 119 → 72 (not the long arc 108).
    """
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def circular_hue_in_band(h: float, center: float, tolerance: float) -> bool:
    """Whether ``h`` lies within ``tolerance`` of ``center`` on the H circle."""
    return circular_hue_distance(h, center) <= tolerance


@dataclass
class _FrameSideSample:
    """Per-frame side aggregate from usable slots."""

    h_median: float
    slot_hues: list[float]


@dataclass
class TeamColorCalibrator:
    """Accumulate early HUD roster hues; latch after corroborating frames."""

    ally_slots: list[NormalizedBox]
    opponent_slots: list[NormalizedBox]
    window_seconds: float = 60.0
    min_accepted_frames: int = 3
    min_hue_separation: float = 30.0
    s_min: int = 70
    v_min: int = 40
    max_within_side_spread: float = _MAX_WITHIN_SIDE_SPREAD
    _ally_frame_h: list[float] = field(default_factory=list)
    _opp_frame_h: list[float] = field(default_factory=list)
    _accepted_times: list[float] = field(default_factory=list)
    _result: TeamColorCalibrationResult | None = None

    @classmethod
    def from_configs(
        cls,
        map_ink: MapInkAnalyzerConfig,
        player_count: PlayerCountDetectorConfig,
    ) -> TeamColorCalibrator:
        """Build calibrator from map-ink knobs + player-count slot geometry."""
        return cls(
            ally_slots=list(player_count.ally_slots),
            opponent_slots=list(player_count.opponent_slots),
            window_seconds=float(map_ink.calibration_window_seconds),
            min_accepted_frames=int(map_ink.min_accepted_frames),
            min_hue_separation=float(map_ink.min_hue_separation),
            s_min=int(map_ink.s_min),
            v_min=int(map_ink.v_min),
        )

    @property
    def result(self) -> TeamColorCalibrationResult | None:
        """Latched calibration, or ``None`` until corroboration succeeds."""
        return self._result

    @property
    def is_latched(self) -> bool:
        """True after a successful sticky latch."""
        return self._result is not None

    def observe(self, image: np.ndarray, video_time: float) -> TeamColorCalibrationResult | None:
        """Sample one frame; return the result when first latched, else ``None``.

        After latch, later calls are no-ops and return ``None``.
        """
        if self._result is not None:
            return None
        if video_time < 0.0 or video_time > self.window_seconds:
            return None
        if image.size == 0:
            return None

        ally = self._side_sample(image, self.ally_slots)
        opp = self._side_sample(image, self.opponent_slots)
        if ally is None or opp is None:
            return None
        separation = circular_hue_distance(ally.h_median, opp.h_median)
        if separation < self.min_hue_separation:
            return None
        if not self._slots_agree(ally.slot_hues) or not self._slots_agree(opp.slot_hues):
            return None

        self._ally_frame_h.append(ally.h_median)
        self._opp_frame_h.append(opp.h_median)
        self._accepted_times.append(float(video_time))
        if len(self._accepted_times) < self.min_accepted_frames:
            return None

        ally_h = float(np.median(np.asarray(self._ally_frame_h, dtype=np.float64)))
        opp_h = float(np.median(np.asarray(self._opp_frame_h, dtype=np.float64)))
        calibrated_at = float(self._accepted_times[-1])
        self._result = TeamColorCalibrationResult(
            ally_h=ally_h,
            opponent_h=opp_h,
            separation_degrees=circular_hue_distance(ally_h, opp_h),
            calibrated_at=calibrated_at,
            source=_SOURCE,
        )
        logger.info(
            "Team color calibration latched at {:.1f}s: ally_h={:.1f} opp_h={:.1f} "
            "separation={:.1f}° ({} frames)",
            calibrated_at,
            ally_h,
            opp_h,
            self._result.separation_degrees,
            len(self._accepted_times),
        )
        return self._result

    def _side_sample(
        self,
        image: np.ndarray,
        slots: list[NormalizedBox],
    ) -> _FrameSideSample | None:
        """Median H across usable slots on one side for one frame."""
        hues: list[float] = []
        usable_total = 0
        for box in slots:
            sample = self._slot_hue(image, box)
            if sample is None:
                continue
            h, usable = sample
            hues.append(h)
            usable_total += usable
        if not hues or usable_total < _MIN_PIXELS_SIDE:
            return None
        return _FrameSideSample(
            h_median=float(np.median(np.asarray(hues, dtype=np.float64))),
            slot_hues=hues,
        )

    def _slot_hue(
        self,
        image: np.ndarray,
        box: NormalizedBox,
    ) -> tuple[float, int] | None:
        """``(h_median, usable_pixels)`` for one roster slot, or ``None``."""
        crop = crop_roi(image, box)
        if crop.size == 0:
            return None
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask = (hsv[:, :, 1] >= self.s_min) & (hsv[:, :, 2] >= self.v_min)
        usable = int(np.count_nonzero(mask))
        if usable < _MIN_PIXELS_PER_SLOT:
            return None
        h_med = float(np.median(hsv[:, :, 0][mask].astype(np.float64)))
        return h_med, usable

    def _slots_agree(self, hues: list[float]) -> bool:
        """Reject frames where usable slots on one side disagree too much."""
        if len(hues) < 2:
            return True
        arr = np.asarray(hues, dtype=np.float64)
        med = float(np.median(arr))
        return all(
            circular_hue_distance(float(h), med) <= self.max_within_side_spread for h in hues
        )
