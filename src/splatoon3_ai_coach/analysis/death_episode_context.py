"""Death-episode Level-1 lifecycle timings and Level-2 factual enrichment.

Convenience lookups only — not coaching judgments.
"""

from __future__ import annotations

from collections.abc import Sequence

from splatoon3_ai_coach.analysis.player_count_series import (
    NumbersState,
    numbers_state,
)
from splatoon3_ai_coach.analysis.scenario_evidence import (
    PlayerCountPoint,
    PlayersEvidence,
)
from splatoon3_ai_coach.analysis.scenario_models import Scenario, ScenarioType
from splatoon3_ai_coach.config.models import ScenarioBuilderConfig
from splatoon3_ai_coach.vision.models import (
    GameEvent,
    GameEventType,
    GameStateSnapshot,
    MatchPhase,
)


def death_episode_context(events: list[GameEvent], scenario: Scenario):
    """Lifecycle timings for a death episode."""
    from splatoon3_ai_coach.analysis.scenario_context import (
        DeathEpisodeContext,
        _delta,
        _event_at,
        _first_between,
        _next_death_time,
    )

    if scenario.scenario_type is not ScenarioType.DEATH_EPISODE:
        return None
    death = _event_at(events, GameEventType.DEATH, scenario.start_time)
    if death is None:
        return DeathEpisodeContext()
    next_death = _next_death_time(events, death.start_time)
    respawn = _first_between(events, GameEventType.RESPAWN, death.start_time, next_death)
    active = _first_between(
        events, GameEventType.ACTIVE_AGAIN, death.start_time, next_death
    )
    reason = respawn.reason.value if respawn is not None and respawn.reason else None
    awaiting_end = (
        respawn.start_time
        if respawn is not None
        else (active.start_time if active is not None else None)
    )
    awaiting_start = death.start_time
    return DeathEpisodeContext(
        death_time=death.start_time,
        respawn_time=respawn.start_time if respawn is not None else None,
        active_again_time=active.start_time if active is not None else None,
        awaiting_start=awaiting_start,
        awaiting_end=awaiting_end,
        awaiting_duration=_delta(awaiting_end, awaiting_start),
        death_to_respawn=_delta(respawn, death),
        death_to_active_again=_delta(active, death),
        respawn_to_active_again=_delta(active, respawn),
        has_respawn=respawn is not None,
        has_active_again=active is not None,
        complete=active is not None,
        respawn_reason=reason,
    )


def enrich_death_episode_level2(
    death_episode,
    *,
    timeline,
    events: list[GameEvent],
    players: PlayersEvidence | None,
    snapshots: Sequence[GameStateSnapshot],
    config: ScenarioBuilderConfig,
):
    """Attach Level 2 convenience facts. Missing evidence stays ``None``."""
    death_time = death_episode.death_time
    is_first = timeline.time_since_previous_death is None
    splat_gap = None
    match_phase = None
    numbers = None
    roster_changed = None
    seconds_since_roster = None
    if death_time is not None:
        splat_gap = time_since_previous_splat(events, death_time)
        match_phase = nearest_match_phase(
            snapshots,
            death_time,
            max_gap_seconds=float(config.context_max_gap_seconds),
        )
        numbers, roster_changed, seconds_since_roster = roster_facts_at_death(
            players,
            death_time=death_time,
            lookback_seconds=float(config.context_lookback_seconds),
        )
    return death_episode.model_copy(
        update={
            "is_first_death": is_first,
            "time_since_previous_splat": splat_gap,
            "match_phase_at_death": match_phase,
            "numbers_state_at_death": numbers,
            "roster_changed_before_death": roster_changed,
            "seconds_since_roster_change": seconds_since_roster,
            # Default until attach_relations mirrors engagement trade.
            "preceded_by_trade_candidate": False,
        }
    )


def time_since_previous_splat(
    events: list[GameEvent], death_time: float
) -> float | None:
    """``death_time - last SPLAT`` strictly before death; else ``None``."""
    from splatoon3_ai_coach.analysis.scenario_context import _last_before, _of_type

    prior = _last_before(_of_type(events, GameEventType.SPLAT), death_time)
    if prior is None:
        return None
    return float(death_time) - float(prior.start_time)


def nearest_match_phase(
    snapshots: Sequence[GameStateSnapshot],
    video_time: float,
    *,
    max_gap_seconds: float,
) -> MatchPhase | None:
    """Nearest snapshot ``match_phase`` within ``max_gap_seconds``; else ``None``."""
    if not snapshots or max_gap_seconds < 0:
        return None
    best_phase: MatchPhase | None = None
    best_gap = float("inf")
    for snapshot in snapshots:
        gap = abs(float(snapshot.timestamp) - float(video_time))
        if gap > max_gap_seconds:
            continue
        if gap < best_gap:
            best_phase = snapshot.match_phase
            best_gap = gap
            if gap == 0.0:
                return snapshot.match_phase
    return best_phase


def roster_facts_at_death(
    players: PlayersEvidence | None,
    *,
    death_time: float,
    lookback_seconds: float,
) -> tuple[NumbersState | None, bool | None, float | None]:
    """Numbers state + roster-change facts from players evidence only."""
    if players is None:
        return None, None, None
    numbers = None
    if players.at_death is not None:
        numbers = numbers_state(
            players.at_death.ally_alive_count,
            players.at_death.opponent_alive_count,
        )
    changed, seconds = roster_change_before_death(
        players.trajectory,
        death_time=death_time,
        lookback_seconds=lookback_seconds,
    )
    return numbers, changed, seconds


def roster_change_before_death(
    trajectory: Sequence[PlayerCountPoint],
    *,
    death_time: float,
    lookback_seconds: float,
) -> tuple[bool, float | None]:
    """Whether compressed AvB changed in ``(death - lookback, death]``.

    Records *what* changed (timing), not why. No prior change → ``False`` /
    ``None`` seconds. Does not invent transitions outside the lookback.
    """
    ordered = sorted(trajectory, key=lambda p: p.video_time)
    if not ordered:
        return False, None
    window_start = float(death_time) - float(lookback_seconds)
    last_change_at: float | None = None
    prev_key: tuple[int, int] | None = None
    for point in ordered:
        key = (int(point.ally_alive_count), int(point.opponent_alive_count))
        t = float(point.video_time)
        if prev_key is not None and key != prev_key:
            if window_start < t <= float(death_time):
                last_change_at = t
        if t <= float(death_time):
            prev_key = key
        if t > float(death_time):
            break
    if last_change_at is None:
        return False, None
    return True, float(death_time) - last_change_at
