"""Bounded pre-scan probe that resolves the recording's game language once.

Runs before detectors are built (language-dependent detectors cannot recover
missed frames later, and the language is part of ``analysis_id``).

- Gate 1: intro battle-mode label, both language packs, on frames before the
  intro deadline and before the first ``in_match`` frame.
- Gate 2 (only when Gate 1 closed without acceptance): bottom-left D-pad
  signal label on eligible frames (``in_match`` with a usable timer read),
  bounded by observation count and live seconds.

The first accepted observation establishes the language and decoding stops.
Nothing defaults to a language: unresolved means ``unknown``. Contract:
``vision/GAME_LANGUAGE.md``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

from loguru import logger
from pydantic import BaseModel, Field

from splatoon3_ai_coach.config.models import VisionConfig, VisionLanguage
from splatoon3_ai_coach.extraction.sampler import FrameSampler
from splatoon3_ai_coach.media.video import VideoFrame, VideoLoader
from splatoon3_ai_coach.vision.game_language_matcher import (
    LocalizedCueMatcher,
    load_callout_templates,
    load_intro_mode_templates,
)
from splatoon3_ai_coach.vision.match_phase import MatchPhaseFuser
from splatoon3_ai_coach.vision.models import (
    GameLanguageCueReading,
    GameLanguageFallbackSummary,
    GameLanguageGate,
    GameLanguageGateSummary,
    GameLanguageIntroSummary,
    GameLanguagePolicy,
    GameLanguageResult,
)
from splatoon3_ai_coach.vision.timer import TimerDetector


class GameLanguageAttempt(BaseModel):
    """One evaluated probe frame (diagnostics trace only)."""

    gate: GameLanguageGate
    timestamp: float = Field(ge=0)
    source_frame_index: int | None = None
    match_phase: str
    timer_seconds: float | None = None
    reading: GameLanguageCueReading


class GameLanguageProbeStats(BaseModel):
    """Cost and extent of one probe decode."""

    skip_intro: bool = False
    decoded_frame_count: int = Field(default=0, ge=0)
    sampled_frame_count: int = Field(default=0, ge=0)
    decode_seconds: float = Field(default=0.0, ge=0)
    probe_seconds: float = Field(default=0.0, ge=0)
    last_sampled_time: float | None = None
    video_duration_seconds: float | None = None


class GameLanguageDiagnostics(BaseModel):
    """``game_language.json``: the result plus the full per-frame attempt trace."""

    schema_version: int = 1
    result: GameLanguageResult
    probe: GameLanguageProbeStats
    attempts: list[GameLanguageAttempt] = Field(default_factory=list)


@dataclass
class GameLanguageProbeOutcome:
    """Probe output before identity exists (no analysis or result IDs yet)."""

    language: str
    termination: str
    intro: GameLanguageIntroSummary
    fallback: GameLanguageFallbackSummary
    policy: GameLanguagePolicy
    stats: GameLanguageProbeStats = field(default_factory=GameLanguageProbeStats)
    attempts: list[GameLanguageAttempt] = field(default_factory=list)
    establishing_frame: VideoFrame | None = None
    establishing_reading: GameLanguageCueReading | None = None

    @property
    def source(self) -> str:
        """Which gate established the language (``none`` when unknown)."""
        if self.establishing_reading is None:
            return "none"
        return self.establishing_reading.gate

    @property
    def resolved(self) -> bool:
        """True when a supported language was established."""
        return self.language != "unknown"


def build_policy(vision: VisionConfig) -> GameLanguagePolicy:
    """Effective thresholds and bounds for this config."""
    lang = vision.game_language
    return GameLanguagePolicy(
        intro_roi=tuple(vision.match_intro.battle_mode_roi),
        intro_threshold=lang.intro_threshold,
        intro_language_margin=lang.intro_language_margin,
        intro_deadline_seconds=vision.match_intro.intro_deadline_seconds,
        callout_roi=tuple(lang.roi),
        callout_cues=list(lang.cues),
        callout_threshold=lang.threshold,
        callout_language_margin=lang.language_margin,
        max_fallback_observations=lang.max_fallback_observations,
        max_fallback_live_seconds=lang.max_fallback_live_seconds,
        max_seconds_before_live=lang.max_seconds_before_live,
        sample_fps=vision.hud_cadence_fps,
        languages=[item.value for item in VisionLanguage],
    )


class GameLanguageProbe:
    """Step sampled frames through Gate 1 then Gate 2 until resolved or bounded."""

    def __init__(self, vision: VisionConfig, *, skip_intro: bool = False) -> None:
        languages = [item.value for item in VisionLanguage]
        lang = vision.game_language
        self.vision = vision
        self.skip_intro = skip_intro
        self.intro_matcher = LocalizedCueMatcher(
            "intro_mode",
            load_intro_mode_templates(vision.match_intro, languages),
            roi=vision.match_intro.battle_mode_roi,
            threshold=lang.intro_threshold,
            margin=lang.intro_language_margin,
            reference_height=lang.reference_height,
        )
        self.callout_matcher = LocalizedCueMatcher(
            "gameplay_callout",
            load_callout_templates(lang, languages),
            roi=lang.roi,
            threshold=lang.threshold,
            margin=lang.language_margin,
            reference_height=lang.reference_height,
        )
        self.timer = TimerDetector(vision.timer, cadence_fps=vision.hud_cadence_fps)
        self.fuser = MatchPhaseFuser(vision.lifecycle)
        self.outcome = GameLanguageProbeOutcome(
            language="unknown",
            termination="video_ended",
            intro=GameLanguageIntroSummary(attempted=not skip_intro),
            fallback=GameLanguageFallbackSummary(),
            policy=build_policy(vision),
            stats=GameLanguageProbeStats(skip_intro=skip_intro),
        )
        self._intro_open = not skip_intro
        self._done = False
        if skip_intro:
            self._close_intro("disabled", None)

    @property
    def done(self) -> bool:
        """True once the language is established or the fallback bound hit."""
        return self._done

    def step(self, frame: VideoFrame) -> bool:
        """Observe one sampled frame; return True when the probe is finished."""
        if self._done:
            return True
        self.outcome.stats.sampled_frame_count += 1
        self.outcome.stats.last_sampled_time = frame.timestamp
        seconds = self._timer_seconds(frame)
        phase = self.fuser.step(frame.timestamp, seconds)
        if self._intro_open:
            reason = self._intro_close_reason(frame.timestamp, phase)
            if reason is None:
                self._observe("intro_mode", frame, phase, seconds)
                return self._done
            self._close_intro(reason, frame.timestamp)
        if phase == "in_match" and seconds is not None:
            self._fallback_step(frame, phase, seconds)
        elif self._no_live_frames(frame.timestamp):
            self._stop("no_live_frames")
        return self._done

    def finish(self) -> GameLanguageProbeOutcome:
        """Close open gates at end of video and return the outcome."""
        if not self._done:
            if self._intro_open:
                self._close_intro("video_ended", self.outcome.stats.last_sampled_time)
            self.outcome.termination = "video_ended"
        _finalize_status(self.outcome.intro, self.outcome.intro.close_reason)
        fallback_state = "accepted" if self.outcome.source == "gameplay_callout" else None
        _finalize_status(self.outcome.fallback, fallback_state)
        return self.outcome

    def _timer_seconds(self, frame: VideoFrame) -> float | None:
        """Usable timer seconds, or None.

        A read above the longest battle clock (``opening_clock_seconds``) is a
        misread (lobby / Salmon Run digits read as 11:11 or 59:xx) and must not
        close Gate 1 or start the fallback bounds.
        """
        reading, confidence = self.timer.detect(frame.image, frame.timestamp)
        if reading is None or confidence < self.vision.timer.min_usable_confidence:
            return None
        if reading.seconds_remaining > max(self.vision.lifecycle.opening_clock_seconds):
            return None
        return float(reading.seconds_remaining)

    def _intro_close_reason(self, timestamp: float, phase: str) -> str | None:
        """Gate 1 closes at the first in-match frame or after the deadline."""
        if phase == "in_match":
            return "in_match"
        if timestamp > self.vision.match_intro.intro_deadline_seconds:
            return "deadline"
        return None

    def _close_intro(self, reason: str, timestamp: float | None) -> None:
        """Stop Gate 1 and enter the bounded Gate 2 fallback."""
        self._intro_open = False
        self.outcome.intro.close_reason = reason
        fallback = self.outcome.fallback
        fallback.entered = True
        fallback.attempted = True
        fallback.entered_at = timestamp
        logger.info(
            "Game language: intro gate closed ({}) at {}; callout fallback entered",
            reason,
            "start" if timestamp is None else f"{timestamp:.1f}s",
        )

    def _fallback_step(self, frame: VideoFrame, phase: str, seconds: float) -> None:
        """Observe one eligible in-match frame unless the bound is reached."""
        bound = self._fallback_bound(frame.timestamp)
        if bound is not None:
            self._stop(bound)
            return
        self._observe("gameplay_callout", frame, phase, seconds)

    def _no_live_frames(self, timestamp: float) -> bool:
        """True when no eligible frame appeared within ``max_seconds_before_live``."""
        return (
            self.outcome.fallback.first_time is None
            and timestamp > self.outcome.policy.max_seconds_before_live
        )

    def _stop(self, bound: str) -> None:
        """End the probe unresolved because a fallback bound was reached."""
        self.outcome.fallback.bound_reached = bound
        self.outcome.termination = "fallback_limit"
        self._done = True
        logger.info("Game language: fallback bound reached ({})", bound)

    def _fallback_bound(self, timestamp: float) -> str | None:
        """Which fallback bound (if any) stops evaluation at ``timestamp``."""
        fallback = self.outcome.fallback
        policy = self.outcome.policy
        if fallback.observations >= policy.max_fallback_observations:
            return "observations"
        first = fallback.first_time
        if first is not None and timestamp - first > policy.max_fallback_live_seconds:
            return "live_seconds"
        return None

    def _observe(
        self,
        gate: GameLanguageGate,
        frame: VideoFrame,
        phase: str,
        seconds: float | None,
    ) -> None:
        """Run one gate's matcher, record the attempt, establish on acceptance."""
        matcher = self.intro_matcher if gate == "intro_mode" else self.callout_matcher
        reading = matcher.observe(frame.image)
        summary = self.outcome.intro if gate == "intro_mode" else self.outcome.fallback
        _record(summary, reading, frame.timestamp)
        self.outcome.attempts.append(
            GameLanguageAttempt(
                gate=gate,
                timestamp=frame.timestamp,
                source_frame_index=frame.frame_index,
                match_phase=phase,
                timer_seconds=seconds,
                reading=reading,
            )
        )
        if reading.accepted:
            self._establish(gate, frame, reading)

    def _establish(
        self,
        gate: GameLanguageGate,
        frame: VideoFrame,
        reading: GameLanguageCueReading,
    ) -> None:
        """Latch the first accepted observation and stop the probe."""
        assert reading.candidate_language is not None
        self.outcome.language = reading.candidate_language
        self.outcome.establishing_frame = frame
        self.outcome.establishing_reading = reading
        if gate == "intro_mode":
            self.outcome.termination = "intro_accepted"
            self.outcome.intro.close_reason = "accepted"
        else:
            self.outcome.termination = "callout_accepted"
        self._done = True
        logger.info(
            "Game language {} established at {:.1f}s by {} ({} score={:.3f} "
            "runner_up={})",
            reading.candidate_language,
            frame.timestamp,
            gate,
            reading.cue_id,
            reading.score,
            reading.runner_up_score,
        )


