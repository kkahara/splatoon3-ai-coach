"""Match-intro identity: stage + battle mode from pre-match UI templates.

Templates only (no OCR). Detection may stop once both IDs are resolved.
Unresolved identity disables map-ink analysis for the match.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

from splatoon3_ai_coach.config.models import MatchIntroDetectorConfig, VisionLanguage
from splatoon3_ai_coach.vision.language import resolve_language_template_dir
from splatoon3_ai_coach.vision.models import MatchIntroReading
from splatoon3_ai_coach.vision.roi import crop_roi

BATTLE_MODE_IDS: tuple[str, ...] = (
    "turf_war",
    "splat_zones",
    "tower_control",
    "rainmaker",
    "clam_blitz",
)

BATTLE_MODE_LABELS: dict[str, dict[str, str]] = {
    "turf_war": {"en": "Turf War", "ja": "ナワバリバトル"},
    "splat_zones": {"en": "Splat Zones", "ja": "ガチエリア"},
    "tower_control": {"en": "Tower Control", "ja": "ガチヤグラ"},
    "rainmaker": {"en": "Rainmaker", "ja": "ガチホコバトル"},
    "clam_blitz": {"en": "Clam Blitz", "ja": "ガチアサリ"},
}


def _to_gray(roi: np.ndarray) -> np.ndarray:
    """Contrast-normalized grayscale for template matching."""
    if roi.size == 0:
        return roi
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    return clahe.apply(gray)


def _fit_template(image: np.ndarray, template: np.ndarray) -> np.ndarray | None:
    """Shrink a template so it fits inside ``image``."""
    th, tw = template.shape[:2]
    ih, iw = image.shape[:2]
    if th < 4 or tw < 4 or ih < 4 or iw < 4:
        return None
    scale = min(1.0, ih / th, iw / tw)
    if scale < 1.0:
        template = cv2.resize(
            template,
            (max(4, int(tw * scale)), max(4, int(th * scale))),
            interpolation=cv2.INTER_AREA,
        )
    if template.shape[0] > ih or template.shape[1] > iw:
        return None
    return template


def _best_named_match(
    roi: np.ndarray,
    templates: dict[str, np.ndarray],
) -> tuple[str | None, float]:
    """Return (id, score) for the best template match against ``roi``."""
    if not templates or roi.size == 0:
        return None, 0.0
    gray = _to_gray(roi)
    best_id: str | None = None
    best_score = 0.0
    for item_id, template in templates.items():
        scaled = _fit_template(gray, template)
        if scaled is None:
            continue
        result = cv2.matchTemplate(gray, scaled, cv2.TM_CCOEFF_NORMED)
        score = float(result.max())
        if score > best_score:
            best_score = score
            best_id = item_id
    return best_id, best_score


# Optional filename prefixes (folder already selects language).
_TEMPLATE_ID_PREFIXES: tuple[str, ...] = ("en-", "ja-", "jp-", "eg-")


def _template_id_from_stem(stem: str) -> str:
    """Map a template filename stem to a canonical battle_mode_id / stage_id.

    Accepts bare IDs (``turf_war``) or language-tagged names
    (``en-turf_war``, ``jp-mahi_mahi_resort``, ``eg-…`` typo for ``en-``).
    """
    lower = stem.lower()
    for prefix in _TEMPLATE_ID_PREFIXES:
        if lower.startswith(prefix) and len(stem) > len(prefix):
            return stem[len(prefix) :]
    return stem


def _load_named_templates(directory: Path) -> dict[str, np.ndarray]:
    """Load ``{id: grayscale}`` from PNG/JPG files in ``directory``.

    Partial packs are fine — only present templates are matched. Missing stages
    simply cannot resolve until their PNGs are added.
    """
    if not directory.is_dir():
        return {}
    loaded: dict[str, np.ndarray] = {}
    for path in sorted(directory.glob("*")):
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            continue
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None or image.size == 0:
            continue
        item_id = _template_id_from_stem(path.stem)
        if item_id in loaded:
            logger.warning(
                "Match intro template id {!r} duplicated by {}; keeping last",
                item_id,
                path.name,
            )
        loaded[item_id] = image
    return loaded


@dataclass
class MatchIdentity:
    """Resolved (or unresolved) match stage + battle mode for one analyze run."""

    stage_id: str | None = None
    battle_mode_id: str | None = None
    stage_score: float = 0.0
    battle_mode_score: float = 0.0
    resolved_at: float | None = None
    intro_closed: bool = False

    @property
    def resolved(self) -> bool:
        """True when both stage and battle mode IDs are known."""
        return self.stage_id is not None and self.battle_mode_id is not None

    @property
    def map_ink_enabled(self) -> bool:
        """Map ink may run only when identity is fully resolved."""
        return self.resolved


@dataclass
class MatchIdentityTracker:
    """Latch intro detections; early-stop once both IDs are known."""

    identity: MatchIdentity = field(default_factory=MatchIdentity)
    intro_deadline_seconds: float = 90.0

    def update(self, reading: MatchIntroReading, *, video_time: float) -> None:
        """Absorb a frame reading unless identity is already resolved or closed."""
        if self.identity.resolved or self.identity.intro_closed:
            return
        if (
            self.identity.stage_id is None
            and reading.stage_id is not None
            and reading.stage_template_score > self.identity.stage_score
        ):
            self.identity.stage_id = reading.stage_id
            self.identity.stage_score = reading.stage_template_score
        if (
            self.identity.battle_mode_id is None
            and reading.battle_mode_id is not None
            and reading.battle_mode_template_score > self.identity.battle_mode_score
        ):
            self.identity.battle_mode_id = reading.battle_mode_id
            self.identity.battle_mode_score = reading.battle_mode_template_score
        if self.identity.resolved:
            self.identity.resolved_at = float(video_time)
            logger.info(
                "Match identity resolved at {:.1f}s: stage={} mode={}",
                video_time,
                self.identity.stage_id,
                self.identity.battle_mode_id,
            )
            return
        if video_time >= self.intro_deadline_seconds:
            self.close_intro(video_time)

    def close_intro(self, video_time: float) -> None:
        """Stop accepting intro evidence (fail-closed if still unresolved)."""
        if self.identity.intro_closed:
            return
        self.identity.intro_closed = True
        if not self.identity.resolved:
            logger.warning(
                "Match identity unresolved by {:.1f}s (stage={}, mode={}); "
                "map ink disabled for this match",
                video_time,
                self.identity.stage_id,
                self.identity.battle_mode_id,
            )

    def should_run_intro_detector(self, video_time: float) -> bool:
        """Whether the intro detector should still run on this frame."""
        if self.identity.resolved or self.identity.intro_closed:
            return False
        if video_time >= self.intro_deadline_seconds:
            self.close_intro(video_time)
            return False
        return True


class MatchIntroDetector:
    """Template-match battle mode (center) and stage (bottom-right) on intro UI."""

    name = "match_intro"
    run_on_evidence = True

    def __init__(
        self,
        config: MatchIntroDetectorConfig,
        *,
        language: VisionLanguage | str,
        cadence_fps: float | None = None,
    ) -> None:
        self.config = config
        self.cadence_fps = cadence_fps
        self._mode_templates: dict[str, np.ndarray] = {}
        self._stage_templates: dict[str, np.ndarray] = {}
        self._load_templates(language)

    def _load_templates(self, language: VisionLanguage | str) -> None:
        """Load language-specific mode and stage template packs."""
        if self.config.template_dir is None:
            logger.warning("MatchIntroDetector: template_dir unset")
            return
        try:
            root = resolve_language_template_dir(self.config.template_dir, language)
        except Exception as exc:  # noqa: BLE001 — missing lang dir is soft-fail
            logger.warning("MatchIntroDetector: {}", exc)
            return
        self._mode_templates = _load_named_templates(root / "battle_modes")
        self._stage_templates = _load_named_templates(root / "stages")
        if not self._mode_templates:
            logger.warning("MatchIntroDetector: no battle_mode templates under {}", root)
        if not self._stage_templates:
            logger.warning("MatchIntroDetector: no stage templates under {}", root)

    def detect(
        self,
        image: np.ndarray,
        timestamp: float | None = None,
    ) -> tuple[MatchIntroReading, float]:
        """Observe intro plates; always returns a reading."""
        _ = timestamp
        mode_roi = crop_roi(image, self.config.battle_mode_roi)
        stage_roi = crop_roi(image, self.config.stage_roi)
        mode_id, mode_score = _best_named_match(mode_roi, self._mode_templates)
        stage_id, stage_score = _best_named_match(stage_roi, self._stage_templates)
        threshold = self.config.match_threshold
        reading = MatchIntroReading(
            stage_id=stage_id if stage_score >= threshold else None,
            battle_mode_id=mode_id if mode_score >= threshold else None,
            stage_template_score=stage_score,
            battle_mode_template_score=mode_score,
        )
        confidence = max(mode_score, stage_score)
        if reading.stage_id and reading.battle_mode_id:
            confidence = min(mode_score, stage_score)
        return reading, float(confidence)
