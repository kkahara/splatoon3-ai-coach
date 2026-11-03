"""Development detection of localized Tower acquisition and loss messages."""

from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from splatoon3_ai_coach.vision.models import TowerControlCue

CueType = Literal["ally_took", "opponent_took", "ally_lost", "opponent_lost"]
_CUE_TYPES: tuple[CueType, ...] = (
    "ally_took",
    "opponent_took",
    "ally_lost",
    "opponent_lost",
)


class TowerControlMessageDetector:
    """Match only supplied language-specific whole-message templates."""

    def __init__(
        self, template_dir: Path, threshold: float = 0.93, language: str | None = None
    ) -> None:
        self.threshold = threshold
        self.language = language if language in ("en", "ja") else None
        self.templates: dict[CueType, list[np.ndarray]] = {}
        for cue_type in _CUE_TYPES:
            paths = [
                template_dir / f"{cue_type}.png",
                *sorted(template_dir.glob(f"{cue_type}__*.png")),
            ]
            for path in paths:
                if not path.is_file():
                    continue
                template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                if template is not None and template.size:
                    self.templates.setdefault(cue_type, []).append(template)

    def detect(self, image: np.ndarray) -> TowerControlCue | None:
        """Return a unique positive message match; ambiguity stays unknown."""
        if not self.templates or image.ndim != 3 or image.shape[2] != 3 or not image.size:
            return None
        frame = cv2.resize(image, (1920, 1080), interpolation=cv2.INTER_AREA)
        region = cv2.cvtColor(frame[250:380, 300:1600], cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(region, 210, 255, cv2.THRESH_BINARY)
        scores: list[tuple[float, CueType]] = []
        for cue_type, templates in self.templates.items():
            for template in templates:
                if any(a < b for a, b in zip(binary.shape, template.shape, strict=True)):
                    continue
                score = float(
                    cv2.matchTemplate(binary, template, cv2.TM_CCOEFF_NORMED).max()
                )
                if np.isfinite(score):
                    scores.append((score, cue_type))
        scores.sort(reverse=True)
        if not scores or scores[0][0] < self.threshold:
            return None
        if any(
            cue_type != scores[0][1] and scores[0][0] - score < 0.04
            for score, cue_type in scores[1:]
        ):
            return None
        score, cue_type = scores[0]
        return TowerControlCue(
            cue_type=cue_type,
            team="ally" if cue_type.startswith("ally_") else "opponent",
            template_score=min(1.0, max(0.0, score)),
            language=self.language,
        )
