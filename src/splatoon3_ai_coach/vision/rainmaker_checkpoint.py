"""Direct Rainmaker checkpoint-breakthrough message detection."""

from typing import Literal

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import RainmakerCheckpointConfig
from splatoon3_ai_coach.vision.models import RainmakerCheckpointReading


class RainmakerCheckpointDetector:
    """Recognize supplied whole-message masks with conservative ambiguity rejection."""

    name = "rainmaker_checkpoint"
    run_on_evidence = True

    def __init__(
        self,
        config: RainmakerCheckpointConfig,
        *,
        language: Literal["en", "ja"],
        cadence_fps: float | None = None,
    ) -> None:
        self.config = config
        self.language = language
        self.cadence_fps = cadence_fps
        self.templates: list[tuple[str, str, np.ndarray]] = []
        folder = config.template_dir / language / "checkpoint"
        for path in sorted(folder.glob("*.png")):
            team = path.stem.split("__", 1)[0]
            if team not in ("ally", "opponent"):
                continue
            mask = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if mask is not None and mask.size:
                self.templates.append((team, path.name, mask))

    def detect(
        self, image: np.ndarray, timestamp: float | None = None
    ) -> tuple[RainmakerCheckpointReading | None, float]:
        """Return only a unique explicit breakthrough message match."""
        if image.ndim != 3 or image.shape[2] != 3 or not image.size:
            return None, 0.0
        frame = cv2.resize(image, (1920, 1080), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(frame[250:380, 200:1720], cv2.COLOR_BGR2GRAY)
        binary = cv2.threshold(gray, 210, 255, cv2.THRESH_BINARY)[1]
        scores = self._scores(binary)
        if not scores or scores[0][0] < self.config.match_threshold:
            return None, 0.0
        score, team, filename = scores[0]
        if any(
            other != team and score - value < self.config.match_margin
            for value, other, _ in scores[1:]
        ):
            return None, 0.0
        reading = RainmakerCheckpointReading(
            team=team,
            language=self.language,
            template_name=filename,
            template_score=min(1.0, max(0.0, score)),
            timestamp=timestamp,
        )
        return reading, reading.template_score

    def _scores(self, binary: np.ndarray) -> list[tuple[float, str, str]]:
        """Compare the same thresholded message representation across templates."""
        scores = []
        for team, filename, mask in self.templates:
            if any(a < b for a, b in zip(binary.shape, mask.shape, strict=True)):
                continue
            score = float(cv2.matchTemplate(binary, mask, cv2.TM_CCOEFF_NORMED).max())
            if np.isfinite(score):
                scores.append((score, team, filename))
        return sorted(scores, reverse=True)
