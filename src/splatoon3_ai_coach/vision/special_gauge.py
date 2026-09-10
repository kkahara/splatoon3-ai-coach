"""Special-gauge HUD detector (sparse observation evidence).

Evidence only: visible / approximate fill / conservative ready.
Does not emit SPECIAL_READY or SPECIAL_USED GameEvents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import SpecialGaugeDetectorConfig
from splatoon3_ai_coach.vision.models import SpecialGaugeReading
from splatoon3_ai_coach.vision.roi import crop_roi


@dataclass
class SpecialGaugeDebug:
    """Per-frame diagnostics for overlays and offline tools."""

    center: tuple[float, float] = (0.0, 0.0)
    radius: float = 0.0
    sector_lit: list[bool] = field(default_factory=list)
    sector_angles_deg: list[float] = field(default_factory=list)
    usable_sector_mask: list[bool] = field(default_factory=list)
    continuity: float = 0.0
    magenta_banner_frac: float = 0.0


def _angle_in_exclude(angle_deg: float, start: float, end: float) -> bool:
    """Return True if ``angle_deg`` lies in [start, end] on a 0–360 circle."""
    a = angle_deg % 360.0
    s = start % 360.0
    e = end % 360.0
    if s <= e:
        return s <= a <= e
    return a >= s or a <= e


def _max_run_fraction(flags: list[bool]) -> float:
    """Longest circular True run as a fraction of list length."""
    n = len(flags)
    if n == 0:
        return 0.0
    if all(flags):
        return 1.0
    doubled = flags + flags
    best = cur = 0
    for value in doubled:
        if value:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return min(best, n) / float(n)


def _lit_mask(hsv: np.ndarray, config: SpecialGaugeDetectorConfig) -> np.ndarray:
    """Yellow/orange/pink bright pixels typical of lit gauge segments."""
    h, s, v = cv2.split(hsv)
    orange = (
        (h >= config.lit_h_min)
        & (h <= config.lit_h_max)
        & (s >= config.lit_s_min)
        & (v >= config.lit_v_min)
    )
    # Near-full rims often wash to pale yellow / pink-white.
    pale = (v >= max(config.lit_v_min, 150)) & (s >= 30) & (
        ((h >= 5) & (h <= 45)) | (h >= 150) | (h <= 10)
    )
    return orange | pale


def _estimate_dial_geometry(
    gray: np.ndarray,
) -> tuple[tuple[float, float], float]:
    """Locate the circular dial inside a gauge ROI crop."""
    h, w = gray.shape[:2]
    fallback_center = (w * 0.55, h * 0.55)
    fallback_radius = 0.42 * min(h, w)
    if h < 16 or w < 16:
        return fallback_center, fallback_radius

    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    min_r = max(8, int(0.22 * min(h, w)))
    max_r = max(min_r + 1, int(0.55 * min(h, w)))
    circles = cv2.HoughCircles(
        blur,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=float(min(h, w) // 2),
        param1=80,
        param2=22,
        minRadius=min_r,
        maxRadius=max_r,
    )
    if circles is not None and len(circles[0]) > 0:
        # Prefer the circle nearest crop center (dial vs peripheral chrome).
        cx0, cy0 = w * 0.5, h * 0.5
        best = min(
            circles[0],
            key=lambda c: (c[0] - cx0) ** 2 + (c[1] - cy0) ** 2,
        )
        return (float(best[0]), float(best[1])), float(best[2])
    return fallback_center, fallback_radius


def _magenta_banner_frac(hsv: np.ndarray) -> float:
    """Fraction of magenta/pink banner pixels (e.g. Finish!)."""
    h, s, v = cv2.split(hsv)
    mask = ((h >= 140) | (h <= 10)) & (s >= 80) & (v >= 120)
    # Restrict to upper third where Finish! banners sit in the gauge crop.
    h_img = hsv.shape[0]
    band = np.zeros_like(mask, dtype=bool)
    band[: max(1, h_img // 3), :] = True
    region = mask & band
    return float(region.mean()) if region.size else 0.0


def _dial_score(gray: np.ndarray, center: tuple[float, float], radius: float) -> float:
    """Score presence of a dark circular dial with structured rim."""
    h, w = gray.shape[:2]
    if h < 8 or w < 8 or radius < 4:
        return 0.0
    yy, xx = np.ogrid[:h, :w]
    cx, cy = center
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    core = dist <= radius * 0.35
    annulus = (dist >= radius * 0.45) & (dist <= radius * 0.95)
    if not core.any() or not annulus.any():
        return 0.0
    core_mean = float(gray[core].mean()) / 255.0
    # Dark core is characteristic; washout frames score low.
    dark_core = max(0.0, 1.0 - core_mean / 0.55)
    edges = cv2.Canny(gray, 40, 120)
    rim_edge = float(edges[annulus].mean()) / 255.0
    rim_structure = min(1.0, rim_edge / 0.08)
    # Prefer some brightness variance in the annulus (segments vs empty).
    annulus_std = float(gray[annulus].std()) / 255.0
    contrast = min(1.0, annulus_std / 0.12)
    score = float(
        np.clip(0.45 * dark_core + 0.30 * rim_structure + 0.25 * contrast, 0, 1)
    )
    # Blank / unstructured regions: dark core alone is not a dial.
    if rim_structure < 0.20 and contrast < 0.25:
        score = min(score, 0.25)
    # Noise / mid-luma discs lack a truly dark HUD core.
    if dark_core < 0.35:
        score = min(score, 0.30)
    return score


def _prompt_score(roi: np.ndarray) -> float:
    """Language-neutral cue for activation chrome (bright UI / stick glyph)."""
    if roi.size == 0 or min(roi.shape[:2]) < 4:
        return 0.0
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    h, s, v = cv2.split(hsv)
    # Bright UI text on dark HUD (押しこみ) — require some edge structure.
    bright = ((v >= 190) & (s <= 70)).mean()
    edges = cv2.Canny(gray, 50, 140)
    edge_frac = float(edges.mean()) / 255.0
    cyan = ((h >= 85) & (h <= 115) & (s >= 70) & (v >= 110)).mean()
    # Stick prompt is compact; avoid scoring large yellow badge blobs alone.
    yellow_small = ((h >= 15) & (h <= 40) & (s >= 120) & (v >= 160)).mean()
    score = 2.2 * bright * min(1.0, edge_frac / 0.04) + 2.5 * cyan
    score += 0.35 * min(yellow_small, 0.15) / 0.15
    return float(np.clip(score, 0, 1))


def _sample_sectors(
    lit: np.ndarray,
    center: tuple[float, float],
    radius: float,
    config: SpecialGaugeDetectorConfig,
) -> tuple[list[bool], list[float], list[bool]]:
    """Classify annulus sectors as lit; skip badge exclusion window."""
    h, w = lit.shape[:2]
    yy, xx = np.ogrid[:h, :w]
    cx, cy = center
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    angles = (np.degrees(np.arctan2(-(yy - cy), xx - cx)) + 360.0) % 360.0
    inner = radius * config.annulus_inner
    outer = radius * config.annulus_outer
    annulus = (dist >= inner) & (dist <= outer)

    sector_lit: list[bool] = []
    sector_angles: list[float] = []
    usable: list[bool] = []
    step = 360.0 / float(config.sector_count)
    for i in range(config.sector_count):
        a0 = i * step
        a1 = (i + 1) * step
        mid = (a0 + a1) * 0.5
        sector_angles.append(mid)
        excluded = _angle_in_exclude(
            mid, config.badge_exclude_start_deg, config.badge_exclude_end_deg
        )
        usable.append(not excluded)
        if excluded:
            sector_lit.append(False)
            continue
        mask = annulus & (angles >= a0) & (angles < a1)
        if not mask.any():
            sector_lit.append(False)
            continue
        frac = float(lit[mask].mean())
        sector_lit.append(frac >= config.lit_sector_pixel_frac)
    return sector_lit, sector_angles, usable


class SpecialGaugeDetector:
    """Observe the top-right Special gauge dial on in-match HUD frames."""

    name = "special_gauge"
    run_on_evidence = True

    def __init__(
        self,
        config: SpecialGaugeDetectorConfig,
        cadence_fps: float | None = None,
    ) -> None:
        self.config = config
        self.cadence_fps = cadence_fps
        self.last_debug: SpecialGaugeDebug | None = None

    def detect(
        self,
        image: np.ndarray,
        timestamp: float | None = None,
    ) -> tuple[SpecialGaugeReading | None, float]:
        """Return a Special-gauge reading for one frame."""
        reading, confidence, debug = self._observe(image, timestamp)
        self.last_debug = debug
        return reading, confidence

    def _observe(
        self,
        image: np.ndarray,
        timestamp: float | None,
    ) -> tuple[SpecialGaugeReading, float, SpecialGaugeDebug]:
        """Crop, score dial presence, estimate fill, decide ready."""
        debug = SpecialGaugeDebug()
        crop = crop_roi(image, self.config.roi)
        if crop.size == 0 or min(crop.shape[:2]) < 8:
            return (
                SpecialGaugeReading(timestamp=timestamp),
                0.0,
                debug,
            )

        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        center, radius = _estimate_dial_geometry(gray)
        debug.center = center
        debug.radius = radius
        debug.magenta_banner_frac = _magenta_banner_frac(hsv)

        dial = _dial_score(gray, center, radius)
        # Finish! / post-match magenta banners make the gauge unusable.
        if debug.magenta_banner_frac >= 0.08:
            dial = min(dial, 0.15)

        prompt_crop = crop_roi(image, self.config.prompt_roi)
        prompt = _prompt_score(prompt_crop)
        # When analyzing a dial crop with near-full ROI, also check the
        # right strip of the crop for activation chrome.
        x1, y1, x2, y2 = self.config.roi
        if (x2 - x1) > 0.85 and (y2 - y1) > 0.85:
            rh, rw = crop.shape[:2]
            right = crop[0 : max(1, rh // 2), int(rw * 0.72) : rw]
            prompt = max(prompt, _prompt_score(right))

        if dial < self.config.min_dial_score:
            confidence = float(np.clip(dial, 0, 1))
            reading = SpecialGaugeReading(
                visible=False,
                fill_fraction=None,
                ready=False,
                timestamp=timestamp,
                dial_score=float(dial),
                lit_sector_fraction=0.0,
                ready_prompt_score=float(prompt),
            )
            return reading, confidence, debug

        lit = _lit_mask(hsv, self.config)
        sector_lit, sector_angles, usable = _sample_sectors(
            lit, center, radius, self.config
        )
        debug.sector_lit = sector_lit
        debug.sector_angles_deg = sector_angles
        debug.usable_sector_mask = usable

        usable_flags = [sector_lit[i] for i, ok in enumerate(usable) if ok]
        if not usable_flags:
            reading = SpecialGaugeReading(
                visible=False,
                fill_fraction=None,
                ready=False,
                timestamp=timestamp,
                dial_score=float(dial),
                ready_prompt_score=float(prompt),
            )
            return reading, float(dial), debug

        fill = sum(1 for flag in usable_flags if flag) / float(len(usable_flags))
        continuity = _max_run_fraction(usable_flags)
        debug.continuity = continuity

        # Conservative ready: near-complete lit ring, OR a strong activation
        # prompt while the dial is visible. Ready frames may pulse with only
        # a few lit rim segments, so prompt must not require high fill.
        # Yellow sub/status badge alone never sets ready.
        strong_ring = (
            fill >= self.config.ready_fill_threshold
            and continuity >= self.config.ready_continuity_threshold
        )
        strong_prompt = prompt >= self.config.ready_prompt_threshold
        ready = bool(strong_ring or strong_prompt)

        # Confidence: dial presence × how decisive the lit/unlit split is.
        lit_arr = lit.astype(np.float32)
        # Contrast proxy inside annulus.
        gh, gw = gray.shape[:2]
        yy, xx = np.ogrid[:gh, :gw]
        dist = np.sqrt((xx - center[0]) ** 2 + (yy - center[1]) ** 2)
        annulus = (dist >= radius * self.config.annulus_inner) & (
            dist <= radius * self.config.annulus_outer
        )
        contrast = float(lit_arr[annulus].std()) if annulus.any() else 0.0
        confidence = float(
            np.clip(0.55 * dial + 0.30 * min(1.0, contrast / 0.25) + 0.15, 0, 1)
        )
        if confidence < self.config.min_usable_confidence and fill < 0.15:
            # Weak observation of nearly-empty gauge: still visible if dial ok.
            pass

        reading = SpecialGaugeReading(
            visible=True,
            fill_fraction=float(np.clip(fill, 0, 1)),
            ready=ready,
            timestamp=timestamp,
            dial_score=float(dial),
            lit_sector_fraction=float(fill),
            ready_prompt_score=float(prompt),
        )
        return reading, confidence, debug


def write_special_gauge_diagnostic(
    image: np.ndarray,
    reading: SpecialGaugeReading,
    debug: SpecialGaugeDebug | None,
    path: Path | str,
    *,
    roi: tuple[float, float, float, float] | None = None,
) -> None:
    """Write an annotated frame showing ROI, sectors, fill, and ready."""
    out = image.copy()
    h, w = out.shape[:2]
    if roi is not None:
        x1, y1, x2, y2 = roi
        cv2.rectangle(
            out,
            (int(x1 * w), int(y1 * h)),
            (int(x2 * w), int(y2 * h)),
            (0, 165, 255),
            2,
        )
        ox, oy = int(x1 * w), int(y1 * h)
        scale_x = (x2 - x1) * w
        scale_y = (y2 - y1) * h
    else:
        ox = oy = 0
        scale_x, scale_y = float(w), float(h)

    if debug is not None and debug.radius > 0:
        cx = int(ox + debug.center[0] * (scale_x / max(image.shape[1], 1)))
        cy = int(oy + debug.center[1] * (scale_y / max(image.shape[0], 1)))
        # When image is already the ROI crop, center is in crop pixels.
        if roi is None or (
            abs(roi[2] - roi[0] - 1.0) < 1e-6 and abs(roi[3] - roi[1] - 1.0) < 1e-6
        ):
            cx = int(debug.center[0])
            cy = int(debug.center[1])
            radius = int(debug.radius)
        else:
            # Map crop-local center/radius into full-frame pixels.
            crop_w = max(1.0, (roi[2] - roi[0]) * w)
            crop_h = max(1.0, (roi[3] - roi[1]) * h)
            cx = int(roi[0] * w + debug.center[0])
            cy = int(roi[1] * h + debug.center[1])
            radius = int(debug.radius)
            _ = (crop_w, crop_h)

        cv2.circle(out, (cx, cy), radius, (255, 255, 0), 1)
        for lit_flag, angle, usable in zip(
            debug.sector_lit,
            debug.sector_angles_deg,
            debug.usable_sector_mask,
            strict=False,
        ):
            if not usable:
                color = (80, 80, 80)
            elif lit_flag:
                color = (0, 255, 0)
            else:
                color = (0, 0, 255)
            rad = np.radians(angle)
            # Match sampling: atan2(-dy, dx) ⇒ y decreases with +sin in image.
            x2 = int(cx + radius * np.cos(rad))
            y2 = int(cy - radius * np.sin(rad))
            cv2.line(out, (cx, cy), (x2, y2), color, 1)

    fill_txt = (
        "None" if reading.fill_fraction is None else f"{reading.fill_fraction:.2f}"
    )
    lines = [
        f"visible={reading.visible} ready={reading.ready}",
        f"fill={fill_txt} conf_dial={reading.dial_score:.2f}",
        f"prompt={reading.ready_prompt_score:.2f}",
    ]
    y = 24
    for line in lines:
        cv2.putText(
            out,
            line,
            (8, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        y += 22

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), out)
