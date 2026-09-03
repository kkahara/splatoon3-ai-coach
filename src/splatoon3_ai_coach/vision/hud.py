"""HUD template matching and OCR for Splatoon HUD elements."""

from loguru import logger

from splatoon3_ai_coach.media.video import VideoFrame
from splatoon3_ai_coach.vision.models import VisionDetection


class HudDetector:
    """Read Splatoon HUD state from normalized screen regions."""

    name = "hud"

    def detect(self, frame: VideoFrame) -> list[VisionDetection]:
        """Return HUD readings for the given frame.

        TODO(phase-3): template matching + OCR for ink %, alive count, timer.
        """
        logger.debug(
            "HudDetector not yet implemented, skipping frame at {:.3f}s",
            frame.timestamp,
        )
        return []
