"""Central logging configuration for CLI and library use."""

import sys

from loguru import logger


def configure_logging(verbose: bool = False) -> None:
    """Configure loguru once for the current process.

    ``diagnose`` and ``backtrace`` stay off: loguru would otherwise print the
    values of local variables in exception tracebacks, which can include
    storage credentials and request signatures.
    """
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(
        sys.stderr,
        level=level,
        format="{time:HH:mm:ss} | {level:<7} | {message}",
        backtrace=False,
        diagnose=False,
    )
