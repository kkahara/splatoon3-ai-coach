"""Probe outcomes for tests that run the pipeline on synthetic videos."""

from __future__ import annotations

import numpy as np

from splatoon3_ai_coach.config.models import VisionConfig
from splatoon3_ai_coach.media.video import VideoFrame
from splatoon3_ai_coach.vision.game_language import (
    GameLanguageProbeOutcome,
    build_policy,
)
from splatoon3_ai_coach.vision.models import (
    GameLanguageCueReading,
    GameLanguageFallbackSummary,
    GameLanguageIntroSummary,
)


def injected_outcome(language: str, vision: VisionConfig) -> GameLanguageProbeOutcome:
    """An intro-accepted outcome for ``language`` without decoding a video."""
    reading = GameLanguageCueReading(
        gate="intro_mode",
        candidate_language=language,
        cue_id="battle_mode/turf_war",
        template_path="tests/injected",
        score=0.99,
        runner_up_language="ja" if language == "en" else "en",
        runner_up_score=0.40,
        accepted=True,
    )
    return GameLanguageProbeOutcome(
        language=language,
        termination="intro_accepted",
        intro=GameLanguageIntroSummary(
            attempted=True,
            observations=1,
            first_time=0.0,
            last_time=0.0,
            best_score=0.99,
            status="accepted",
            close_reason="accepted",
        ),
        fallback=GameLanguageFallbackSummary(),
        policy=build_policy(vision),
        establishing_frame=VideoFrame(
            timestamp=0.0,
            frame_index=0,
            image=np.zeros((18, 32, 3), dtype=np.uint8),
        ),
        establishing_reading=reading,
    )


def unknown_outcome(vision: VisionConfig) -> GameLanguageProbeOutcome:
    """An outcome where the fallback bound stopped the probe unresolved."""
    return GameLanguageProbeOutcome(
        language="unknown",
        termination="fallback_limit",
        intro=GameLanguageIntroSummary(
            attempted=True,
            observations=4,
            first_time=0.0,
            last_time=1.5,
            span_seconds=1.5,
            below_threshold=4,
            best_score=0.31,
            status="unreadable",
            close_reason="in_match",
        ),
        fallback=GameLanguageFallbackSummary(
            attempted=True,
            entered=True,
            entered_at=2.0,
            observations=3,
            first_time=2.0,
            last_time=3.0,
            span_seconds=1.0,
            ambiguous_margin=1,
            below_threshold=2,
            best_score=0.72,
            status="ambiguous",
            bound_reached="observations",
        ),
        policy=build_policy(vision),
    )
