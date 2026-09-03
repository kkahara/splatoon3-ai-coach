"""YOLO training entry point.

Training data and weights live here, outside the installable package, so they
are not bundled into the wheel.

TODO(phase-3): implement ultralytics training loop.
TODO(phase-6): map recognition (Ancho-V, Museum, etc.).
TODO(phase-6): weapon recognition.

Usage (future):
    python training/train.py --data training/datasets/splatoon3.yaml
"""

from loguru import logger


def main() -> None:
    """Placeholder training entry point."""
    logger.warning("Training not yet implemented. See training/train.py TODOs.")


if __name__ == "__main__":
    main()
