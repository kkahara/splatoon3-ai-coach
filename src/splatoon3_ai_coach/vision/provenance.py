"""Detector and pipeline version provenance.

Detector versions are part of evidence identity, so they carry only the
detector name and package version. The git commit is run provenance: it is
resolved once per run and stored on ``AnalysisIdentity.git_sha``.
"""

import re
import subprocess
from functools import lru_cache
from importlib.metadata import version
from importlib.resources import files
from importlib.resources.abc import Traversable

from loguru import logger

PIPELINE_VERSION = "3.0.0"
PACKAGE_NAME = "splatoon3-ai-coach"
BUILD_COMMIT_FILE = "BUILD_COMMIT"
_FULL_SHA = re.compile(r"[0-9a-f]{40}")


@lru_cache(maxsize=1)
def package_version() -> str:
    """Return the installed package version."""
    try:
        return version(PACKAGE_NAME)
    except Exception:
        return "0.0.0"


def build_commit_file() -> Traversable:
    """Location of the commit file written by the image build."""
    return files("splatoon3_ai_coach") / BUILD_COMMIT_FILE


def build_commit() -> str | None:
    """Return the full commit SHA recorded by the image build, if any.

    The image has no ``.git`` and no ``git``; the Dockerfile writes the
    verified ``GIT_SHA`` build argument to this file instead.
    """
    path = build_commit_file()
    try:
        if not path.is_file():
            return None
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        logger.warning("Unreadable {}; commit not recorded", BUILD_COMMIT_FILE)
        return None
    if not _FULL_SHA.fullmatch(text):
        logger.warning("Malformed {}; commit not recorded", BUILD_COMMIT_FILE)
        return None
    return text


def git_short_sha() -> str | None:
    """Return the commit this code runs from (call once per run).

    An image build's recorded commit (full SHA) wins; otherwise the short SHA
    of the git checkout, or ``None`` when neither is available.
    """
    baked = build_commit()
    if baked is not None:
        return baked
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
