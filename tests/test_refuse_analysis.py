"""Re-fusing an analysis from stored frame readings (no video decode)."""

from __future__ import annotations

import json
from pathlib import Path

from splatoon3_ai_coach.analysis.pipeline import (
    SCENARIO_CONTEXTS_JSON_FILENAME,
    SCENARIOS_JSON_FILENAME,
    refuse_analysis,
)
from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import AppConfig, VisionLanguage
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.media.vision_manifest import (
    hash_vision_config,
    load_vision_manifest,
    save_vision_manifest,
)
from splatoon3_ai_coach.vision.models import (
    AnalysisIdentity,
    DetectorResult,
    PlayerCountReading,
    VisionFrameResult,
    VisionManifest,
)
from splatoon3_ai_coach.vision.pipeline import refuse_vision_manifest


def _frame(timestamp: float, ally_dead: int) -> VisionFrameResult:
    reading = PlayerCountReading(
        ally_dead_slots=tuple(range(1, ally_dead + 1)), opponent_dead_slots=()
    )
    return VisionFrameResult(
        frame_id=f"f{timestamp}",
        timestamp=timestamp,
        source="cadence",
        detections=[
            DetectorResult(
                id=f"player_count:{timestamp}",
                detector_name="player_count",
                detector_version="player_count@test",
                confidence=0.9,
                reading=reading,
            )
        ],
    )


def _manifest() -> VisionManifest:
    frames = [_frame(0.0, 0), _frame(0.5, 2), _frame(1.0, 0)]
    return VisionManifest(
        analysis=AnalysisIdentity(
            analysis_id="a1",
            package_version="test",
            vision_config_sha256="original",
            video_identity="v1",
            language="ja",
        ),
        video_identity="v1",
        frame_results=frames,
    )


def _config(confirm_readings: int = 2) -> AppConfig:
    config = load_config(default_config_path())
    config.vision.player_count.confirm_readings = confirm_readings
    return config


def test_refuse_vision_manifest_applies_fusion_settings_and_keeps_provenance() -> None:
    config = _config()
    refused = refuse_vision_manifest(_manifest(), config)
    assert [s.ally_alive_count for s in refused.state_snapshots] == [4, 4, 4]
    assert refused.analysis.analysis_id == "a1"
    assert refused.analysis.vision_config_sha256 == "original"
    assert refused.analysis.refused_vision_config_sha256 == hash_vision_config(
        config.vision
    )
    assert refused.frame_results == _manifest().frame_results


def test_refuse_without_debounce_reproduces_raw_readings() -> None:
    refused = refuse_vision_manifest(_manifest(), _config(confirm_readings=1))
    assert [s.ally_alive_count for s in refused.state_snapshots] == [4, 2, 4]


def test_refuse_analysis_rewrites_manifest_and_scenarios(tmp_path: Path) -> None:
    save_vision_manifest(_manifest(), tmp_path)
    config = _config()
    refuse_analysis(tmp_path, config)
    stored = load_vision_manifest(tmp_path)
    assert [s.ally_alive_count for s in stored.state_snapshots] == [4, 4, 4]
    assert json.loads((tmp_path / SCENARIOS_JSON_FILENAME).read_text()) == []
    assert (tmp_path / SCENARIO_CONTEXTS_JSON_FILENAME).is_file()
    ja = config.model_copy(deep=True)
    ja.vision.language = VisionLanguage.JA
    assert stored.analysis.refused_vision_config_sha256 == hash_vision_config(ja.vision)
    assert config.vision.language.value == "en"
