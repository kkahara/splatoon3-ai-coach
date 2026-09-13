"""VideoSource resolution, observability, and VideoRunMetadata."""

from __future__ import annotations

import pytest

from splatoon3_ai_coach.analysis.scenario_context import build_scenario_context
from splatoon3_ai_coach.analysis.scenario_models import Scenario, ScenarioOutcome, ScenarioType
from splatoon3_ai_coach.coach.coach_input import collect_evidence_limits
from splatoon3_ai_coach.coach.evidence_contract import map_observation_statements
from splatoon3_ai_coach.config.models import ScenarioBuilderConfig
from splatoon3_ai_coach.media.source_capabilities import (
    absence_is_reliable_negative,
    map_overlay_observability,
)
from splatoon3_ai_coach.media.video_source import (
    Observability,
    UnsupportedVideoSourceError,
    VideoSource,
    analysis_frame_size,
    build_video_run_metadata,
    resolve_video_source,
)
from splatoon3_ai_coach.vision.models import GameEvent, GameEventReason, GameEventType


def test_resolve_default_screen_capture() -> None:
    source, how = resolve_video_source(declared=None, review_icon_detected=False)
    assert source is VideoSource.SCREEN_CAPTURE
    assert how == "default"


def test_resolve_detected_review_icon_upgrades() -> None:
    source, how = resolve_video_source(declared=None, review_icon_detected=True)
    assert source is VideoSource.REVIEW
    assert how == "detected_review_icon"


def test_resolve_declared_review_with_icon() -> None:
    source, how = resolve_video_source(
        declared=VideoSource.REVIEW, review_icon_detected=True
    )
    assert source is VideoSource.REVIEW
    assert how == "declared_with_review_icon"


def test_resolve_declared_review_without_icon() -> None:
    source, how = resolve_video_source(
        declared=VideoSource.REVIEW, review_icon_detected=False
    )
    assert source is VideoSource.REVIEW
    assert how == "declared"


def test_resolve_hand_capture_ok_without_icon() -> None:
    source, how = resolve_video_source(
        declared=VideoSource.HAND_CAPTURE, review_icon_detected=False
    )
    assert source is VideoSource.HAND_CAPTURE
    assert how == "declared"


def test_resolve_hand_capture_rejects_review_icon() -> None:
    with pytest.raises(UnsupportedVideoSourceError):
        resolve_video_source(
            declared=VideoSource.HAND_CAPTURE, review_icon_detected=True
        )


def test_analysis_frame_size_downscale_and_equal() -> None:
    assert analysis_frame_size(2560, 1440, max_width=1920, max_height=1080) == (
        1920,
        1080,
    )
    assert analysis_frame_size(1920, 1080, max_width=1920, max_height=1080) == (
        1920,
        1080,
    )
    assert analysis_frame_size(1280, 720, max_width=1920, max_height=1080) == (
        1280,
        720,
    )


def test_map_overlay_observability_table() -> None:
    assert map_overlay_observability(VideoSource.SCREEN_CAPTURE) is Observability.OBSERVABLE
    assert map_overlay_observability(VideoSource.REVIEW) is Observability.UNOBSERVABLE
    assert (
        map_overlay_observability(VideoSource.HAND_CAPTURE)
        is Observability.POTENTIALLY_OBSERVABLE
    )
    assert absence_is_reliable_negative(Observability.OBSERVABLE) is True
    assert absence_is_reliable_negative(Observability.UNOBSERVABLE) is False
    assert absence_is_reliable_negative(Observability.POTENTIALLY_OBSERVABLE) is False


def test_video_run_metadata_answers_manifest_questions() -> None:
    meta = build_video_run_metadata(
        declared=None,
        review_icon_detected=True,
        review_icon_video_time=1.2,
        review_icon_score=0.9,
        original_width=2560,
        original_height=1440,
        analysis_width=1920,
        analysis_height=1080,
    )
    assert meta.source is VideoSource.REVIEW
    assert meta.source_determination == "detected_review_icon"
    assert meta.review_icon_detected is True
    assert meta.original_width == 2560
    assert meta.analysis_width == 1920
    assert meta.map_overlay_observability is Observability.UNOBSERVABLE
    assert meta.analysis_quality is None


def _death_event(ts: float = 70.0) -> GameEvent:
    return GameEvent(
        start_time=ts,
        event_type=GameEventType.DEATH,
        reason=GameEventReason.ALIVE_TO_DEAD,
        confidence=1.0,
    )


def _death_scenario(start: float = 70.0) -> Scenario:
    return Scenario(
        scenario_id=f"death_episode:{start:.3f}",
        scenario_type=ScenarioType.DEATH_EPISODE,
        start_time=start,
        end_time=start + 10.0,
        event_ids=[f"death:{start:.3f}:alive_to_dead"],
        confidence=1.0,
        outcome=ScenarioOutcome.DIED,
    )


def test_review_map_check_absence_is_none_not_false() -> None:
    ctx = build_scenario_context(
        [_death_event()],
        _death_scenario(),
        ScenarioBuilderConfig(),
        map_overlay_observability=Observability.UNOBSERVABLE,
    )
    assert ctx.map is not None
    assert ctx.map.map_check_before_death is None
    assert ctx.map.map_checked_while_dead is None
    statements = map_observation_statements(ctx)
    assert not any("No map overlay was observed" in line for line in statements)
    limits = collect_evidence_limits(
        _death_scenario(),
        ctx,
        game_clock_samples=[],
    )
    assert any(item.code == "map_check_unobservable" for item in limits)


def test_hand_capture_map_check_absence_is_none() -> None:
    ctx = build_scenario_context(
        [_death_event()],
        _death_scenario(),
        ScenarioBuilderConfig(),
        map_overlay_observability=Observability.POTENTIALLY_OBSERVABLE,
    )
    assert ctx.map is not None
    assert ctx.map.map_check_before_death is None


def test_review_icon_tracker_matches_template() -> None:
    import cv2
    import numpy as np
    from pathlib import Path

    from splatoon3_ai_coach.config.models import ReviewIconConfig
    from splatoon3_ai_coach.vision.review_icon import ReviewIconTracker

    template_dir = Path(__file__).resolve().parents[1] / "calibration" / "templates" / "review"
    tmpl = cv2.imread(str(template_dir / "review-icon-01.png"))
    assert tmpl is not None
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    th, tw = tmpl.shape[:2]
    frame[40 : 40 + th, 40 : 40 + tw] = tmpl
    tracker = ReviewIconTracker(
        ReviewIconConfig(template_dir=template_dir, match_threshold=0.7)
    )
    hit = tracker.observe(frame, video_time=0.5)
    assert hit is not None
    assert hit.score >= 0.7
    ctx = build_scenario_context(
        [_death_event()],
        _death_scenario(),
        ScenarioBuilderConfig(),
        map_overlay_observability=Observability.OBSERVABLE,
    )
    assert ctx.map is not None
    assert ctx.map.map_check_before_death is False
    statements = map_observation_statements(ctx)
    assert any("No map overlay was observed" in line for line in statements)
