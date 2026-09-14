"""Labeled video-time samples for game clock and player-count channels."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, Field

from splatoon3_ai_coach.analysis.scenario_context import ScenarioContext
from splatoon3_ai_coach.analysis.scenario_models import Scenario
from splatoon3_ai_coach.coach.game_clock import GameClock, GameClockObservation
from splatoon3_ai_coach.coach.player_count_clock import (
    PlayerCountClock,
    PlayerCountObservation,
)


class GameClockSample(BaseModel):
    """Observed (or missing) match countdown at a labeled video time."""

    label: str
    video_time: float = Field(ge=0)
    observation: GameClockObservation | None = None
    gap_seconds: float | None = None


class PlayerCountSample(BaseModel):
    """Observed (or missing) fused roster alive counts at a labeled video time."""

    label: str
    video_time: float = Field(ge=0)
    observation: PlayerCountObservation | None = None
    gap_seconds: float | None = None


def attach_game_clock_to_event_times(
    video_times: Iterable[float],
    clock: GameClock,
    *,
    max_gap_seconds: float,
) -> dict[float, GameClockObservation | None]:
    """Map each canonical video time to a nearby observed game-clock sample."""
    return {
        float(video_time): clock.at(float(video_time), max_gap_seconds=max_gap_seconds)
        for video_time in video_times
    }


def labeled_video_times(
    scenario: Scenario,
    context: ScenarioContext,
) -> list[tuple[str, float]]:
    """Labeled primary scenario key video times (shared by clock channels)."""
    labeled: list[tuple[str, float]] = [
        ("primary_start", scenario.start_time),
        ("primary_end", scenario.end_time),
    ]
    death = context.death_episode
    if death is not None:
        if death.death_time is not None:
            labeled.append(("death", death.death_time))
        if death.respawn_time is not None:
            labeled.append(("respawn", death.respawn_time))
        if death.active_again_time is not None:
            labeled.append(("active_again", death.active_again_time))
    combat = context.combat
    if combat is not None:
        if combat.first_splat_time is not None:
            labeled.append(("first_splat", combat.first_splat_time))
        if combat.last_splat_time is not None:
            labeled.append(("last_splat", combat.last_splat_time))
    return labeled


def game_clock_samples(
    labeled: list[tuple[str, float]],
    game_clock: GameClock,
    *,
    max_gap_seconds: float,
) -> list[GameClockSample]:
    """Labeled lookups for primary scenario key video times."""
    samples: list[GameClockSample] = []
    seen: set[tuple[str, float]] = set()
    for label, video_time in labeled:
        key = (label, video_time)
        if key in seen:
            continue
        seen.add(key)
        obs = game_clock.at(video_time, max_gap_seconds=max_gap_seconds)
        gap = None if obs is None else abs(obs.video_time - video_time)
        samples.append(
            GameClockSample(
                label=label,
                video_time=video_time,
                observation=obs,
                gap_seconds=gap,
            )
        )
    return samples


def player_count_samples(
    labeled: list[tuple[str, float]],
    player_count_clock: PlayerCountClock,
    *,
    max_gap_seconds: float,
) -> list[PlayerCountSample]:
    """Labeled roster-count lookups at the same times as game-clock samples."""
    samples: list[PlayerCountSample] = []
    seen: set[tuple[str, float]] = set()
    for label, video_time in labeled:
        key = (label, video_time)
        if key in seen:
            continue
        seen.add(key)
        obs = player_count_clock.at(video_time, max_gap_seconds=max_gap_seconds)
        gap = None if obs is None else abs(obs.video_time - video_time)
        samples.append(
            PlayerCountSample(
                label=label,
                video_time=video_time,
                observation=obs,
                gap_seconds=gap,
            )
        )
    return samples
