"""Direct, per-frame Tower Control HUD observations.

This development detector observes score pods and localized HUD messages.
Historical marker helpers remain available but are not used by the detector.
"""

from __future__ import annotations

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import TowerControlDetectorConfig, VisionLanguage
from splatoon3_ai_coach.vision.language import require_game_language
from splatoon3_ai_coach.vision.models import (
    TowerControlReading,
    TowerMarkerReading,
    TowerScorePodReading,
)
from splatoon3_ai_coach.vision.roi import pixel_box_from_normalized
from splatoon3_ai_coach.vision.templates import load_templates
from splatoon3_ai_coach.vision.timer import match_glyph
from splatoon3_ai_coach.vision.tower_checkpoint_cue import CheckpointPassedDetector
from splatoon3_ai_coach.vision.tower_control_messages import TowerControlMessageDetector


class TowerControlDetector:
    """Observe Tower score pods and HUD messages on one frame."""

    name = "tower_control"
    run_on_evidence = True

    def __init__(
        self,
        config: TowerControlDetectorConfig,
        cadence_fps: float | None = None,
        *,
        language: VisionLanguage | str,
    ) -> None:
        self.config = config
        self.cadence_fps = cadence_fps
        language = require_game_language(language)
        self._templates = load_templates(config.template_dir)
        self._checkpoint_cue = None
        self._checkpoint_at_cue = None
        self._control_messages = None
        if config.checkpoint_cue_template_dir is not None:
            template_dir = config.checkpoint_cue_template_dir / language
            self._control_messages = TowerControlMessageDetector(
                template_dir / "control", language=language
            )
            path = template_dir / "passed_checkpoint_template.png"
            if path.is_file():
                self._checkpoint_cue = CheckpointPassedDetector(path)
            path = template_dir / "at_checkpoint_template.png"
            if path.is_file():
                self._checkpoint_at_cue = CheckpointPassedDetector(path, threshold=0.96)

    def detect(
        self, image: np.ndarray, timestamp: float | None = None
    ) -> tuple[TowerControlReading | None, float]:
        """Return native Tower HUD observations, or no reading when absent."""
        pods = self._score_pods(image)
        control_cue = (
            self._control_messages.detect(image) if self._control_messages else None
        )
        cue, cue_confidence = (
            self._checkpoint_cue.detect(image, timestamp)
            if self._checkpoint_cue is not None
            else (None, 0.0)
        )
        at_cue, at_confidence = (
            self._checkpoint_at_cue.detect(image, timestamp)
            if self._checkpoint_at_cue is not None and cue is None
            else (None, 0.0)
        )
        if not pods and cue is None and at_cue is None and control_cue is None:
            return None, 0.0
        pod_confidences = [
            float(np.mean(pod.digit_scores)) for pod in pods if pod.digit_scores
        ]
        confidence = max(
            [
                cue_confidence,
                at_confidence,
                control_cue.template_score if control_cue else 0.0,
                *pod_confidences,
            ],
            default=0.0,
        )
        return (
            TowerControlReading(
                score_pods=pods,
                timestamp=timestamp,
                checkpoint_at_cue_score=at_cue.template_score if at_cue else None,
                checkpoint_passed_cue_score=cue.template_score if cue else None,
                control_cue=control_cue,
            ),
            confidence,
        )

    def _score_pods(self, image: np.ndarray) -> list[TowerScorePodReading]:
        """Locate white digit rows, then reuse shared deterministic glyph matching."""
        height, width = image.shape[:2]
        x1, y1, x2, y2 = pixel_box_from_normalized(
            self.config.score_search_roi, width, height
        )
        crop = image[y1:y2, x1:x2]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (0, 0, 175), (179, 105, 255))
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        boxes = [
            tuple(int(value) for value in stat[:4])
            for stat in stats[1:count]
            if 24 <= stat[3] <= 62 and 4 <= stat[2] <= 45 and stat[4] >= 35
        ]
        rows = _digit_rows(boxes)
        pods: list[TowerScorePodReading] = []
        digit_templates = {k: v for k, v in self._templates.items() if k in "0123456789"}
        for row in rows:
            pod = _read_pod_row(
                image, mask, row, x1, y1, width, height, digit_templates, self.config
            )
            if pod is not None:
                pods.append(pod)
        return sorted(pods, key=lambda pod: pod.center_x)[:2]

    def _marker(self, image: np.ndarray) -> tuple[TowerMarkerReading | None, float]:
        """Locate a marker core; unsupported or ambiguous frames stay unknown."""
        height, width = image.shape[:2]
        x1, y1, x2, y2 = pixel_box_from_normalized(self.config.lane_roi, width, height)
        crop = image[y1:y2, x1:x2]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        core_marker = self._white_core_marker(crop, hsv, x1, y1, width, height)
        if core_marker is not None:
            return core_marker
        return None, 0.0

    def _white_core_marker(
        self,
        crop: np.ndarray,
        hsv: np.ndarray,
        offset_x: int,
        offset_y: int,
        frame_width: int,
        frame_height: int,
    ) -> tuple[TowerMarkerReading, float] | None:
        """Locate the compact white 8/anchor core inside the moving marker."""
        white = cv2.inRange(hsv, (0, 0, 175), (179, 105, 255))
        count, labels, stats, _ = cv2.connectedComponentsWithStats(white, 8)
        matches: list[tuple[float, tuple[int, int, int, int], bool]] = []
        for label, stat in enumerate(stats[1:count], start=1):
            x, y, w, h, area = (int(value) for value in stat)
            if not (
                24 <= h <= 46
                and 18 <= w <= 46
                and area >= 180
                and y >= crop.shape[0] * 0.25
                and y + h <= crop.shape[0] * 0.72
            ):
                continue
            pad = 14
            left, top = max(0, x - pad), max(0, y - pad)
            right = min(crop.shape[1], x + w + pad)
            bottom = min(crop.shape[0], y + h + pad)
            surround = hsv[top:bottom, left:right]
            colored = float(
                (
                    (surround[:, :, 1] >= self.config.marker_saturation_min)
                    & (surround[:, :, 2] >= self.config.marker_value_min)
                ).mean()
            )
            compactness = area / float(w * h)
            if colored >= 0.12:
                component = np.where(
                    labels[y : y + h, x : x + w] == label,
                    255,
                    0,
                ).astype(np.uint8)
                _, hierarchy = cv2.findContours(
                    component, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE
                )
                holes = (
                    0
                    if hierarchy is None
                    else sum(1 for item in hierarchy[0] if item[3] >= 0)
                )
                matches.append((compactness + colored, (x, y, w, h), holes >= 2))
        if not matches:
            return None
        score, (x, y, w, h), digit_like = max(matches, key=lambda item: item[0])
        pad_x, pad_y = 22, 16
        left, top = max(0, x - pad_x), max(0, y - pad_y)
        right = min(crop.shape[1], x + w + pad_x)
        bottom = min(crop.shape[0], y + h + pad_y)
        # Sample the marker's central presentation, not neighboring HUD cues.
        center_x, center_y = x + w // 2, y + h // 2
        hue, concentration = _color_evidence(
            crop[
                max(0, center_y - 16) : min(crop.shape[0], center_y + 16),
                max(0, center_x - 16) : min(crop.shape[1], center_x + 16),
            ]
        )
        center = offset_x + x + w / 2
        lane_position = (x + w / 2) / max(1, crop.shape[1])
        bbox = (
            (offset_x + left) / frame_width,
            (offset_y + top) / frame_height,
            (offset_x + right) / frame_width,
            (offset_y + bottom) / frame_height,
        )
        return (
            TowerMarkerReading(
                center_x=center / frame_width,
                lane_position=lane_position,
                bbox=bbox,
                appearance=(
                    "colored_presentation"
                    if digit_like and concentration >= 0.55
                    else "neutral_like"
                ),
                hue=hue,
                hue_concentration=concentration,
                usable=True,
            ),
            min(1.0, score / 1.5),
        )


