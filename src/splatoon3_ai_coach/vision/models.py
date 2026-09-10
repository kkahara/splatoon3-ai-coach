"""Phase 3 vision models: observations, state, and events."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, computed_field

from splatoon3_ai_coach.vision.provenance import PIPELINE_VERSION

FrameSource = Literal["evidence", "cadence"]
StateQuality = Literal["observed", "smoothed", "held", "unknown"]


class GameEventType(StrEnum):
    """Semantic gameplay events inferred from fused state transitions."""

    SPLAT = "splat"
    DEATH = "death"
    RESPAWN = "respawn"
    ACTIVE_AGAIN = "active_again"
    MAP_OVERLAY = "map_overlay"
    SPECIAL_USED = "special_used"
    SPECIAL_READY = "special_ready"
    OBJECTIVE_UPDATE = "objective_update"
    PLAYER_DETECTED = "player_detected"
    ENEMY_DETECTED = "enemy_detected"
    BOMB_DETECTED = "bomb_detected"
    ZONE_DETECTED = "zone_detected"
    TOWER_DETECTED = "tower_detected"


class GameEventSource(StrEnum):
    """Which fusion layer asserted the event. Not a detector name."""

    LIFECYCLE = "lifecycle"
    SPLAT_EPISODE = "splat_episode"
    STATE = "state"


class GameEventReason(StrEnum):
    """Why the fusion layer emitted this event."""

    ALIVE_TO_DEAD = "alive_to_dead"
    UNKNOWN_TO_DEAD = "unknown_to_dead"
    COUNTDOWN_PLATE_ENDED = "countdown_plate_ended"
    SKIP_COUNTDOWN_CONTROL = "skip_countdown_control"
    AWAITING_CONTROL_TO_ALIVE = "awaiting_control_to_alive"
    SPLAT_INSTANCE_OPENED = "splat_instance_opened"
    MAP_OVERLAY_PRESENT = "map_overlay_present"


PlayerLifecycle = Literal[
    "unknown", "alive", "dead", "countdown", "respawned", "awaiting_control"
]
# Orthogonal to PlayerLifecycle. ``opening_countdown`` is frozen 5:00/3:00
# before GO — not the respawn waiting plate (player_lifecycle ``countdown``).
MatchPhase = Literal[
    "out_of_match",
    "intro",
    "opening_countdown",
    "in_match",
    "post_match",
]
RespawnEvidenceType = Literal["template", "ocr", "heuristic", "hold"]
RespawnCountdownValue = Literal[1, 2, 3, 4]


class TimerReading(BaseModel):
    """Timer OCR result from a single frame."""

    kind: Literal["timer"] = "timer"
    display: str
    seconds_remaining: float = Field(ge=0)


class DeathReading(BaseModel):
    """Per-frame death-UI observation. Not a semantic DEATH event."""

    kind: Literal["death"] = "death"
    detected: bool = False
    # Strong Ouch: template match and/or OCR (drives death when require_glyph).
    ouch_detected: bool = False
    ouch_template_score: float = Field(default=0.0, ge=0, le=1)
    ouch_white_score: float = Field(default=0.0, ge=0, le=1)
    # White-pixel HUD heuristic only; never sufficient alone when require_glyph.
    ouch_heuristic: bool = False
    banner_detected: bool = False
    banner_template_score: float = Field(default=0.0, ge=0, le=1)
    banner_dark_score: float = Field(default=0.0, ge=0, le=1)


class SplatBannerInstance(BaseModel):
    """One kill-banner row observed this frame. Not a SPLAT event."""

    fingerprint: str
    slot_y: float = Field(default=0.5, ge=0, le=1)
    skull_score: float = Field(default=0.0, ge=0, le=1)
    text_score: float = Field(default=0.0, ge=0, le=1)


class SplatReading(BaseModel):
    """Per-frame local-kill banner observation. Not a semantic SPLAT event."""

    kind: Literal["splat"] = "splat"
    detected: bool = False
    skull_score: float = Field(default=0.0, ge=0, le=1)
    text_score: float = Field(default=0.0, ge=0, le=1)
    adjacent_color_score: float = Field(default=0.0, ge=0, le=1)
    instances: list[SplatBannerInstance] = Field(default_factory=list)
    # Optional enrichment only; events must not gate on OCR.
    victim_name: str | None = None
    victim_name_confidence: float = Field(default=0.0, ge=0, le=1)


class RespawnReading(BaseModel):
    """Per-frame respawn/waiting-UI observation. Not a RESPAWN event."""

    kind: Literal["respawn"] = "respawn"
    detected: bool = False
    confidence: float = Field(default=0.0, ge=0, le=1)
    countdown_value: RespawnCountdownValue | None = None
    template_score: float = Field(default=0.0, ge=0, le=1)
    ocr_text: str | None = None
    evidence_type: RespawnEvidenceType | None = None
    # Plate metrics retained for diagnostics / heuristic fallback.
    presence_score: float = Field(default=0.0, ge=0, le=1)
    dark_frac: float = Field(default=0.0, ge=0, le=1)
    bright_frac: float = Field(default=0.0, ge=0, le=1)
    p95: float = Field(default=0.0, ge=0, le=1)
    yellow_frac: float = Field(default=0.0, ge=0, le=1)
    mean_sat: float = Field(default=0.0, ge=0, le=1)
    mean_val: float = Field(default=0.0, ge=0, le=1)


class ActiveGameplayReading(BaseModel):
    """Per-frame HUD/weapon/center evidence. Not an ACTIVE_AGAIN event or match phase.

    ``detected`` is HUD-chrome evidence only. Match phase and player lifecycle
    decide whether that evidence means in-control play.
    """

    kind: Literal["active_gameplay"] = "active_gameplay"
    detected: bool = False
    score: float = Field(default=0.0, ge=0, le=1)
    weapon_edge_frac: float = Field(default=0.0, ge=0, le=1)
    weapon_luma_std: float = Field(default=0.0, ge=0, le=1)
    hud_edge_frac: float = Field(default=0.0, ge=0, le=1)
    # Center ink-tank / reticle cue — used for return_control, not detected.
    center_edge_frac: float = Field(default=0.0, ge=0, le=1)
    center_control: bool = False
    # Kept for older manifests; the detector no longer nested-runs the timer.
    timer_present: bool = False
    ouch_veto_score: float = Field(default=0.0, ge=0, le=1)
    # Softer latch-exit cue: weapon + center (HUD optional). Used only while
    # awaiting_control; does not change detected / snapshot active_gameplay.
    return_control: bool = False


class MapOverlayReading(BaseModel):
    """Per-frame map-viewing observation (independent gameplay evidence).

    Recorded for coaching (how often / when the player opens the map).
    Must not drive DEATH, RESPAWN, COUNTDOWN, ACTIVE_AGAIN, or the
    post-respawn ``awaiting_control`` latch.
    """

    kind: Literal["map_overlay"] = "map_overlay"
    present: bool = False
    template_score: float = Field(default=0.0, ge=0, le=1)
    map_edge_frac: float = Field(default=0.0, ge=0, le=1)
    periphery_blur: float = Field(default=0.0, ge=0, le=1)
    center_tank_edge_frac: float = Field(default=0.0, ge=0, le=1)


class MatchIntroReading(BaseModel):
    """Per-frame intro plate observation (stage + battle mode templates).

    Not a GameEvent. Captures match identity from the pre-match intro UI.
    """

    kind: Literal["match_intro"] = "match_intro"
    stage_id: str | None = None
    battle_mode_id: str | None = None
    stage_template_score: float = Field(default=0.0, ge=0, le=1)
    battle_mode_template_score: float = Field(default=0.0, ge=0, le=1)


class PlayerCountReading(BaseModel):
    """Per-frame HUD death-X observations. Detector evidence only.

    Describes which player-slot ROIs show the dark-gray X death marker.
    Not coaching vocabulary — fused alive counts live on ``GameStateSnapshot``.
    Slot indexes are 1-based (1..4). Per-slot scores are masked
    ``TM_SQDIFF_NORMED`` distances (lower = better X match); detection is
    decided by the detector against ``sqdiff_match_threshold``, not by
    consumers reversing these scores.
    """

    kind: Literal["player_count"] = "player_count"
    ally_dead_slots: tuple[int, ...] = ()
    opponent_dead_slots: tuple[int, ...] = ()
    ally_slot_scores: tuple[float, ...] = ()
    opponent_slot_scores: tuple[float, ...] = ()


class SpecialGaugeReading(BaseModel):
    """Per-frame Special-gauge observation. Detector evidence only.

    Sparse HUD sample for eventual coaching. Not a SPECIAL_READY /
    SPECIAL_USED GameEvent. ``fill_fraction`` is approximate (radial
    sector estimate), not pixel-perfect. When ``visible`` is false,
    ``fill_fraction`` is None — never invent zero fill from absence.
    """

    kind: Literal["special_gauge"] = "special_gauge"
    visible: bool = False
    fill_fraction: float | None = Field(default=None, ge=0, le=1)
    ready: bool = False
    # Echo of detect(timestamp=...); frame time also on VisionFrameResult.
    timestamp: float | None = None
    dial_score: float = Field(default=0.0, ge=0, le=1)
    lit_sector_fraction: float = Field(default=0.0, ge=0, le=1)
    ready_prompt_score: float = Field(default=0.0, ge=0, le=1)


Reading = Annotated[
    TimerReading
    | DeathReading
    | SplatReading
    | RespawnReading
    | ActiveGameplayReading
    | MapOverlayReading
    | MatchIntroReading
    | PlayerCountReading
    | SpecialGaugeReading,
    Field(discriminator="kind"),
]


class SourceFrameReference(BaseModel):
    """Decoder identity for a frame, independent of detector output."""

    frame_id: str
    source_frame_index: int | None = None
    timestamp: float = Field(ge=0)
    source_pts: int | None = None
    source_time_base_num: int | None = None
    source_time_base_den: int | None = None
    frame_path: str | None = None


class DetectorResult(BaseModel):
    """One detector observation on one frame."""

    id: str
    detector_name: str
    detector_version: str
    confidence: float = Field(ge=0, le=1)
    reading: Reading


class VisionFrameResult(BaseModel):
    """Generic per-frame vision output."""

    frame_id: str
    timestamp: float = Field(ge=0)
    source: FrameSource
    source_frame_index: int | None = None
    source_pts: int | None = None
    source_time_base_num: int | None = None
    source_time_base_den: int | None = None
    frame_path: str | None = None
    detections: list[DetectorResult] = Field(default_factory=list)


class GameStateSnapshot(BaseModel):
    """Minimal domain state at one point in time."""

    timestamp: float = Field(ge=0)
    match_time_remaining: float | None = None
    player_alive: bool | None = None
    player_splatted: bool | None = None
    # This-frame banner instances only. Never copied from fusion hold.
    splat_instances: list[SplatBannerInstance] = Field(default_factory=list)
    countdown_present: bool | None = None
    active_gameplay: bool | None = None
    map_overlay_present: bool | None = None
    match_phase: MatchPhase = "out_of_match"
    player_lifecycle: PlayerLifecycle = "unknown"
    # Episode latches for respawn / control-return diagnostics (viewer).
    countdown_confirmed_this_death_episode: bool | None = None
    awaiting_control_confirmed_this_death_episode: bool | None = None
    # Fused roster alive counts from HUD X markers (None = not observed).
    ally_alive_count: int | None = None
    opponent_alive_count: int | None = None
    # Detector confidence for the fused roster observation (None if unknown).
    player_count_confidence: float | None = None
    quality: StateQuality = "unknown"
    evidence_ids: list[str] = Field(default_factory=list)
    source_frame: SourceFrameReference | None = None
    last_observed_at: float | None = None
    observation_age_seconds: float | None = None


class GameEvent(BaseModel):
    """Authoritative gameplay event inferred after fusion.

    Detector readings stay on ``VisionFrameResult``. This record is the
    one-shot fact: a lifecycle edge, a new splat episode, or a fused
    map-overlay interval. ``debounce_ms`` is only a lifecycle edge
    guard; splat episodes are fingerprint identity, not a time window.
    """

    start_time: float = Field(ge=0)
    end_time: float | None = None
    event_type: GameEventType
    source: GameEventSource = GameEventSource.LIFECYCLE
    reason: GameEventReason | None = None
    from_lifecycle: PlayerLifecycle | None = None
    to_lifecycle: PlayerLifecycle | None = None
    splat_fingerprint: str | None = None
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    source_frames: list[SourceFrameReference] = Field(default_factory=list)


class AnalysisIdentity(BaseModel):
    """Reproducible analysis provenance."""

    analysis_id: str
    pipeline_version: str = PIPELINE_VERSION
    package_version: str
    detector_versions: dict[str, str] = Field(default_factory=dict)
    # Empty for cadence-only analysis (no extraction manifest).
    extraction_manifest_sha256: str = ""
    vision_config_sha256: str
    video_identity: str
    # Analysis-level UI language; also hashed into vision_config_sha256.
    language: str = "en"


class VisionTimingMetrics(BaseModel):
    """Wall-clock cost of one cadence analysis run."""

    video_duration_seconds: float = Field(ge=0)
    decoded_frame_count: int = Field(ge=0)
    cadence_frame_count: int = Field(ge=0)
    decode_seconds: float = Field(
        ge=0,
        description=(
            "Wall time spent producing usable BGR frames: PyAV retrieval, "
            "to_ndarray(bgr24), and optional downscale. Excludes detector "
            "and temporal work after each yield."
        ),
    )
    timer_detector_seconds: float = Field(default=0.0, ge=0)
    death_detector_seconds: float = Field(default=0.0, ge=0)
    splat_detector_seconds: float = Field(default=0.0, ge=0)
    respawn_detector_seconds: float = Field(default=0.0, ge=0)
    active_gameplay_detector_seconds: float = Field(default=0.0, ge=0)
    map_overlay_detector_seconds: float = Field(default=0.0, ge=0)
    player_count_detector_seconds: float = Field(default=0.0, ge=0)
    special_gauge_detector_seconds: float = Field(default=0.0, ge=0)
    timer_detector_invocations: int = Field(default=0, ge=0)
    death_detector_invocations: int = Field(default=0, ge=0)
    splat_detector_invocations: int = Field(default=0, ge=0)
    respawn_detector_invocations: int = Field(default=0, ge=0)
    active_gameplay_detector_invocations: int = Field(default=0, ge=0)
    map_overlay_detector_invocations: int = Field(default=0, ge=0)
    player_count_detector_invocations: int = Field(default=0, ge=0)
    special_gauge_detector_invocations: int = Field(default=0, ge=0)
    temporal_seconds: float = Field(ge=0)
    total_seconds: float = Field(ge=0)

    @computed_field
    @property
    def realtime_factor(self) -> float:
        """Video duration divided by total analysis time."""
        if self.total_seconds <= 0:
            return 0.0
        return self.video_duration_seconds / self.total_seconds


class VisionManifest(BaseModel):
    """Versioned output of the Phase 3 vision pipeline."""

    schema_version: int = 1
    analysis: AnalysisIdentity
    video_identity: str
    extraction_manifest_path: str | None = None
    frame_results: list[VisionFrameResult] = Field(default_factory=list)
    state_snapshots: list[GameStateSnapshot] = Field(default_factory=list)
    game_events: list[GameEvent] = Field(default_factory=list)
    timing: VisionTimingMetrics | None = None
