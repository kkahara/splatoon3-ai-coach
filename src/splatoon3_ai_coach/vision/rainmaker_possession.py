"""Direct localized Rainmaker possession-message observations."""

from typing import Literal

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import RainmakerPossessionConfig
from splatoon3_ai_coach.vision.models import (
    RainmakerPossessionCue,
    RainmakerPossessionReading,
)

RainmakerCueName = Literal[
    "ally_acquired_rainmaker",
    "opponent_acquired_rainmaker",
    "ally_lost_rainmaker",
    "opponent_lost_rainmaker",
    "rainmaker_reset",
]


class RainmakerPossessionDetector:
    """Match only configured language-specific whole-message templates."""

    name = "rainmaker_possession"
    run_on_evidence = True

    def __init__(
        self,
        config: RainmakerPossessionConfig,
        *,
        language: str,
        cadence_fps: float | None = None,
    ) -> None:
        self.threshold = config.match_threshold
        self.margin = config.match_margin
        self.language = language if language in ("en", "ja") else "en"
        self.cadence_fps = cadence_fps
        self.templates: list[tuple[RainmakerCueName, str, np.ndarray]] = []
        folder = config.template_dir / self.language / "possession"
        if folder.is_dir():
            for path in sorted(folder.glob("*.png")):
                cue_name = path.stem.split("__", 1)[0]
                if cue_name not in _CUES:
                    continue
                template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                if template is not None and template.size:
                    self.templates.append((cue_name, path.name, template))

    def detect(
        self, image: np.ndarray, timestamp: float | None = None
    ) -> tuple[RainmakerPossessionReading | None, float]:
        """Return a unique direct-message match; uncertainty stays absent."""
        if not self.templates or image.ndim != 3 or not image.size:
            return None, 0.0
        frame = cv2.resize(image, (1920, 1080), interpolation=cv2.INTER_AREA)
        region = cv2.cvtColor(frame[250:380, 300:1600], cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(region, 210, 255, cv2.THRESH_BINARY)
        scores = self._scores(binary)
        if not scores or scores[0][0] < self.threshold:
            return None, 0.0
        best_score, cue_name, filename = scores[0]
        if any(name != cue_name and best_score - score < self.margin
               for score, name, _ in scores[1:]):
            return None, 0.0
        cue = RainmakerPossessionCue(
            cue=cue_name,
            template_score=min(1.0, max(0.0, best_score)),
            language=self.language,
            template_name=filename,
        )
        reading = RainmakerPossessionReading(cue=cue, timestamp=timestamp)
        return reading, cue.template_score

    def _scores(
        self, binary: np.ndarray
    ) -> list[tuple[float, RainmakerCueName, str]]:
        """Score every fitting mask and retain the best match per template."""
        scores = []
        for cue_name, filename, template in self.templates:
            if template.shape[0] > binary.shape[0] or template.shape[1] > binary.shape[1]:
                continue
            match = cv2.matchTemplate(binary, template, cv2.TM_CCOEFF_NORMED)
            score = float(match.max())
            if np.isfinite(score):
                scores.append((score, cue_name, filename))
        return sorted(scores, reverse=True)


_CUES: set[str] = {
    "ally_acquired_rainmaker",
    "opponent_acquired_rainmaker",
    "ally_lost_rainmaker",
    "opponent_lost_rainmaker",
    "rainmaker_reset",
}
