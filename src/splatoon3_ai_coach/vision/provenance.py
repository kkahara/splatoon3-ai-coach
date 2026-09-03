"""Detector and pipeline version provenance."""

import subprocess
from importlib.metadata import version

PIPELINE_VERSION = "3.0.0"
PACKAGE_NAME = "splatoon3-ai-coach"


def package_version() -> str:
    """Return the installed package version."""
    try:
        return version(PACKAGE_NAME)
    except Exception:
        return "0.0.0"


def git_short_sha() -> str | None:
    """Return the current Git short SHA when available."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def detector_version(detector_name: str) -> str:
    """Build a detector version string from package metadata and optional Git SHA."""
    base = f"{detector_name}@{package_version()}"
    sha = git_short_sha()
    return f"{base}+{sha}" if sha else base
