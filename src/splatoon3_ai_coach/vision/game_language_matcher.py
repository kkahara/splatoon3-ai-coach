"""Both-language localized cue matching for the pre-scan game-language probe.

Reports evidence only: the best-scoring language, its template, and the best
score of any other language. A cue is accepted only when it clears the
threshold *and* beats every other language by the margin. Absence of a cue is
never evidence for a language. Matching reuses the ``match_intro`` primitives
(CLAHE grayscale + ``TM_CCOEFF_NORMED``) without changing that detector.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import (
    GameLanguageConfig,
    MatchIntroDetectorConfig,
)
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.paths import portable_path
from splatoon3_ai_coach.types import NormalizedBox
from splatoon3_ai_coach.vision.match_intro import (
    BATTLE_MODE_IDS,
    _fit_template,
    _template_id_from_stem,
    _to_gray,
)
from splatoon3_ai_coach.vision.models import GameLanguageCueReading, GameLanguageGate
from splatoon3_ai_coach.vision.roi import crop_roi

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


@dataclass(frozen=True)
class LocalizedTemplate:
    """One grayscale cue template and the language pack it came from."""

    language: str
    cue_id: str
    template_path: str
    image: np.ndarray


def load_intro_mode_templates(
    intro: MatchIntroDetectorConfig,
    languages: Sequence[str],
) -> list[LocalizedTemplate]:
    """Load battle-mode label templates from every language pack."""
    if intro.template_dir is None:
        return []
    templates: list[LocalizedTemplate] = []
    for language in languages:
        folder = intro.template_dir / language / "battle_modes"
        for path in _image_files(folder):
            mode_id = _template_id_from_stem(path.stem)
            if mode_id not in BATTLE_MODE_IDS:
                continue
            templates.extend(_load(path, language, f"battle_mode/{mode_id}"))
    return templates


def load_callout_templates(
    config: GameLanguageConfig,
    languages: Sequence[str],
) -> list[LocalizedTemplate]:
    """Load ``template_dir/<language>/<cue>.png`` for every language and cue."""
    if config.template_dir is None:
        return []
    templates: list[LocalizedTemplate] = []
    for language in languages:
        for cue in config.cues:
            for suffix in sorted(_IMAGE_SUFFIXES):
                path = config.template_dir / language / f"{cue}{suffix}"
                if path.is_file():
                    templates.extend(_load(path, language, f"callout/{cue}"))
                    break
    return templates


def _image_files(folder: Path) -> list[Path]:
    """Sorted image files directly inside ``folder`` (empty when missing)."""
    if not folder.is_dir():
        return []
    return [p for p in sorted(folder.iterdir()) if p.suffix.lower() in _IMAGE_SUFFIXES]


def _load(path: Path, language: str, cue_id: str) -> list[LocalizedTemplate]:
    """Read one template as grayscale; unreadable files contribute nothing."""
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None or image.size == 0:
        return []
    return [
        LocalizedTemplate(
            language=language,
            cue_id=cue_id,
            template_path=portable_path(path, PROJECT_ROOT),
            image=image,
        )
    ]


@dataclass(frozen=True)
class _Score:
    template: LocalizedTemplate
    score: float


class LocalizedCueMatcher:
    """Match every language's templates in one ROI and apply the margin rule."""

    def __init__(
        self,
        gate: GameLanguageGate,
        templates: Sequence[LocalizedTemplate],
        *,
        roi: NormalizedBox,
        threshold: float,
        margin: float,
        reference_height: int,
    ) -> None:
        self.gate = gate
        self.templates = list(templates)
        self.roi = roi
        self.threshold = threshold
        self.margin = margin
        self.reference_height = reference_height

    def observe(self, image: np.ndarray) -> GameLanguageCueReading:
        """Return the both-language reading for one frame."""
        crop = crop_roi(image, self.roi)
        if not self.templates or crop.size == 0:
            return GameLanguageCueReading(gate=self.gate, rejection="no_templates")
        scale = image.shape[0] / float(self.reference_height)
        gray = _to_gray(crop)
        best_by_language: dict[str, _Score] = {}
        for template in self.templates:
            score = _match(gray, template.image, scale)
            if score is None:
                continue
            current = best_by_language.get(template.language)
            if current is None or score > current.score:
                best_by_language[template.language] = _Score(template, score)
        return self._decide(best_by_language)

    def _decide(self, best_by_language: dict[str, _Score]) -> GameLanguageCueReading:
        """Apply threshold then cross-language margin to per-language bests."""
        if not best_by_language:
            return GameLanguageCueReading(gate=self.gate, rejection="no_templates")
        ranked = sorted(best_by_language.values(), key=lambda s: -s.score)
        best = ranked[0]
        runner = ranked[1] if len(ranked) > 1 else None
        runner_score = runner.score if runner is not None else 0.0
        rejection = None
        if best.score < self.threshold:
            rejection = "below_threshold"
        elif best.score - runner_score < self.margin:
            rejection = "ambiguous_margin"
        return GameLanguageCueReading(
            gate=self.gate,
            candidate_language=best.template.language,
            cue_id=best.template.cue_id,
            template_path=best.template.template_path,
            score=_clip(best.score),
            runner_up_language=None if runner is None else runner.template.language,
            runner_up_score=None if runner is None else _clip(runner.score),
            accepted=rejection is None,
            rejection=rejection,
        )


def _match(gray: np.ndarray, template: np.ndarray, scale: float) -> float | None:
    """Best ``TM_CCOEFF_NORMED`` score of ``template`` inside ``gray``."""
    if abs(scale - 1.0) > 1e-3:
        th, tw = template.shape[:2]
        size = (max(4, round(tw * scale)), max(4, round(th * scale)))
        template = cv2.resize(template, size, interpolation=cv2.INTER_AREA)
    fitted = _fit_template(gray, template)
    if fitted is None:
        return None
    result = cv2.matchTemplate(gray, fitted, cv2.TM_CCOEFF_NORMED)
    return float(np.nan_to_num(result).max())


def _clip(score: float) -> float:
    """Clamp a correlation score into the persisted [-1, 1] range."""
    return round(max(-1.0, min(1.0, score)), 6)
