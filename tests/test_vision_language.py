"""Tests for vision language configuration and template resolution."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import (
    DeathDetectorConfig,
    RespawnDetectorConfig,
    SplatDetectorConfig,
    TimerDetectorConfig,
    VisionConfig,
    VisionLanguage,
    VisionOcrConfig,
)
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.exceptions import VisionError
from splatoon3_ai_coach.media.vision_manifest import hash_vision_config
from splatoon3_ai_coach.vision.death import DeathDetector
from splatoon3_ai_coach.vision.language import (
    localized_template_dir,
    ocr_tesseract_lang,
    resolve_language_template_dir,
)
from splatoon3_ai_coach.vision.registry import build_detectors
from splatoon3_ai_coach.vision.respawn import RespawnDetector
from splatoon3_ai_coach.vision.splat import SplatDetector

_REPO = Path(__file__).resolve().parents[1]
_RESPAWN = _REPO / "calibration" / "templates" / "respawn"
_SPLAT = _REPO / "calibration" / "templates" / "splat"
_DEATH = _REPO / "calibration" / "templates" / "death"


def test_default_config_sets_english_language() -> None:
    config = load_config(default_config_path())
    assert config.vision.language == VisionLanguage.EN
    assert config.vision.tesseract_lang() == "eng"
    assert config.vision.ocr.engine == "tesseract"


def test_invalid_language_is_rejected(timer_template_dir: Path) -> None:
    with pytest.raises(ValidationError):
        VisionConfig(
            language="fr",  # type: ignore[arg-type]
            timer=TimerDetectorConfig(
                roi=(0.0, 0.0, 1.0, 1.0),
                template_dir=timer_template_dir,
            ),
        )


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


def test_resolve_language_template_dir_requires_language_subdir(
    tmp_path: Path,
) -> None:
    base = tmp_path / "respawn"
    (base / "ja").mkdir(parents=True)
    assert resolve_language_template_dir(base, VisionLanguage.JA) == base / "ja"
    with pytest.raises(VisionError, match="language='en'"):
        resolve_language_template_dir(base, VisionLanguage.EN)


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


def test_language_changes_vision_config_hash(timer_template_dir: Path) -> None:
    en = VisionConfig(
        language=VisionLanguage.EN,
        timer=TimerDetectorConfig(
            roi=(0.0, 0.0, 1.0, 1.0),
            template_dir=timer_template_dir,
        ),
    )
    ja = en.model_copy(update={"language": VisionLanguage.JA})
    assert hash_vision_config(en) != hash_vision_config(ja)


def _template_names(detector_templates: list) -> int:
    return len(detector_templates)


def test_respawn_loads_only_language_templates() -> None:
    en = RespawnDetector(
        RespawnDetectorConfig(template_dir=_RESPAWN),
        language=VisionLanguage.EN,
    )
    ja = RespawnDetector(
        RespawnDetectorConfig(template_dir=_RESPAWN),
        language=VisionLanguage.JA,
    )
    assert en._template_dir == _RESPAWN / "en"
    assert ja._template_dir == _RESPAWN / "ja"
    assert _template_names(en._templates) == len(list((_RESPAWN / "en").glob("*.png")))
    assert _template_names(ja._templates) == len(list((_RESPAWN / "ja").glob("*.png")))
    assert en._templates and ja._templates
    # Distinct packs: do not silently merge both languages.
    assert len(en._templates) != len(ja._templates) or en._template_dir != ja._template_dir


def test_splat_loads_only_language_templates() -> None:
    en = SplatDetector(
        SplatDetectorConfig(template_dir=_SPLAT),
        language=VisionLanguage.EN,
    )
    ja = SplatDetector(
        SplatDetectorConfig(template_dir=_SPLAT),
        language=VisionLanguage.JA,
    )
    assert en._template_dir == _SPLAT / "en"
    assert ja._template_dir == _SPLAT / "ja"
    assert len(en._templates) == 2
    assert len(ja._templates) == 2
    assert len(en._icon_templates) == 1
    assert len(en._text_templates) == 1
    assert len(ja._icon_templates) == 1
    assert len(ja._text_templates) == 1


def test_death_ouch_and_splatted_load_only_language_templates() -> None:
    en = DeathDetector(
        DeathDetectorConfig(template_dir=_DEATH),
        language=VisionLanguage.EN,
    )
    ja = DeathDetector(
        DeathDetectorConfig(template_dir=_DEATH),
        language=VisionLanguage.JA,
    )
    assert len(en._ouch_templates) == len(list((_DEATH / "ouch" / "en").glob("*")))
    assert len(ja._ouch_templates) == len(list((_DEATH / "ouch" / "ja").glob("*")))
    assert len(en._banner_templates) == len(
        list((_DEATH / "splatted" / "en").glob("*"))
    )
    assert len(ja._banner_templates) == len(
        list((_DEATH / "splatted" / "ja").glob("*"))
    )
    assert len(en._ouch_templates) != len(ja._ouch_templates)


def test_registry_propagates_single_language(timer_template_dir: Path) -> None:
    config = VisionConfig(
        language=VisionLanguage.JA,
        enabled_detectors=["death", "splat", "respawn"],
        timer=TimerDetectorConfig(
            roi=(0.0, 0.0, 1.0, 1.0),
            template_dir=timer_template_dir,
        ),
        death=DeathDetectorConfig(template_dir=_DEATH),
        splat=SplatDetectorConfig(template_dir=_SPLAT),
        respawn=RespawnDetectorConfig(template_dir=_RESPAWN),
    )
    detectors = {d.name: d for d in build_detectors(config)}
    assert detectors["death"].language == "ja"  # type: ignore[attr-defined]
    assert detectors["splat"].language == "ja"  # type: ignore[attr-defined]
    assert detectors["respawn"].language == "ja"  # type: ignore[attr-defined]
    assert detectors["respawn"]._template_dir == _RESPAWN / "ja"  # type: ignore[attr-defined]
    assert detectors["splat"]._template_dir == _SPLAT / "ja"  # type: ignore[attr-defined]
