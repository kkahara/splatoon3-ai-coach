"""Turn a probe outcome into the persisted result once identity is known.

The probe never depends on ``analysis_id``. After the effective (detected)
language has produced the vision config hash and ``analysis_id``, this module
mints the evidence and frame IDs, saves the establishing frame, and writes
``game_language.json``. Unresolved outcomes are written without IDs.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

from splatoon3_ai_coach.config.models import AppConfig, VisionLanguage
from splatoon3_ai_coach.vision.game_language import (
    GameLanguageDiagnostics,
    GameLanguageProbeOutcome,
)
from splatoon3_ai_coach.vision.ids import make_frame_id, make_result_id
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    GameLanguageResult,
    SourceFrameReference,
)
from splatoon3_ai_coach.vision.provenance import detector_version

GAME_LANGUAGE_FILENAME = "game_language.json"
GAME_LANGUAGE_DETECTOR = "game_language"
EVIDENCE_DIRNAME = "game_language"


def with_detected_language(config: AppConfig, language: str) -> AppConfig:
    """Copy ``config`` with the detected language as the detector language."""
    effective = config.model_copy(deep=True)
    effective.vision.language = VisionLanguage(language)
    return effective


def supplied_agreement(language: str, supplied: VisionLanguage | None) -> str:
    """Compare a legacy supplied language with the detected one (record only)."""
    if supplied is None:
        return "not_supplied"
    if language == "unknown":
        return "unresolved"
    return "agree" if supplied.value == language else "disagree"


def build_game_language_result(
    outcome: GameLanguageProbeOutcome,
    output_dir: Path,
    *,
    analysis_id: str | None,
    supplied_language: VisionLanguage | None,
) -> GameLanguageResult:
    """Build the compact result; evidence IDs exist only when resolved."""
    evidence, source_frame = _establishing_evidence(outcome, output_dir, analysis_id)
    agreement = supplied_agreement(outcome.language, supplied_language)
    if agreement == "disagree":
        assert supplied_language is not None
        logger.warning(
            "Supplied game language {} disagrees with detected {}; the detected "
            "language is used and the supplied value is recorded for comparison",
            supplied_language.value,
            outcome.language,
        )
    return GameLanguageResult(
        language=outcome.language,
        source=outcome.source,
        termination=outcome.termination,
        established_at=None if source_frame is None else source_frame.timestamp,
        evidence_ids=[] if evidence is None else [evidence.id],
        evidence=evidence,
        source_frame=source_frame,
        intro=outcome.intro,
        fallback=outcome.fallback,
        video_ended_before_resolution=outcome.termination == "video_ended"
        and not outcome.resolved,
        supplied_language=None if supplied_language is None else supplied_language.value,
        supplied_agreement=agreement,
        policy=outcome.policy,
    )


def _establishing_evidence(
    outcome: GameLanguageProbeOutcome,
    output_dir: Path,
    analysis_id: str | None,
) -> tuple[DetectorResult | None, SourceFrameReference | None]:
    """Mint the establishing DetectorResult and frame reference."""
    frame = outcome.establishing_frame
    reading = outcome.establishing_reading
    if frame is None or reading is None or analysis_id is None:
        return None, None
    frame_path = _save_evidence_frame(frame.image, frame.frame_index, output_dir)
    source_frame = SourceFrameReference(
        frame_id=make_frame_id(analysis_id, frame.frame_index, frame.timestamp),
        source_frame_index=frame.frame_index,
        timestamp=frame.timestamp,
        source_pts=frame.source_pts,
        source_time_base_num=frame.source_time_base_num,
        source_time_base_den=frame.source_time_base_den,
        frame_path=frame_path,
    )
    version = detector_version(GAME_LANGUAGE_DETECTOR)
    evidence = DetectorResult(
        id=make_result_id(
            analysis_id,
            frame.frame_index,
            frame.timestamp,
            GAME_LANGUAGE_DETECTOR,
            version,
            reading,
        ),
        detector_name=GAME_LANGUAGE_DETECTOR,
        detector_version=version,
        confidence=max(0.0, min(1.0, reading.score)),
        reading=reading,
    )
    return evidence, source_frame


def _save_evidence_frame(image: np.ndarray, frame_index: int, output_dir: Path) -> str:
    """Write the establishing frame; return its path relative to ``output_dir``."""
    relative = Path(EVIDENCE_DIRNAME) / f"evidence_{frame_index}.jpg"
    path = output_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return relative.as_posix()


def write_game_language_diagnostics(
    result: GameLanguageResult,
    outcome: GameLanguageProbeOutcome,
    output_dir: Path,
) -> Path:
    """Write ``game_language.json`` (result, probe cost, full attempt trace)."""
    diagnostics = GameLanguageDiagnostics(
        result=result,
        probe=outcome.stats,
        attempts=outcome.attempts,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / GAME_LANGUAGE_FILENAME
    path.write_text(
        json.dumps(diagnostics.model_dump(mode="json"), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return path


def load_game_language_diagnostics(output_dir: Path) -> GameLanguageDiagnostics:
    """Read ``game_language.json`` from an analysis directory."""
    path = output_dir / GAME_LANGUAGE_FILENAME
    return GameLanguageDiagnostics.model_validate_json(path.read_text(encoding="utf-8"))