def _color_evidence(crop: np.ndarray) -> tuple[float | None, float]:
    """Circular hue mean/concentration for saturated, visible pixels."""
    if crop.size == 0:
        return None, 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    selected = hsv[(hsv[:, :, 1] >= 80) & (hsv[:, :, 2] >= 60), 0].astype(float)
    if selected.size < 20:
        return None, 0.0
    angles = selected * (2 * np.pi / 180.0)
    vector = np.mean(np.exp(1j * angles))
    hue = float((np.angle(vector) % (2 * np.pi)) * 180.0 / (2 * np.pi))
    return hue, float(abs(vector))


def _digit_rows(
    boxes: list[tuple[int, int, int, int]],
) -> list[list[tuple[int, int, int, int]]]:
    """Group adjacent, similarly sized glyph boxes into candidate score rows."""
    rows: list[list[tuple[int, int, int, int]]] = []
    for box in sorted(boxes, key=lambda item: (item[1], item[0])):
        x, y, w, h = box
        for row in rows:
            rx, ry, rw, rh = row[-1]
            if abs((y + h / 2) - (ry + rh / 2)) <= 8 and 0 <= x - (rx + rw) <= 16:
                row.append(box)
                break
        else:
            rows.append([box])
    return [row[:3] for row in rows if 1 <= len(row) <= 3]


def _read_pod_row(
    image: np.ndarray,
    mask: np.ndarray,
    row: list[tuple[int, int, int, int]],
    offset_x: int,
    offset_y: int,
    frame_width: int,
    frame_height: int,
    templates: dict[str, list[np.ndarray]],
    config: TowerControlDetectorConfig,
) -> TowerScorePodReading | None:
    """Read one localized row with the shared glyph matcher."""
    symbols: list[str] = []
    scores: list[float] = []
    for x, y, w, h in row:
        symbol, score = match_glyph(
            mask[y : y + h, x : x + w], templates, config.digit_match_threshold
        )
        if symbol is None:
            return None
        symbols.append(symbol)
        scores.append(float(score))
    confidence = float(np.mean(scores))
    if confidence < config.min_pod_confidence:
        return None
    parsed = int("".join(symbols))
    if parsed > 100:
        return None
    # A lone glyph may be a real late-match score, but development footage also
    # shows partial reads of multi-digit scores. Keep localization/color while
    # withholding the number until a single-digit validation set exists.
    value = parsed if len(symbols) >= 2 else None
    left = max(offset_x, offset_x + row[0][0] - 28)
    right = min(frame_width, offset_x + row[-1][0] + row[-1][2] + 28)
    top = max(offset_y, offset_y + min(box[1] for box in row) - 22)
    bottom = min(frame_height, offset_y + max(box[1] + box[3] for box in row) + 10)
    hue, concentration = _color_evidence(image[top:bottom, left:right])
    return TowerScorePodReading(
        value=value,
        bbox=(
            left / frame_width,
            top / frame_height,
            right / frame_width,
            bottom / frame_height,
        ),
        center_x=((left + right) / 2) / frame_width,
        hue=hue,
        hue_concentration=concentration,
        digit_scores=scores,
        usable=True,
    )
