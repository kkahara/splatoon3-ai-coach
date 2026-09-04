"""Tests for vision language configuration and template resolution."""

from pathlib import Path

from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import (
    TimerDetectorConfig,
    VisionConfig,
    VisionLanguage,
    VisionOcrConfig,
)
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.vision.language import (
    localized_template_dir,
    ocr_tesseract_lang,
    resolve_localized_template_dir,
)


def test_default_config_sets_english_language() -> None:
    config = load_config(default_config_path())
    assert config.vision.language == VisionLanguage.EN
    assert config.vision.tesseract_lang() == "eng"
    assert config.vision.ocr.engine == "tesseract"


def test_ocr_tesseract_lang_follows_vision_language(
    timer_template_dir: Path,
) -> None:
    config = VisionConfig(
        language=VisionLanguage.JA,
        ocr=VisionOcrConfig(),
        timer=TimerDetectorConfig(
            roi=(0.0, 0.0, 1.0, 1.0),
            template_dir=timer_template_dir,
        ),
    )
    assert ocr_tesseract_lang(config) == "jpn"
    config.language = VisionLanguage.EN
    assert ocr_tesseract_lang(config) == "eng"


def test_resolve_localized_template_dir_prefers_language_subdir(
    tmp_path: Path,
) -> None:
    base = tmp_path / "splat"
    (base / "skull").mkdir(parents=True)
    (base / "ja").mkdir()
    assert resolve_localized_template_dir(base, VisionLanguage.JA) == base / "ja"
    assert resolve_localized_template_dir(base, VisionLanguage.EN) == base


def test_localized_template_dir_uses_vision_language(
    timer_template_dir: Path,
    tmp_path: Path,
) -> None:
    base = tmp_path / "text_templates"
    (base / "en").mkdir(parents=True)
    config = VisionConfig(
        language=VisionLanguage.EN,
        timer=TimerDetectorConfig(
            roi=(0.0, 0.0, 1.0, 1.0),
            template_dir=timer_template_dir,
        ),
    )
    assert localized_template_dir(config, base) == base / "en"


def test_splat_detector_does_not_require_language_subdir(
    timer_template_dir: Path,
) -> None:
    """Skull templates stay language-neutral under template_dir/skull/."""
    from splatoon3_ai_coach.config.models import SplatDetectorConfig
    from splatoon3_ai_coach.vision.registry import build_detectors

    repo = Path(__file__).resolve().parents[1]
    config = VisionConfig(
        language=VisionLanguage.JA,
        enabled_detectors=["splat"],
        timer=TimerDetectorConfig(
            roi=(0.0, 0.0, 1.0, 1.0),
            template_dir=timer_template_dir,
        ),
        splat=SplatDetectorConfig(
            template_dir=repo / "calibration" / "templates" / "splat",
        ),
    )
    detectors = build_detectors(config)
    assert len(detectors) == 1
    assert detectors[0].name == "splat"
    assert len(detectors[0]._templates) > 0  # type: ignore[attr-defined]
