"""YOLO-based object detection for players, enemies, and map objects."""

from loguru import logger

from splatoon3_ai_coach.media.video import VideoFrame
from splatoon3_ai_coach.vision.models import VisionDetection


class YoloDetector:
    """Run Ultralytics YOLO inference on gameplay frames."""

    name = "yolo"

    def __init__(self, weights_path: str | None = None) -> None:
        self.weights_path = weights_path

    def detect(self, frame: VideoFrame) -> list[VisionDetection]:
        """Return object detections for the given frame.

        TODO(phase-3): load ultralytics model and run inference.
        """
        logger.debug(
            "YoloDetector not yet implemented, skipping frame at {:.3f}s",
            frame.timestamp,
        )
        return []
