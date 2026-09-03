"""Central logging configuration for CLI and library use."""

import sys

from loguru import logger


def configure_logging(verbose: bool = False) -> None:
    """Configure loguru once for the current process."""
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(sys.stderr, level=level, format="{time:HH:mm:ss} | {level:<7} | {message}")
