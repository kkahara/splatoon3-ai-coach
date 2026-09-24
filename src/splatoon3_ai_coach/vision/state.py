"""Pure temporal state fusion from vision frame results."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from splatoon3_ai_coach.config.models import (
    ActiveGameplayDetectorConfig,
    DeathDetectorConfig,
    LifecycleFusionConfig,
    LowInkDetectorConfig,
    MapOverlayDetectorConfig,
    PlayerCountDetectorConfig,
    RespawnDetectorConfig,
    ScoreDetectorConfig,
    SplatDetectorConfig,
    StateFusionConfig,
    TimerDetectorConfig,
)
from splatoon3_ai_coach.vision.lifecycle import (
    LifecycleFuser,
    extract_lifecycle_observation,
)
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    GameStateSnapshot,
    MatchPhase,
    PlayerCountReading,
    ScoreReading,
    ScoreSideReading,
    SourceFrameReference,
    SplatBannerInstance,
    SplatReading,
    StateQuality,
    TimerReading,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.player_count import alive_counts_from_reading


@dataclass
class _ScoreSideMemory:
    """Last accepted observation for one score side (ally or opponent)."""

    value: int | None = None
    last_at: float | None = None
    evidence_ids: list[str] = field(default_factory=list)


def fuse_timer_state(
    frame_results: list[VisionFrameResult],
    timer_config: TimerDetectorConfig,
    fusion_config: StateFusionConfig,
) -> list[GameStateSnapshot]:
    """Convert observations into accepted game-state snapshots (timer-only tests)."""
    return fuse_game_state(frame_results, timer_config, fusion_config)


def fuse_game_state(
    frame_results: list[VisionFrameResult],
    timer_config: TimerDetectorConfig,
    fusion_config: StateFusionConfig,
    death_config: DeathDetectorConfig | None = None,
    splat_config: SplatDetectorConfig | None = None,
    respawn_config: RespawnDetectorConfig | None = None,
    active_gameplay_config: ActiveGameplayDetectorConfig | None = None,
    lifecycle_config: LifecycleFusionConfig | None = None,
    map_overlay_config: MapOverlayDetectorConfig | None = None,
    player_count_config: PlayerCountDetectorConfig | None = None,
    low_ink_config: LowInkDetectorConfig | None = None,
    score_config: ScoreDetectorConfig | None = None,
) -> list[GameStateSnapshot]:
    """Fuse detector readings into domain snapshots. Does not emit game events."""
    death_cfg = death_config or DeathDetectorConfig()
    splat_cfg = splat_config or SplatDetectorConfig()
    respawn_cfg = respawn_config or RespawnDetectorConfig()
    active_cfg = active_gameplay_config or ActiveGameplayDetectorConfig()
    map_cfg = map_overlay_config or MapOverlayDetectorConfig()
    player_count_cfg = player_count_config or PlayerCountDetectorConfig()
    score_cfg = score_config or ScoreDetectorConfig()
    lifecycle_cfg = lifecycle_config or LifecycleFusionConfig()
    lifecycle = LifecycleFuser(lifecycle_cfg)

    snapshots: list[GameStateSnapshot] = []
    timer_values: deque[float] = deque(maxlen=fusion_config.smoothing_window)
    last_timer: float | None = None
    last_timer_at: float | None = None
    last_timer_ids: list[str] = []
    last_splatted: bool | None = None
    last_splatted_at: float | None = None
    last_splatted_ids: list[str] = []
    ally_score_mem = _ScoreSideMemory()
    opponent_score_mem = _ScoreSideMemory()

    for frame in sorted(frame_results, key=lambda item: item.timestamp):
        source = _source_reference(frame)
        remaining, quality, timer_ids, last_timer, last_timer_at, last_timer_ids = (
            _fuse_timer_frame(
                frame,
                timer_config,
                fusion_config,
                timer_values,
                last_timer,
                last_timer_at,
                last_timer_ids,
            )
        )
        obs = extract_lifecycle_observation(
            frame,
            death_config=death_cfg,
            respawn_config=respawn_cfg,
            active_config=active_cfg,
            map_overlay_config=map_cfg,
            low_ink_config=low_ink_config,
            timer_config=timer_config,
        )
        # Held timer from fusion also counts as in-match context.
        if remaining is not None:
            obs.match_context = True
            obs.timer_seconds = remaining
        life = lifecycle.step(frame.timestamp, obs)
        splatted, instances, last_splatted, last_splatted_at, last_splatted_ids = (
            _fuse_splat_frame(
                frame,
                splat_cfg,
                fusion_config,
                last_splatted,
                last_splatted_at,
                last_splatted_ids,
            )
        )
        ally_alive, opponent_alive, player_count_ids, player_count_conf = (
            _fuse_player_count_frame(frame, player_count_cfg)
        )
        (
            ally_remaining,
            ally_score_quality,
            opponent_remaining,
            opponent_score_quality,
            score_ids,
            ally_score_mem,
            opponent_score_mem,
        ) = _fuse_score_frame(
            frame,
            life.match_phase,
            score_cfg,
            ally_score_mem,
            opponent_score_mem,
        )
        snapshots.append(
            GameStateSnapshot(
                timestamp=frame.timestamp,
                match_time_remaining=remaining,
                player_alive=life.player_alive,
                player_splatted=splatted,
                splat_instances=instances,
                countdown_present=life.countdown_present,
                active_gameplay=life.active_gameplay,
                map_overlay_present=life.map_overlay_present,
                low_ink_present=life.low_ink_present,
                match_phase=life.match_phase,
                player_lifecycle=life.player_lifecycle,
                countdown_confirmed_this_death_episode=(
                    life.countdown_confirmed_this_death_episode
                ),
                awaiting_control_confirmed_this_death_episode=(
                    life.awaiting_control_confirmed_this_death_episode
                ),
                ally_alive_count=ally_alive,
                opponent_alive_count=opponent_alive,
                player_count_confidence=player_count_conf,
                ally_remaining=ally_remaining,
                opponent_remaining=opponent_remaining,
                ally_score_quality=ally_score_quality,
                opponent_score_quality=opponent_score_quality,
                quality=quality,
                evidence_ids=_combined_evidence(
                    remaining,
                    timer_ids,
                    life.player_alive,
                    life.evidence_ids,
                    splatted,
                    last_splatted_ids,
                    ally_alive,
                    player_count_ids,
                    ally_remaining=ally_remaining,
                    opponent_remaining=opponent_remaining,
                    score_ids=score_ids,
                ),
                source_frame=source,
                last_observed_at=last_timer_at,
                observation_age_seconds=_age(frame.timestamp, last_timer_at),
            )
        )

    return snapshots


def fuse_score_sides_at(
    *,
    timestamp: float,
    match_phase: MatchPhase,
    reading: ScoreReading | None,
    reading_usable: bool,
    evidence_id: str | None,
    max_hold_seconds: float,
    ally_mem: _ScoreSideMemory,
    opponent_mem: _ScoreSideMemory,
) -> tuple[
    int | None,
    StateQuality,
    int | None,
    StateQuality,
    list[str],
    _ScoreSideMemory,
    _ScoreSideMemory,
]:
    """Pure per-side score fusion (left→ally, right→opponent).

    Hold last observation only — never invent unobserved values.
    Outside ``in_match``, both sides clear to unknown.
    """
    if match_phase != "in_match":
        cleared = _ScoreSideMemory()
        return None, "unknown", None, "unknown", [], cleared, cleared

    left_side: ScoreSideReading | None = None
    right_side: ScoreSideReading | None = None
    if reading_usable and reading is not None:
        left_side = reading.left
        right_side = reading.right

    ally_val, ally_q, ally_mem = _fuse_one_score_side(
        timestamp=timestamp,
        side=left_side,
        mem=ally_mem,
        evidence_id=evidence_id,
        max_hold_seconds=max_hold_seconds,
    )
    opp_val, opp_q, opponent_mem = _fuse_one_score_side(
        timestamp=timestamp,
        side=right_side,
        mem=opponent_mem,
        evidence_id=evidence_id,
        max_hold_seconds=max_hold_seconds,
    )
    ids: list[str] = []
    if ally_q == "observed" or opp_q == "observed":
        if evidence_id:
            ids = [evidence_id]
    elif ally_q == "held" or opp_q == "held":
        held_ids: list[str] = []
        if ally_q == "held":
            held_ids.extend(ally_mem.evidence_ids)
        if opp_q == "held":
            held_ids.extend(opponent_mem.evidence_ids)
        ids = list(dict.fromkeys(held_ids))
    return ally_val, ally_q, opp_val, opp_q, ids, ally_mem, opponent_mem


def _fuse_one_score_side(
    *,
    timestamp: float,
    side: ScoreSideReading | None,
    mem: _ScoreSideMemory,
    evidence_id: str | None,
    max_hold_seconds: float,
) -> tuple[int | None, StateQuality, _ScoreSideMemory]:
    """Observe or hold one counter; never interpolate."""
    if side is not None and side.visible and side.value is not None:
        ids = [evidence_id] if evidence_id else []
        mem = _ScoreSideMemory(value=int(side.value), last_at=timestamp, evidence_ids=ids)
        return mem.value, "observed", mem

    if (
        mem.value is not None
        and mem.last_at is not None
        and timestamp - mem.last_at <= max_hold_seconds
    ):
        return mem.value, "held", mem
    return None, "unknown", mem


def _fuse_score_frame(
    frame: VisionFrameResult,
    match_phase: MatchPhase,
    score_config: ScoreDetectorConfig,
    ally_mem: _ScoreSideMemory,
    opponent_mem: _ScoreSideMemory,
) -> tuple[
    int | None,
    StateQuality,
    int | None,
    StateQuality,
    list[str],
    _ScoreSideMemory,
    _ScoreSideMemory,
]:
    """Map ScoreReading left/right → ally/opponent with per-side hold."""
    best = _best_score(frame)
    usable = (
        best is not None and best.confidence >= score_config.min_usable_confidence
    )
    reading: ScoreReading | None = None
    evidence_id: str | None = None
    if usable and best is not None:
        assert isinstance(best.reading, ScoreReading)
        reading = best.reading
        evidence_id = best.id
    return fuse_score_sides_at(
        timestamp=frame.timestamp,
        match_phase=match_phase,
        reading=reading,
        reading_usable=usable,
        evidence_id=evidence_id,
        max_hold_seconds=score_config.score_max_hold_seconds,
        ally_mem=ally_mem,
        opponent_mem=opponent_mem,
    )


def _fuse_timer_frame(
    frame: VisionFrameResult,
    timer_config: TimerDetectorConfig,
    fusion_config: StateFusionConfig,
    accepted_values: deque[float],
    last_value: float | None,
    last_at: float | None,
    last_ids: list[str],
) -> tuple[float | None, StateQuality, list[str], float | None, float | None, list[str]]:
    """Accept or hold timer state for one frame."""
    best = _best_timer(frame)
    if best is None or best.confidence < timer_config.min_usable_confidence:
        return _hold_timer(frame.timestamp, last_value, last_at, last_ids, fusion_config)
    reading = best.reading
    assert isinstance(reading, TimerReading)
    if not _temporally_valid(reading.seconds_remaining, last_value):
        return _hold_timer(frame.timestamp, last_value, last_at, last_ids, fusion_config)

    accepted_values.append(reading.seconds_remaining)
    smoothed = float(sum(accepted_values) / len(accepted_values))
    if smoothed == reading.seconds_remaining:
        quality: StateQuality = "observed"
    else:
        quality = "smoothed"
    ids = [best.id]
    return smoothed, quality, ids, smoothed, frame.timestamp, ids


def _hold_timer(
    timestamp: float,
    last_value: float | None,
    last_at: float | None,
    last_ids: list[str],
    fusion_config: StateFusionConfig,
) -> tuple[float | None, StateQuality, list[str], float | None, float | None, list[str]]:
    """Return held or unknown timer fields without mutating last-accepted time."""
    if last_value is not None and last_at is not None:
        if timestamp - last_at <= fusion_config.max_hold_duration:
            return last_value, "held", last_ids, last_value, last_at, last_ids
    return None, "unknown", [], last_value, last_at, last_ids


def _fuse_splat_frame(
    frame: VisionFrameResult,
    splat_config: SplatDetectorConfig,
    fusion_config: StateFusionConfig,
    last_splatted: bool | None,
    last_at: float | None,
    last_ids: list[str],
) -> tuple[
    bool | None,
    list[SplatBannerInstance],
    bool | None,
    float | None,
    list[str],
]:
    """Fuse transient ``player_splatted`` from kill-banner readings.

    Rules:
    - usable ``detected`` → ``True`` plus this-frame ``splat_instances``
    - short hold after a positive → ``True`` with **empty** instances
    - expired / no cue → ``None`` (never assert ``False``)

    Episodes must not see held banners as still present.
    """
    best = _best_splat(frame)
    if best is not None and best.confidence >= splat_config.min_usable_confidence:
        reading = best.reading
        assert isinstance(reading, SplatReading)
        if reading.detected:
            return (
                True,
                list(reading.instances),
                True,
                frame.timestamp,
                [best.id],
            )

    if last_splatted is True and last_at is not None:
        if frame.timestamp - last_at <= fusion_config.max_hold_duration:
            return True, [], last_splatted, last_at, last_ids
    return None, [], None, last_at, []


def _best_timer(frame: VisionFrameResult) -> DetectorResult | None:
    """Highest-confidence timer result on a frame, if any."""
    results = [
        detection
        for detection in frame.detections
        if detection.detector_name == "timer"
        and isinstance(detection.reading, TimerReading)
    ]
    return max(results, key=lambda item: item.confidence) if results else None


def _best_splat(frame: VisionFrameResult) -> DetectorResult | None:
    """Highest-confidence splat result on a frame, if any."""
    results = [
        detection
        for detection in frame.detections
        if detection.detector_name == "splat"
        and isinstance(detection.reading, SplatReading)
    ]
    return max(results, key=lambda item: item.confidence) if results else None


def _best_score(frame: VisionFrameResult) -> DetectorResult | None:
    """Highest-confidence score result on a frame, if any."""
    results = [
        detection
        for detection in frame.detections
        if detection.detector_name == "score"
        and isinstance(detection.reading, ScoreReading)
    ]
    return max(results, key=lambda item: item.confidence) if results else None


def _fuse_player_count_frame(
    frame: VisionFrameResult,
    player_count_config: PlayerCountDetectorConfig,
) -> tuple[int | None, int | None, list[str], float | None]:
    """Fuse roster alive counts from an X-marker reading (no hold)."""
    best = _best_player_count(frame)
    if best is None or best.confidence < player_count_config.min_usable_confidence:
        return None, None, [], None
    reading = best.reading
    assert isinstance(reading, PlayerCountReading)
    ally_alive, opponent_alive = alive_counts_from_reading(reading)
    return ally_alive, opponent_alive, [best.id], float(best.confidence)


def _best_player_count(frame: VisionFrameResult) -> DetectorResult | None:
    """Highest-confidence player_count result on a frame, if any."""
    results = [
        detection
        for detection in frame.detections
        if detection.detector_name == "player_count"
        and isinstance(detection.reading, PlayerCountReading)
    ]
    return max(results, key=lambda item: item.confidence) if results else None


def _combined_evidence(
    remaining: float | None,
    timer_ids: list[str],
    alive: bool | None,
    life_ids: list[str],
    splatted: bool | None,
    splat_ids: list[str],
    ally_alive_count: int | None = None,
    player_count_ids: list[str] | None = None,
    *,
    ally_remaining: int | None = None,
    opponent_remaining: int | None = None,
    score_ids: list[str] | None = None,
) -> list[str]:
    """Keep evidence IDs for fields this snapshot actually asserts."""
    ids: list[str] = []
    if remaining is not None:
        ids.extend(timer_ids)
    if alive is not None:
        ids.extend(life_ids)
    if splatted is not None:
        ids.extend(splat_ids)
    if ally_alive_count is not None and player_count_ids:
        ids.extend(player_count_ids)
    if (ally_remaining is not None or opponent_remaining is not None) and score_ids:
        ids.extend(score_ids)
    seen: set[str] = set()
    unique: list[str] = []
    for item in ids:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _temporally_valid(candidate: float, last_accepted: float | None) -> bool:
    """Return whether a timer reading is monotonic during normal play."""
    if last_accepted is None:
        return True
    return candidate <= last_accepted + 0.5


def _age(timestamp: float, last_observed_at: float | None) -> float | None:
    """Age of the last accepted timer observation."""
    if last_observed_at is None:
        return None
    return timestamp - last_observed_at


def _source_reference(frame: VisionFrameResult) -> SourceFrameReference:
    """Build a source-frame reference from a vision frame result."""
    return SourceFrameReference(
        frame_id=frame.frame_id,
        source_frame_index=frame.source_frame_index,
        timestamp=frame.timestamp,
        source_pts=frame.source_pts,
        source_time_base_num=frame.source_time_base_num,
        source_time_base_den=frame.source_time_base_den,
        frame_path=frame.frame_path,
    )
