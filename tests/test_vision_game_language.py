"""Pre-scan game-language probe: gates, bounds, provenance, failure boundary.

Cue fixtures are real ROI crops from permitted local recordings (see
``tests/fixtures/game_language/README.md``). Each is pasted back into a
1920x1080 canvas at its ROI so the production matcher sees it unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
import pytest
from game_language_helpers import injected_outcome, unknown_outcome
from typer.testing import CliRunner

from splatoon3_ai_coach.cli.analyze import GAME_LANGUAGE_UNDETERMINED_EXIT_CODE
from splatoon3_ai_coach.cli.app import app
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig, VisionConfig, VisionLanguage
from splatoon3_ai_coach.exceptions import (
    GAME_LANGUAGE_UNDETERMINED_MESSAGE,
    GameLanguageUndeterminedError,
)
from splatoon3_ai_coach.media.video import VideoFrame
from splatoon3_ai_coach.media.vision_manifest import VISION_MANIFEST_FILENAME
from splatoon3_ai_coach.types import NormalizedBox
from splatoon3_ai_coach.vision import pipeline as vision_pipeline
from splatoon3_ai_coach.vision.game_language import GameLanguageProbe
from splatoon3_ai_coach.vision.game_language_matcher import (
    LocalizedCueMatcher,
    LocalizedTemplate,
    load_callout_templates,
)
from splatoon3_ai_coach.vision.game_language_result import (
    GAME_LANGUAGE_FILENAME,
    build_game_language_result,
    load_game_language_diagnostics,
    write_game_language_diagnostics,
)
from splatoon3_ai_coach.vision.ids import parse_result_id
from splatoon3_ai_coach.vision.match_intro import BATTLE_MODE_IDS
from splatoon3_ai_coach.vision.models import TimerReading
from splatoon3_ai_coach.vision.roi import pixel_box_from_normalized

FIXTURES = Path(__file__).parent / "fixtures" / "game_language"
HEIGHT, WIDTH = 1080, 1920


@pytest.fixture(scope="module")
def config() -> AppConfig:
    return load_config(default_config_path())


def _canvas_with(name: str | None, roi: NormalizedBox) -> np.ndarray:
    """A black 1080p frame with fixture ``name`` pasted at ``roi``."""
    image = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    if name is None:
        return image
    crop = cv2.imread(str(FIXTURES / name))
    assert crop is not None, name
    left, top, right, bottom = pixel_box_from_normalized(roi, WIDTH, HEIGHT)
    if crop.shape[:2] != (bottom - top, right - left):
        crop = cv2.resize(crop, (right - left, bottom - top))
    image[top:bottom, left:right] = crop
    return image


def _intro(vision: VisionConfig, name: str | None) -> np.ndarray:
    return _canvas_with(name, vision.match_intro.battle_mode_roi)


def _callout(vision: VisionConfig, name: str | None) -> np.ndarray:
    return _canvas_with(name, vision.game_language.roi)


class _ScriptedTimer:
    """Timer stand-in: ``clock(t)`` returns remaining seconds or None."""

    def __init__(self, clock: Callable[[float], float | None]) -> None:
        self.clock = clock

    def detect(
        self, image: np.ndarray, timestamp: float | None = None
    ) -> tuple[TimerReading | None, float]:
        _ = image
        seconds = self.clock(float(timestamp or 0.0))
        if seconds is None:
            return None, 0.0
        return TimerReading(display="x", seconds_remaining=seconds), 0.95


def _probe(
    vision: VisionConfig,
    clock: Callable[[float], float | None],
    *,
    skip_intro: bool = False,
) -> GameLanguageProbe:
    probe = GameLanguageProbe(vision, skip_intro=skip_intro)
    probe.timer = _ScriptedTimer(clock)
    return probe


def _frame(t: float, image: np.ndarray) -> VideoFrame:
    return VideoFrame(timestamp=t, frame_index=int(round(t * 60)), image=image)


def _in_match_after(start: float) -> Callable[[float], float | None]:
    return lambda t: None if t < start else 180.0 - (t - start) - 1.0


@pytest.mark.parametrize("language", ["en", "ja"])
@pytest.mark.parametrize("mode", BATTLE_MODE_IDS)
def test_intro_mode_label_is_accepted_for_every_mode(
    config: AppConfig, language: str, mode: str
) -> None:
    probe = GameLanguageProbe(config.vision)
    reading = probe.intro_matcher.observe(
        _intro(config.vision, f"intro_{language}_{mode}.png")
    )
    assert reading.accepted, reading
    assert reading.candidate_language == language
    assert reading.cue_id == f"battle_mode/{mode}"
    assert reading.template_path.startswith(
        f"calibration/templates/match_intro/{language}/battle_modes/"
    )
    assert reading.runner_up_language != language


@pytest.mark.parametrize("language", ["en", "ja"])
def test_callout_is_accepted_in_both_languages(config: AppConfig, language: str) -> None:
    probe = GameLanguageProbe(config.vision)
    reading = probe.callout_matcher.observe(
        _callout(config.vision, f"callout_{language}_come_on.png")
    )
    assert reading.accepted, reading
    assert reading.candidate_language == language
    assert reading.cue_id == "callout/come_on"
    assert reading.template_path == f"calibration/templates/lang/{language}/come_on.png"


def test_intro_success_stops_scheduling(config: AppConfig) -> None:
    probe = _probe(config.vision, lambda _t: None)
    assert probe.step(_frame(0.0, _intro(config.vision, None))) is False
    assert probe.step(_frame(0.5, _intro(config.vision, "intro_ja_turf_war.png")))
    assert probe.step(_frame(1.0, _intro(config.vision, "intro_en_turf_war.png")))
    outcome = probe.finish()
    assert outcome.language == "ja"
    assert outcome.source == "intro_mode"
    assert outcome.termination == "intro_accepted"
    assert len(outcome.attempts) == 2
    assert outcome.intro.status == "accepted"
    assert outcome.fallback.entered is False
    assert outcome.fallback.status == "not_attempted"


def test_missing_intro_falls_back_to_callout(config: AppConfig) -> None:
    probe = _probe(config.vision, _in_match_after(2.0))
    for t in (0.0, 0.5, 1.0, 1.5):
        assert probe.step(_frame(t, _intro(config.vision, None))) is False
    assert probe.step(_frame(2.0, _callout(config.vision, "callout_en_come_on.png")))
    outcome = probe.finish()
    assert outcome.language == "en"
    assert outcome.source == "gameplay_callout"
    assert outcome.termination == "callout_accepted"
    assert outcome.intro.close_reason == "in_match"
    assert outcome.intro.status == "unreadable"
    assert outcome.intro.observations == 4
    assert outcome.fallback.entered_at == 2.0
    assert outcome.fallback.observations == 1
    assert outcome.fallback.status == "accepted"


def test_fallback_success_stops_scheduling(config: AppConfig) -> None:
    probe = _probe(config.vision, lambda _t: 170.0, skip_intro=True)
    assert probe.step(_frame(0.0, _callout(config.vision, None))) is False
    assert probe.step(_frame(0.5, _callout(config.vision, "callout_ja_come_on.png")))
    assert probe.step(_frame(1.0, _callout(config.vision, "callout_en_come_on.png")))
    outcome = probe.finish()
    assert outcome.language == "ja"
    assert outcome.intro.status == "not_attempted"
    assert outcome.intro.close_reason == "disabled"
    assert [a.gate for a in outcome.attempts] == ["gameplay_callout"] * 2


def test_missing_callout_never_implies_a_language(config: AppConfig) -> None:
    vision = config.vision.model_copy(deep=True)
    vision.game_language.max_fallback_observations = 4
    probe = _probe(vision, lambda _t: 150.0, skip_intro=True)
    for i in range(10):
        if probe.step(_frame(i * 0.5, _callout(vision, None))):
            break
    outcome = probe.finish()
    assert outcome.language == "unknown"
    assert outcome.termination == "fallback_limit"
    assert outcome.fallback.bound_reached == "observations"
    assert outcome.fallback.observations == 4
    assert outcome.fallback.status == "unreadable"
    assert not any(a.reading.accepted for a in outcome.attempts)


def test_one_language_pack_does_not_accept_the_other_languages_cue(
    config: AppConfig,
) -> None:
    ja_only = load_callout_templates(config.vision.game_language, ["ja"])
    matcher = LocalizedCueMatcher(
        "gameplay_callout",
        ja_only,
        roi=config.vision.game_language.roi,
        threshold=config.vision.game_language.threshold,
        margin=config.vision.game_language.language_margin,
        reference_height=1080,
    )
    reading = matcher.observe(_callout(config.vision, "callout_en_come_on.png"))
    assert reading.accepted is False
    assert reading.rejection == "below_threshold"


def test_unreadable_callout_stays_unknown(config: AppConfig) -> None:
    vision = config.vision.model_copy(deep=True)
    vision.game_language.max_fallback_observations = 3
    blurred = cv2.GaussianBlur(_callout(vision, "callout_ja_come_on.png"), (0, 0), 9)
    probe = _probe(vision, lambda _t: 150.0, skip_intro=True)
    for i in range(6):
        if probe.step(_frame(i * 0.5, blurred)):
            break
    outcome = probe.finish()
    assert outcome.language == "unknown"
    assert outcome.fallback.below_threshold == 3
    assert outcome.fallback.status == "unreadable"


@pytest.mark.parametrize(
    "name",
    ["callout_en_dead.png", "callout_ja_dead.png", "callout_fr_in_match.png"],
)
def test_death_text_and_other_languages_are_not_a_cue(
    config: AppConfig, name: str
) -> None:
    probe = GameLanguageProbe(config.vision, skip_intro=True)
    reading = probe.callout_matcher.observe(_callout(config.vision, name))
    assert reading.accepted is False
    assert reading.rejection == "below_threshold"
    assert reading.score < config.vision.game_language.threshold - 0.3


def test_live_seconds_bound_terminates_the_fallback(config: AppConfig) -> None:
    vision = config.vision.model_copy(deep=True)
    vision.game_language.max_fallback_observations = 1000
    vision.game_language.max_fallback_live_seconds = 2.0
    probe = _probe(vision, _in_match_after(10.0), skip_intro=True)
    times = [t * 0.5 for t in range(40)]
    stopped_at = next(t for t in times if probe.step(_frame(t, _callout(vision, None))))
    outcome = probe.finish()
    assert stopped_at == 12.5
    assert outcome.fallback.bound_reached == "live_seconds"
    assert outcome.fallback.first_time == 10.0
    assert outcome.fallback.span_seconds == 2.0
    assert outcome.termination == "fallback_limit"


def test_no_live_frames_bound_stops_a_video_without_battle_clock(
    config: AppConfig,
) -> None:
    vision = config.vision.model_copy(deep=True)
    vision.match_intro.intro_deadline_seconds = 1.0
    vision.game_language.max_seconds_before_live = 3.0
    probe = _probe(vision, lambda _t: None)
    times = [t * 0.5 for t in range(40)]
    image = _callout(vision, "callout_en_come_on.png")
    stopped_at = next(t for t in times if probe.step(_frame(t, image)))
    outcome = probe.finish()
    assert stopped_at == 3.5
    assert outcome.language == "unknown"
    assert outcome.termination == "fallback_limit"
    assert outcome.fallback.bound_reached == "no_live_frames"
    assert outcome.fallback.entered is True
    assert outcome.fallback.observations == 0


def test_impossible_clock_read_does_not_close_the_intro_gate(
    config: AppConfig,
) -> None:
    probe = _probe(config.vision, lambda t: 671.0 if t == 2.5 else None)
    assert not probe.step(_frame(2.5, _intro(config.vision, None)))
    assert probe.outcome.intro.close_reason is None
    assert probe.outcome.fallback.first_time is None
    assert probe.step(_frame(3.0, _intro(config.vision, "intro_ja_tower_control.png")))
    assert probe.finish().termination == "intro_accepted"


def test_video_end_before_resolution_is_reported(config: AppConfig) -> None:
    vision = config.vision.model_copy(deep=True)
    vision.match_intro.intro_deadline_seconds = 1.0
    probe = _probe(vision, lambda _t: None)
    for t in (0.0, 0.5, 1.0, 1.5, 2.0):
        probe.step(_frame(t, _intro(vision, None)))
    outcome = probe.finish()
    result = build_game_language_result(outcome, Path("/nonexistent"), analysis_id=None)
    assert result.language == "unknown"
    assert result.termination == "video_ended"
    assert result.video_ended_before_resolution is True
    assert result.intro.close_reason == "deadline"
    assert result.intro.observations == 3
    assert result.fallback.entered is True
    assert result.fallback.observations == 0
    assert result.fallback.status == "unreadable"
    assert result.evidence is None and result.evidence_ids == []


def test_cross_language_near_tie_is_rejected_as_ambiguous(config: AppConfig) -> None:
    image = cv2.imread(str(FIXTURES / "callout_ja_come_on.png"), cv2.IMREAD_GRAYSCALE)
    tie = [
        LocalizedTemplate("en", "callout/come_on", "en.png", image[20:80, 40:160]),
        LocalizedTemplate("ja", "callout/come_on", "ja.png", image[20:80, 40:160]),
    ]
    matcher = LocalizedCueMatcher(
        "gameplay_callout",
        tie,
        roi=config.vision.game_language.roi,
        threshold=0.5,
        margin=0.05,
        reference_height=1080,
    )
    reading = matcher.observe(_callout(config.vision, "callout_ja_come_on.png"))
    assert reading.score >= 0.5
    assert reading.runner_up_score == pytest.approx(reading.score)
    assert reading.accepted is False
    assert reading.rejection == "ambiguous_margin"


def test_provenance_survives_a_json_round_trip(
    config: AppConfig, tmp_path: Path
) -> None:
    probe = _probe(config.vision, lambda _t: 150.0, skip_intro=True)
    probe.step(_frame(3.5, _callout(config.vision, "callout_ja_come_on.png")))
    outcome = probe.finish()
    result = build_game_language_result(outcome, tmp_path, analysis_id="aid")
    write_game_language_diagnostics(result, outcome, tmp_path)
    loaded = load_game_language_diagnostics(tmp_path)
    assert loaded.result == result
    assert loaded.attempts == outcome.attempts
    assert result.source_frame.frame_id == "aid:frame:210"
    assert (tmp_path / result.source_frame.frame_path).is_file()
    parts = parse_result_id(result.evidence_ids[0])
    assert parts is not None
    assert (parts.analysis_id, parts.frame_key, parts.detector_name) == (
        "aid",
        "210",
        "game_language",
    )
    assert result.evidence.reading.candidate_language == "ja"


def _patch_probe(monkeypatch: pytest.MonkeyPatch, outcome: object) -> None:
    monkeypatch.setattr(
        vision_pipeline, "probe_game_language", lambda *_a, **_k: outcome
    )


def test_unknown_language_stops_before_any_manifest(
    config: AppConfig, sample_video: Path, tmp_path: Path, monkeypatch
) -> None:
    _patch_probe(monkeypatch, unknown_outcome(config.vision))
    built: list[object] = []
    monkeypatch.setattr(vision_pipeline, "build_detectors", built.append)
    out = tmp_path / "analysis"
    with pytest.raises(GameLanguageUndeterminedError) as raised:
        vision_pipeline.run_vision(sample_video, config, out)
    assert str(raised.value) == GAME_LANGUAGE_UNDETERMINED_MESSAGE
    assert built == []
    assert not (out / VISION_MANIFEST_FILENAME).exists()
    assert not (out / "match_identity.json").exists()
    diagnostics = load_game_language_diagnostics(out)
    assert diagnostics.result.language == "unknown"
    assert diagnostics.result.fallback.bound_reached == "observations"
    assert diagnostics.result.fallback.status == "ambiguous"
    assert diagnostics.result.intro.status == "unreadable"


def test_cli_exits_with_the_language_code(
    config: AppConfig, sample_video: Path, tmp_path: Path, monkeypatch
) -> None:
    _patch_probe(monkeypatch, unknown_outcome(config.vision))
    out = tmp_path / "analysis"
    result = CliRunner().invoke(
        app, ["analyze", str(sample_video), "--out", str(out)]
    )
    assert result.exit_code == GAME_LANGUAGE_UNDETERMINED_EXIT_CODE == 4
    assert "Game language could not be determined" in result.output
    assert (out / GAME_LANGUAGE_FILENAME).is_file()
    assert not (out / VISION_MANIFEST_FILENAME).exists()


def test_analyze_has_no_language_option(sample_video: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["analyze", str(sample_video), "--out", str(tmp_path), "--language", "en"]
    )
    assert result.exit_code == 2
    assert "No such option" in result.output
    assert not (tmp_path / GAME_LANGUAGE_FILENAME).exists()


def _run_detected(
    config: AppConfig,
    sample_video: Path,
    out: Path,
    monkeypatch: pytest.MonkeyPatch,
    detected: str,
) -> tuple[list[VisionLanguage | None], object]:
    seen: list[VisionLanguage | None] = []

    def capture(vision: VisionConfig) -> list:
        seen.append(vision.language)
        return []

    monkeypatch.setattr(vision_pipeline, "build_detectors", capture)
    manifest = vision_pipeline.run_vision(
        sample_video,
        config,
        out,
        game_language=injected_outcome(detected, config.vision),
    )
    return seen, manifest


@pytest.mark.parametrize("detected", ["en", "ja"])
def test_detected_language_alone_selects_detectors_and_identity(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, detected: str
) -> None:
    config = load_config(default_config_path())
    assert config.vision.language is None
    seen, manifest = _run_detected(config, sample_video, tmp_path, monkeypatch, detected)
    assert seen == [VisionLanguage(detected)]
    assert manifest.analysis.language == detected
    assert manifest.game_language.language == detected
    assert config.vision.language is None


def test_en_and_ja_detections_have_distinct_identities(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(default_config_path())
    _, english = _run_detected(config, sample_video, tmp_path / "en", monkeypatch, "en")
    _, japanese = _run_detected(config, sample_video, tmp_path / "ja", monkeypatch, "ja")
    assert english.analysis.analysis_id != japanese.analysis.analysis_id


def test_a_preset_language_never_overrides_detection(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(default_config_path())
    _, unset = _run_detected(config, sample_video, tmp_path / "unset", monkeypatch, "ja")
    config.vision.language = VisionLanguage.EN
    seen, preset = _run_detected(
        config, sample_video, tmp_path / "preset", monkeypatch, "ja"
    )
    assert seen == [VisionLanguage.JA]
    assert preset.analysis.language == "ja"
    assert preset.analysis.analysis_id == unset.analysis.analysis_id


def test_unknown_language_runs_nothing_downstream_of_resolution(
    config: AppConfig, sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splatoon3_ai_coach.analysis import pipeline as analysis_pipeline

    _patch_probe(monkeypatch, unknown_outcome(config.vision))
    calls: list[str] = []

    def spy(name: str) -> Callable[..., object]:
        def record(*_a: object, **_k: object) -> object:
            calls.append(name)
            raise AssertionError(f"{name} ran after an undetermined game language")

        return record

    for module, name in (
        (vision_pipeline, "with_detected_language"),
        (vision_pipeline, "hash_vision_config"),
        (vision_pipeline, "compute_analysis_id"),
        (vision_pipeline, "build_detectors"),
        (vision_pipeline, "build_map_ink_context"),
        (vision_pipeline, "scan_video"),
        (vision_pipeline, "build_manifest"),
        (analysis_pipeline, "write_scenarios"),
    ):
        monkeypatch.setattr(module, name, spy(name))
    out = tmp_path / "analysis"
    with pytest.raises(GameLanguageUndeterminedError):
        analysis_pipeline.run_analysis(sample_video, config, out)
    assert calls == []
    assert sorted(p.name for p in out.iterdir()) == [GAME_LANGUAGE_FILENAME]


def test_phase1_artifacts_with_supplied_fields_still_load(
    config: AppConfig, tmp_path: Path
) -> None:
    import json

    from splatoon3_ai_coach.vision.models import GameLanguageResult

    probe = _probe(config.vision, lambda _t: 150.0, skip_intro=True)
    probe.step(_frame(3.5, _callout(config.vision, "callout_ja_come_on.png")))
    outcome = probe.finish()
    result = build_game_language_result(outcome, tmp_path, analysis_id="aid")
    write_game_language_diagnostics(result, outcome, tmp_path)
    path = tmp_path / GAME_LANGUAGE_FILENAME
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["result"]["supplied_language"] = "en"
    raw["result"]["supplied_agreement"] = "disagree"
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_game_language_diagnostics(tmp_path)
    assert loaded.result == result
    assert loaded.result.language == "ja"
    assert "supplied_language" not in loaded.result.model_dump()
    legacy = GameLanguageResult.model_validate(
        {
            **result.model_dump(mode="json"),
            "supplied_language": "en",
            "supplied_agreement": "agree",
        }
    )
    assert legacy == result


_IDENTITY = {
    "analysis_id": "aid",
    "package_version": "test",
    "vision_config_sha256": "sha",
    "video_identity": "vid",
}


def test_analysis_identity_never_defaults_the_game_language() -> None:
    from pydantic import ValidationError

    from splatoon3_ai_coach.vision.models import AnalysisIdentity

    with pytest.raises(ValidationError, match="language"):
        AnalysisIdentity(**_IDENTITY)
    with pytest.raises(ValidationError, match="language"):
        AnalysisIdentity(**_IDENTITY, language="fr")
    assert AnalysisIdentity(**_IDENTITY, language="ja").language == "ja"


@pytest.mark.parametrize("stored", ["en", "ja", None])
def test_stored_manifests_keep_their_language_and_identity(
    tmp_path: Path, stored: str | None
) -> None:
    import json

    from splatoon3_ai_coach.media.vision_manifest import load_vision_manifest

    analysis = dict(_IDENTITY) if stored is None else {**_IDENTITY, "language": stored}
    raw = {"analysis": analysis, "video_identity": "vid"}
    (tmp_path / VISION_MANIFEST_FILENAME).write_text(json.dumps(raw), encoding="utf-8")
    manifest = load_vision_manifest(tmp_path)
    # Only a manifest predating analysis.language reads as English.
    assert manifest.analysis.language == (stored or "en")
    assert manifest.analysis.analysis_id == "aid"
    assert manifest.analysis.vision_config_sha256 == "sha"
