"""Phase 3 vision models: observations, state, and events."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from splatoon3_ai_coach.vision.provenance import PIPELINE_VERSION

FrameSource = Literal["evidence", "cadence"]
StateQuality = Literal["observed", "smoothed", "held", "unknown"]


class GameEventType(StrEnum):
    """Semantic gameplay events inferred from state transitions."""

    SPLAT = "splat"
    DEATH = "death"
    RESPAWN = "respawn"
    ACTIVE_AGAIN = "active_again"
    SPECIAL_USED = "special_used"
    SPECIAL_READY = "special_ready"
    OBJECTIVE_UPDATE = "objective_update"
    PLAYER_DETECTED = "player_detected"
    ENEMY_DETECTED = "enemy_detected"
    BOMB_DETECTED = "bomb_detected"
    ZONE_DETECTED = "zone_detected"
    TOWER_DETECTED = "tower_detected"


PlayerLifecycle = Literal["unknown", "alive", "dead", "countdown", "respawned"]
RespawnEvidenceType = Literal["template", "ocr", "heuristic"]
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


class SplatReading(BaseModel):
    """Per-frame local-kill banner observation. Not a semantic SPLAT event."""

    kind: Literal["splat"] = "splat"
    detected: bool = False
    skull_score: float = Field(default=0.0, ge=0, le=1)
    adjacent_color_score: float = Field(default=0.0, ge=0, le=1)
    # Future: victim identity (not extracted in v1).
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
    """Per-frame positive normal-gameplay observation. Not an ACTIVE_AGAIN event."""

    kind: Literal["active_gameplay"] = "active_gameplay"
    detected: bool = False
    score: float = Field(default=0.0, ge=0, le=1)
    weapon_edge_frac: float = Field(default=0.0, ge=0, le=1)
    weapon_luma_std: float = Field(default=0.0, ge=0, le=1)
    hud_edge_frac: float = Field(default=0.0, ge=0, le=1)


Reading = Annotated[
    TimerReading | DeathReading | SplatReading | RespawnReading | ActiveGameplayReading,
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
    countdown_present: bool | None = None
    active_gameplay: bool | None = None
    player_lifecycle: PlayerLifecycle = "unknown"
    # Episode latch for respawn anti-FP diagnostics (viewer / debugging).
    countdown_confirmed_this_death_episode: bool | None = None
    quality: StateQuality = "unknown"
    evidence_ids: list[str] = Field(default_factory=list)
    source_frame: SourceFrameReference | None = None
    last_observed_at: float | None = None
    observation_age_seconds: float | None = None


class GameEvent(BaseModel):
    """Semantic event inferred from state transitions."""

    start_time: float = Field(ge=0)
    end_time: float | None = None
    event_type: GameEventType
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    source_frames: list[SourceFrameReference] = Field(default_factory=list)


class AnalysisIdentity(BaseModel):
    """Reproducible analysis provenance."""

    analysis_id: str
    pipeline_version: str = PIPELINE_VERSION
    package_version: str
    detector_versions: dict[str, str] = Field(default_factory=dict)
    extraction_manifest_sha256: str
    vision_config_sha256: str
    video_identity: str


class VisionManifest(BaseModel):
    """Versioned output of the Phase 3 vision pipeline."""

    schema_version: int = 1
    analysis: AnalysisIdentity
    video_identity: str
    extraction_manifest_path: str
    frame_results: list[VisionFrameResult] = Field(default_factory=list)
    state_snapshots: list[GameStateSnapshot] = Field(default_factory=list)
    game_events: list[GameEvent] = Field(default_factory=list)
