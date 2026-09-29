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
    ZoneControlDetectorConfig,
)
from splatoon3_ai_coach.vision.lifecycle import (
    LifecycleFuser,
    extract_lifecycle_observation,
)
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    GameStateSnapshot,
    PlayerCountReading,
    SourceFrameReference,
    SplatBannerInstance,
    SplatReading,
    StateQuality,
    TimerReading,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.player_count import alive_counts_from_reading
from splatoon3_ai_coach.vision.score_fusion import (
    ScoreFrameFuser,
    ScoreFrameFusion,
    _ScoreSideMemory,
    fuse_score_sides_at,
    resolve_battle_mode,
    retract_contradicted_scores,
)
from splatoon3_ai_coach.vision.zone_control_fusion import (
    ZoneControlFuser,
)

__all__ = [
    "_ScoreSideMemory",
    "debounce_roster_side",
    "fuse_game_state",
    "fuse_score_sides_at",
    "fuse_timer_state",
    "ZoneControlFuser",
]


@dataclass
class _RosterSideMemory:
    """Debounce state for one roster side's alive count."""

    accepted: int | None = None
    last_seen_at: float | None = None
    evidence_ids: list[str] = field(default_factory=list)
    pending: int | None = None
    pending_count: int = 0
    pending_ids: list[str] = field(default_factory=list)

    def accept(self, value: int, timestamp: float, ids: list[str]) -> None:
        """Make ``value`` the accepted count, last read at ``timestamp``."""
        self.accepted = value
        self.last_seen_at = timestamp
        self.evidence_ids = list(ids)
        self.clear_pending()

    def clear_pending(self) -> None:
        """Drop any unconfirmed change."""
        self.pending, self.pending_count, self.pending_ids = None, 0, []


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
    zone_control_config: ZoneControlDetectorConfig | None = None,
) -> list[GameStateSnapshot]:
    """Fuse detector readings into domain snapshots. Does not emit game events."""
    death_cfg = death_config or DeathDetectorConfig()
    splat_cfg = splat_config or SplatDetectorConfig()
    respawn_cfg = respawn_config or RespawnDetectorConfig()
    active_cfg = active_gameplay_config or ActiveGameplayDetectorConfig()
    map_cfg = map_overlay_config or MapOverlayDetectorConfig()
    player_count_cfg = player_count_config or PlayerCountDetectorConfig()
    score_cfg = score_config or ScoreDetectorConfig()
    zone_cfg = zone_control_config or ZoneControlDetectorConfig()
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
    score_fuser = ScoreFrameFuser(score_cfg, resolve_battle_mode(frame_results))
    score_fusions: list[ScoreFrameFusion] = []
    zone_fuser = ZoneControlFuser(zone_cfg, resolve_battle_mode(frame_results))
    ally_roster_mem = _RosterSideMemory()
    opponent_roster_mem = _RosterSideMemory()

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
            _fuse_player_count_frame(
                frame, player_count_cfg, ally_roster_mem, opponent_roster_mem
            )
        )
        score = score_fuser.step(frame, life.match_phase)
        score_fusions.append(score)
        zone = zone_fuser.step(frame, life.match_phase)
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
                ally_remaining=score.ally_remaining,
                opponent_remaining=score.opponent_remaining,
                ally_score_quality=score.ally_score_quality,
                opponent_score_quality=score.opponent_score_quality,
                ally_penalty=score.ally_penalty,
                opponent_penalty=score.opponent_penalty,
                ally_penalty_quality=score.ally_penalty_quality,
                opponent_penalty_quality=score.opponent_penalty_quality,
                zone_control_state=zone.state,
                zone_control_quality=zone.quality,
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
                    score_asserted=score.asserts_anything,
                    score_ids=score.evidence_ids,
                    zone_state=zone.state,
                    zone_ids=zone.evidence_ids,
                ),
                source_frame=source,
                last_observed_at=last_timer_at,
                observation_age_seconds=_age(frame.timestamp, last_timer_at),
            )
        )

    if score_fuser.plausibility.enabled:
        snapshots = _apply_score_retractions(snapshots, score_fusions, score_cfg)
    return snapshots


