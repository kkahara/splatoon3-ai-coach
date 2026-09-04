"""Language-aware template and OCR path helpers.

Layout convention for detector template roots::

    calibration/templates/<detector>/
      skull/                 # language-neutral visual cues (icons, glyphs)
      en/                    # English text / keyword templates (optional)
      ja/                    # Japanese text / keyword templates (optional)

Prefer language-neutral assets for primary detection. Resolve localized
subdirectories only when a detector needs text templates or OCR.
"""

from __future__ import annotations

from pathlib import Path

from splatoon3_ai_coach.config.models import VisionConfig, VisionLanguage


def resolve_localized_template_dir(
    base_dir: Path,
    language: VisionLanguage | str,
) -> Path:
    """Return ``base_dir / language`` when it exists, otherwise ``base_dir``.

    Language-neutral packs (e.g. splat skull icons) live directly under
    ``base_dir``. Text templates for a locale live under a language subdirectory.
    """
    code = language.value if isinstance(language, VisionLanguage) else str(language)
    localized = base_dir / code
    if localized.is_dir():
        return localized
    return base_dir


def localized_template_dir(config: VisionConfig, base_dir: Path) -> Path:
    """Resolve a detector template root for the active vision language."""
    return resolve_localized_template_dir(base_dir, config.language)


def ocr_tesseract_lang(config: VisionConfig) -> str:
    """Tesseract language pack for ``config.language``."""
    return config.tesseract_lang()
