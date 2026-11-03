"""Rainmaker remaining-count observations and calibrated team association."""

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import RainmakerScoreConfig
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    RainmakerCountPod,
    RainmakerScoreAssociation,
    RainmakerScoreReading,
)
from splatoon3_ai_coach.vision.roi import pixel_box_from_normalized
from splatoon3_ai_coach.vision.team_color_calibration import (
    TeamColorCalibrationResult,
    circular_hue_distance,
)
from splatoon3_ai_coach.vision.templates import load_templates
from splatoon3_ai_coach.vision.timer import match_glyph


class RainmakerScoreDetector:
    """Read white count rows in the HUD count band, independently of possession."""

    name = "rainmaker_score"
    run_on_evidence = True

    def __init__(
        self, config: RainmakerScoreConfig, cadence_fps: float | None = None
    ) -> None:
        self.config = config
        self.cadence_fps = cadence_fps
        self.templates = load_templates(config.template_dir)

    def detect(
        self, image: np.ndarray, timestamp: float | None = None
    ) -> tuple[RainmakerScoreReading | None, float]:
        """Observe native digit boxes; unreadable rows produce no numeric assertion."""
        frame = cv2.resize(image, (1920, 1080), interpolation=cv2.INTER_AREA)
        x1, y1, x2, y2 = pixel_box_from_normalized(self.config.count_roi, 1920, 1080)
        hsv = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (0, 0, 175), (179, 105, 255))
        _, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        boxes = sorted(
            [
                tuple(int(v) for v in s[:4])
                for s in stats[1:]
                if 26 <= s[3] <= 38 and 4 <= s[2] <= 32 and s[4] >= 45
            ]
        )
        rows: list[list[tuple[int, int, int, int]]] = []
        for box in boxes:
            if rows and 0 <= box[0] - (rows[-1][-1][0] + rows[-1][-1][2]) <= 12:
                rows[-1].append(box)
            else:
                rows.append([box])
        pods = [self._pod(frame, mask, row, x1, y1) for row in rows]
        pods = [pod for pod in pods if pod is not None]
        if not pods or len(pods) > 2:
            return None, 0.0
        confidence = min(min(p.digit_scores) for p in pods)
        return RainmakerScoreReading(pods=pods, timestamp=timestamp), confidence

    def _pod(
        self,
        frame: np.ndarray,
        mask: np.ndarray,
        row: list[tuple[int, int, int, int]],
        ox: int,
        oy: int,
    ) -> RainmakerCountPod | None:
        """Require a complete row of matched digits on a saturated pod interior."""
        if not 1 <= len(row) <= 3:
            return None
        matches = [
            match_glyph(
                mask[y : y + h, x : x + w], self.templates, self.config.match_threshold
            )
            for x, y, w, h in row
        ]
        if any(symbol is None for symbol, _ in matches):
            return None
        value = int("".join(str(symbol) for symbol, _ in matches))
        if value > 100:
            return None
        left, top = ox + row[0][0] - 12, oy + min(b[1] for b in row)
        right = ox + row[-1][0] + row[-1][2] + 12
        bottom = oy + max(b[1] + b[3] for b in row)
        hsv = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2HSV)
        colored = (hsv[:, :, 1] >= 110) & (hsv[:, :, 2] >= 90)
        if colored.mean() < 0.25:
            return None
        angles = hsv[:, :, 0][colored] * (2 * np.pi / 180)
        vector = np.mean(np.exp(1j * angles))
        if abs(vector) < 0.9:
            return None
        return RainmakerCountPod(
            value=value,
            usable=True,
            digit_scores=[score for _, score in matches],
            bbox=(left / 1920, top / 1080, right / 1920, bottom / 1080),
            hue=float(np.angle(vector) % (2 * np.pi) * 180 / (2 * np.pi)),
        )


def associate_rainmaker_scores(
    detection: DetectorResult,
    calibration: TeamColorCalibrationResult | None,
) -> RainmakerScoreAssociation | None:
    """Bind uniquely matching pod colors to established match team colors."""
    if calibration is None or not isinstance(detection.reading, RainmakerScoreReading):
        return None
    assignments: dict[str, list[int]] = {"ally": [], "opponent": []}
    for index, pod in enumerate(detection.reading.pods):
        if pod.hue is None or not pod.usable:
            continue
        a = circular_hue_distance(pod.hue, calibration.ally_h)
        o = circular_hue_distance(pod.hue, calibration.opponent_h)
        if min(a, o) <= 18 and abs(a - o) >= 8:
            assignments["ally" if a < o else "opponent"].append(index)
    return RainmakerScoreAssociation(
        evidence_id=detection.id,
        calibration_time=calibration.calibrated_at,
        calibration_frame_ids=list(calibration.accepted_frame_ids),
        ally_pod=assignments["ally"][0] if len(assignments["ally"]) == 1 else None,
        opponent_pod=(
            assignments["opponent"][0] if len(assignments["opponent"]) == 1 else None
        ),
    )