def _apply_score_retractions(
    snapshots: list[GameStateSnapshot],
    fusions: list[ScoreFrameFusion],
    score_cfg: ScoreDetectorConfig,
) -> list[GameStateSnapshot]:
    """Apply the backward score retraction pass to already-fused snapshots."""
    phases = [snap.match_phase for snap in snapshots]
    retracted = retract_contradicted_scores(fusions, phases, score_cfg.score_drop_slack)
    out: list[GameStateSnapshot] = []
    for snap, before, after in zip(snapshots, fusions, retracted, strict=True):
        if after == before:
            out.append(snap)
            continue
        ids = snap.evidence_ids
        if before.asserts_anything and not after.asserts_anything:
            ids = [i for i in ids if i not in before.evidence_ids]
        out.append(
            snap.model_copy(
                update={
                    "ally_remaining": after.ally_remaining,
                    "ally_score_quality": after.ally_score_quality,
                    "opponent_remaining": after.opponent_remaining,
                    "opponent_score_quality": after.opponent_score_quality,
                    "evidence_ids": ids,
                }
            )
        )
    return out


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


def _fuse_player_count_frame(
    frame: VisionFrameResult,
    player_count_config: PlayerCountDetectorConfig,
    ally_mem: _RosterSideMemory,
    opponent_mem: _RosterSideMemory,
) -> tuple[int | None, int | None, list[str], float | None]:
    """Fuse roster alive counts from an X-marker reading, debounced per side.

    A frame with no player_count result at all (detector not scheduled on it)
    carries no counts and leaves the debounce state untouched; a result below
    ``min_usable_confidence`` is an unreadable frame and restarts confirmation.
    """
    best = _best_player_count(frame)
    if best is None:
        return None, None, [], None
    ally_read: int | None = None
    opponent_read: int | None = None
    reading_id: str | None = None
    confidence: float | None = None
    if best.confidence >= player_count_config.min_usable_confidence:
        reading = best.reading
        assert isinstance(reading, PlayerCountReading)
        ally_read, opponent_read = alive_counts_from_reading(reading)
        reading_id, confidence = best.id, float(best.confidence)
    ally, ally_ids = debounce_roster_side(
        ally_mem, ally_read, reading_id, frame.timestamp, player_count_config
    )
    opponent, opponent_ids = debounce_roster_side(
        opponent_mem, opponent_read, reading_id, frame.timestamp, player_count_config
    )
    if ally is None and opponent is None:
        return None, None, [], None
    ids = list(dict.fromkeys([*ally_ids, *opponent_ids]))
    return ally, opponent, ids, confidence


def debounce_roster_side(
    mem: _RosterSideMemory,
    value: int | None,
    reading_id: str | None,
    timestamp: float,
    config: PlayerCountDetectorConfig,
) -> tuple[int | None, list[str]]:
    """Accept a changed alive count only after consecutive agreeing readings.

    Returns the count this frame asserts and the reading IDs supporting it.
    One-frame misreads never become the accepted count; a real change is
    delayed by ``confirm_readings - 1`` readings. ``None`` input (no usable
    reading) returns ``None`` and restarts any pending confirmation.
    """
    if value is None:
        mem.clear_pending()
        return None, []
    ids = [reading_id] if reading_id else []
    if mem.accepted is None or value == mem.accepted:
        mem.accept(value, timestamp, ids)
        return value, ids
    if value == mem.pending:
        mem.pending_count += 1
        mem.pending_ids.extend(ids)
    else:
        mem.pending, mem.pending_count, mem.pending_ids = value, 1, list(ids)
    if mem.pending_count >= config.confirm_readings:
        mem.accept(value, timestamp, mem.pending_ids)
        return value, list(mem.evidence_ids)
    if (
        mem.last_seen_at is not None
        and timestamp - mem.last_seen_at <= config.hold_seconds
    ):
        return mem.accepted, list(mem.evidence_ids)
    return None, []


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
    score_asserted: bool = False,
    score_ids: list[str] | None = None,
    zone_state: str = "unknown",
    zone_ids: list[str] | None = None,
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
    if score_asserted and score_ids:
        ids.extend(score_ids)
    if zone_state != "unknown" and zone_ids:
        ids.extend(zone_ids)
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