def _record(
    summary: GameLanguageGateSummary,
    reading: GameLanguageCueReading,
    timestamp: float,
) -> None:
    """Fold one reading into a gate summary's counts and time span."""
    summary.observations += 1
    if summary.first_time is None:
        summary.first_time = timestamp
    summary.last_time = timestamp
    summary.span_seconds = round(timestamp - summary.first_time, 6)
    if reading.rejection == "below_threshold":
        summary.below_threshold += 1
    elif reading.rejection == "ambiguous_margin":
        summary.ambiguous_margin += 1
    elif reading.rejection == "no_templates":
        summary.no_templates += 1
    if reading.rejection != "no_templates" and (
        summary.best_score is None or reading.score > summary.best_score
    ):
        summary.best_score = reading.score


def _finalize_status(summary: GameLanguageGateSummary, accepted: str | None) -> None:
    """Set the closed-vocabulary gate status from its counts."""
    if accepted == "accepted":
        summary.status = "accepted"
    elif not summary.attempted:
        summary.status = "not_attempted"
    elif summary.ambiguous_margin > 0:
        summary.status = "ambiguous"
    else:
        summary.status = "unreadable"


def probe_game_language(
    video: Path,
    vision: VisionConfig,
    *,
    max_width: int,
    max_height: int,
    skip_intro: bool = False,
) -> GameLanguageProbeOutcome:
    """Decode from t=0 at the cadence rate until the probe resolves or stops.

    Uses the same loader limits and ``FrameSampler`` step as the main scan,
    so sampled frame indices match its frame keys.
    """
    started = perf_counter()
    probe = GameLanguageProbe(vision, skip_intro=skip_intro)
    sampler = FrameSampler(1.0 / vision.hud_cadence_fps)
    with VideoLoader(video, max_width=max_width, max_height=max_height) as loader:
        meta = loader.open()
        for frame in sampler.sample(loader.frames()):
            if probe.step(frame):
                break
        outcome = probe.finish()
        outcome.stats.decode_seconds = loader.decode_seconds
        outcome.stats.decoded_frame_count = loader.decoded_frame_count
    outcome.stats.video_duration_seconds = meta.duration_seconds
    outcome.stats.probe_seconds = perf_counter() - started
    return outcome


def observe_game_language_stream(
    frames: Iterator[VideoFrame],
    vision: VisionConfig,
    *,
    skip_intro: bool = False,
) -> GameLanguageProbeOutcome:
    """Run the probe over an already-decoded frame iterator (tests, tools)."""
    probe = GameLanguageProbe(vision, skip_intro=skip_intro)
    sampler = FrameSampler(1.0 / vision.hud_cadence_fps)
    for frame in sampler.sample(frames):
        if probe.step(frame):
            break
    return probe.finish()
