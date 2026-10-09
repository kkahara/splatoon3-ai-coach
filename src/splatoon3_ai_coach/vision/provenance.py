"""Detector and pipeline version provenance.

Detector versions are part of evidence identity, so they carry only the
detector name and package version. The git commit is run provenance: it is
resolved once per run and stored on ``AnalysisIdentity.git_sha``.
"""

import subprocess
from functools import lru_cache
from importlib.metadata import version

PIPELINE_VERSION = "3.0.0"
PACKAGE_NAME = "splatoon3-ai-coach"


@lru_cache(maxsize=1)
def package_version() -> str:
    """Return the installed package version."""
    try:
        return version(PACKAGE_NAME)
    except Exception:
        return "0.0.0"


def git_short_sha() -> str | None:
    """Return the current Git short SHA when available (call once per run)."""
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


def detector_version(detector_name: str, package: str | None = None) -> str:
    """Identity version for a detector: ``name@package_version``, never a commit."""
    return f"{detector_name}@{package or package_version()}"
