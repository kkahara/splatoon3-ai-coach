"""Registry of available vision detectors."""

from splatoon3_ai_coach.config.models import VisionConfig
from splatoon3_ai_coach.vision.active_gameplay import ActiveGameplayDetector
from splatoon3_ai_coach.vision.base import BaseDetector
from splatoon3_ai_coach.vision.death import DeathDetector
from splatoon3_ai_coach.vision.map_overlay import MapOverlayDetector
from splatoon3_ai_coach.vision.player_count import PlayerCountDetector
from splatoon3_ai_coach.vision.respawn import RespawnDetector
from splatoon3_ai_coach.vision.splat import SplatDetector
from splatoon3_ai_coach.vision.timer import TimerDetector


def build_detectors(config: VisionConfig) -> list[BaseDetector]:
    """Construct configured detectors for an analysis run."""
    detectors: list[BaseDetector] = []
    if "timer" in config.enabled_detectors:
        detectors.append(
            TimerDetector(config.timer, cadence_fps=config.hud_cadence_fps)
        )
    if "death" in config.enabled_detectors:
        detectors.append(
            DeathDetector(
                config.death,
                cadence_fps=config.hud_cadence_fps,
                language=config.language,
            )
        )
    if "splat" in config.enabled_detectors:
        detectors.append(
            SplatDetector(
                config.splat,
                cadence_fps=config.hud_cadence_fps,
                language=config.language,
            )
        )
    if "respawn" in config.enabled_detectors:
        detectors.append(
            RespawnDetector(
                config.respawn,
                cadence_fps=config.hud_cadence_fps,
                language=config.language,
                ocr_lang=config.tesseract_lang(),
            )
        )
    if "active_gameplay" in config.enabled_detectors:
        detectors.append(
            ActiveGameplayDetector(
                config.active_gameplay,
                cadence_fps=config.hud_cadence_fps,
            )
        )
    if "map_overlay" in config.enabled_detectors:
        detectors.append(
            MapOverlayDetector(
                config.map_overlay,
                cadence_fps=config.hud_cadence_fps,
            )
        )
    if "player_count" in config.enabled_detectors:
        detectors.append(
            PlayerCountDetector(
                config.player_count,
                cadence_fps=config.hud_cadence_fps,
            )
        )
    return detectors
