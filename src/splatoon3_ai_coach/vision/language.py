"""Language-aware template and OCR path helpers.

Layout convention for detector template roots::

    calibration/templates/<detector>/
      en/   # English text / keyword templates
      ja/   # Japanese text / keyword templates

    calibration/templates/death/
      ouch/en/   ouch/ja/
      splatted/en/   splatted/ja/

Language is an analysis-level setting (``VisionConfig.language``). Detectors
must not infer language from frames, OCR, or template scores. Resource
selection is deterministic: only ``base_dir / {language}`` is used, with no
fallback to the parent directory or the other language.
"""

from __future__ import annotations

from pathlib import Path

from splatoon3_ai_coach.config.models import VisionConfig, VisionLanguage
from splatoon3_ai_coach.exceptions import VisionError


def resolve_language_template_dir(
    base_dir: Path,
    language: VisionLanguage | str,
) -> Path:
    """Return ``base_dir / language``, requiring that directory to exist.

    Raises:
        VisionError: When the language subdirectory is missing.
    """
    code = language.value if isinstance(language, VisionLanguage) else str(language)
    localized = base_dir / code
    if not localized.is_dir():
        raise VisionError(
            f"Language template directory not found for language={code!r}: "
            f"{localized}"
        )
    return localized


# Backward-compatible name used by older tests and docs.
resolve_localized_template_dir = resolve_language_template_dir


def localized_template_dir(config: VisionConfig, base_dir: Path) -> Path:
    """Resolve a detector template root for the active vision language."""
    return resolve_language_template_dir(base_dir, config.language)


def ocr_tesseract_lang(config: VisionConfig) -> str:
    """Tesseract language pack for ``config.language``."""
    return config.tesseract_lang()
