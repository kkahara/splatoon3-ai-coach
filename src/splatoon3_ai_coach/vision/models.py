"""Phase 3 vision models: observations, state, and events."""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, computed_field, model_validator

from splatoon3_ai_coach.media.video_source import VideoRunMetadata
from splatoon3_ai_coach.vision.provenance import PIPELINE_VERSION
from splatoon3_ai_coach.vision.tower_checkpoint_state import TowerCheckpointState

FrameSource = Literal["evidence", "cadence"]
StateQuality = Literal["observed", "smoothed", "held", "unknown"]
# Fused SZ main counter: ``rejected_implausible`` = a reading was seen but did
# not fit the accepted trajectory (value withheld, never replaced).
ScoreQuality = Literal["observed", "held", "rejected_implausible", "unknown"]
# Fused SZ penalty: ``not_shown`` = main counter visible, no ``+N`` on screen.
# Not zero — a missed or animating ``+N`` looks the same.
PenaltyQuality = Literal["observed", "held", "not_shown", "unknown"]
ZoneControlState = Literal["neutral", "ally_control", "opponent_control", "unknown"]
ZoneControlQuality = Literal["observed", "held", "unknown"]
TowerControlPresentation = Literal["ally", "opponent", "unknown"]


class GameEventType(StrEnum):
    """Semantic gameplay events inferred from fused state transitions."""

    SPLAT = "splat"
    DEATH = "death"
    RESPAWN = "respawn"
    ACTIVE_AGAIN = "active_again"
    MAP_OVERLAY = "map_overlay"
    LOW_INK = "low_ink"
    SPECIAL_USED = "special_used"
    SPECIAL_READY = "special_ready"
    OBJECTIVE_UPDATE = "objective_update"
    ALLY_GAIN_CONTROL = "ally_gain_control"
    ALLY_LOSE_CONTROL = "ally_lose_control"
    OPPONENT_GAIN_CONTROL = "opponent_gain_control"
    OPPONENT_LOSE_CONTROL = "opponent_lose_control"
    ZONE_CONTROL_CHANGED = "zone_control_changed"
    PLAYER_DETECTED = "player_detected"
    ENEMY_DETECTED = "enemy_detected"
    BOMB_DETECTED = "bomb_detected"
    ZONE_DETECTED = "zone_detected"
    TOWER_DETECTED = "tower_detected"
    MATCH_END = "match_end"


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
    LOW_INK_PRESENT = "low_ink_present"
    ZONE_CONTROL_TRANSITION = "zone_control_transition"
    # MATCH_END: first trustworthy game clock 0:00 with no clock afterwards.
    CLOCK_ZERO = "clock_zero"
    # MATCH_END: timer HUD gone for good (overtime / knockout); not a clock read.
    HUD_LOSS = "hud_loss"


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


class ReadyReading(BaseModel):
    """Per-frame pre-match ``Ready?`` plate observation.

    Not a GameEvent. ``present`` means the centered Ready? glyph matched.
    Temporal search window (stage known → first clock tick) is pipeline policy.
    """

    kind: Literal["ready"] = "ready"
    present: bool = False
    template_score: float = Field(default=0.0, ge=0, le=1)


class LowInkReading(BaseModel):
    """Per-frame floating ``Low ink!`` / 「インク不足！」 plate observation.

    Detector evidence only. Fusion maps usable confidence into snapshot
    ``low_ink_present`` (True/False) and leaves None when unusable — never
    treat an unusable reading as False on the snapshot.
    """

    kind: Literal["low_ink"] = "low_ink"
    present: bool = False
    template_score: float = Field(default=0.0, ge=0, le=1)


class PlayerCountReading(BaseModel):
    """Per-frame HUD roster-X observations. Detector evidence only.

    Describes which player-slot ROIs show the dark-gray X marker on
    teammate/opponent icons. Not local-player death and not a GameEvent —
    fused alive counts live on ``GameStateSnapshot``.
    Slot indexes are 1-based (1..4). Per-slot scores are masked
    ``TM_SQDIFF_NORMED`` distances (lower = better X match); detection is
    decided by the detector against ``sqdiff_match_threshold``, not by
    consumers reversing these scores. ``*_dead_slots`` means “X present.”

    ``roster_visible`` is False when a slot ROI has no icon edges (map open,
    respawn map): no slot is scored and the frame carries no roster evidence.
    ``slot_edge_energy`` lists ally slots 1..4 then opponent slots 1..4.
    """

    kind: Literal["player_count"] = "player_count"
    ally_dead_slots: tuple[int, ...] = ()
    opponent_dead_slots: tuple[int, ...] = ()
    ally_slot_scores: tuple[float, ...] = ()
    opponent_slot_scores: tuple[float, ...] = ()
    roster_visible: bool = True
    slot_edge_energy: tuple[float, ...] = ()


class SpecialGaugeReading(BaseModel):
    """Per-frame Special-gauge observation. Detector evidence only.

    ``ready`` is **status on this frame** (Charged!/Press templates or
    near-full ring) — not a transition and not ``SPECIAL_READY``.
    Repeated ``ready=True`` remains ordinary readings.

    Do not conflate: ``ready=True`` ≠ ``SPECIAL_READY``;
    ``fill_fraction`` drop ≠ ``SPECIAL_USED``; ``DEATH`` ≠ ``SPECIAL_USED``.
    Architecture: ``vision/SPECIAL_GAUGE.md``.

    ``fill_fraction`` is approximate (radial sector estimate). When
    ``visible`` is false, ``fill_fraction`` is None — never invent zero fill.
    ``ready_prompt_score`` is ``max(charged_score, press_score)`` for viewer
    compatibility. Do not add event fields to this reading.
    """

    kind: Literal["special_gauge"] = "special_gauge"
    visible: bool = False
    fill_fraction: float | None = Field(default=None, ge=0, le=1)
    ready: bool = False
    # Echo of detect(timestamp=...); frame time also on VisionFrameResult.
    timestamp: float | None = None
    dial_score: float = Field(default=0.0, ge=0, le=1)
    lit_sector_fraction: float = Field(default=0.0, ge=0, le=1)
    charged_score: float = Field(default=0.0, ge=0, le=1)
    press_score: float = Field(default=0.0, ge=0, le=1)
    ready_prompt_score: float = Field(default=0.0, ge=0, le=1)


class ScoreSideReading(BaseModel):
    """One screen-side remaining counter (left or right). Not ally/opponent."""

    value: int | None = Field(default=None, ge=0, le=100)
    digit_scores: list[float] = Field(default_factory=list)
    visible: bool = False


class ScoreReading(BaseModel):
    """Dual remaining counters. Screen geometry only.

    ``left`` / ``right`` are observation positions. Ally/opponent mapping is a
    fusion concern and must not appear here. Penalty fields are optional
    secondary observations: ``*_penalty`` is the ``+N`` value when a ``+``
    glyph followed by digits was seen. ``penalty_evaluated`` says the penalty
    regions were configured and inspected on this frame, so ``None`` there
    means "no ``+N`` visible" rather than "not looked at". Penalty never
    contributes to ``confidence``. ``battle_mode_id`` records the intended
    reader configuration, not independent evidence of the actual match mode.
    """

    kind: Literal["score"] = "score"
    battle_mode_id: str | None = "splat_zones"
    left: ScoreSideReading = Field(default_factory=ScoreSideReading)
    right: ScoreSideReading = Field(default_factory=ScoreSideReading)
    left_penalty: int | None = Field(default=None, ge=0)
    right_penalty: int | None = Field(default=None, ge=0)
    left_penalty_scores: list[float] = Field(default_factory=list)
    right_penalty_scores: list[float] = Field(default_factory=list)
    penalty_evaluated: bool = False
    confidence: float = Field(default=0.0, ge=0, le=1)


class ClamBasketStateReading(BaseModel):
    """Visual state and independent color measurements, not gameplay duration.

    Hue is the circular mean of sampled saturated pixels (OpenCV 0–180);
    concentration measures hue consistency, not classification confidence.
    """

    kind: Literal["clam_basket_state"] = "clam_basket_state"
    visual_state: Literal["open", "closed", "unknown"] = "unknown"
    hue: float | None = Field(default=None, ge=0, lt=180)
    hue_concentration: float = Field(default=0, ge=0, le=1)
    timestamp: float | None = None


class ClamScoreObservation(BaseModel):
    """Fresh Clam score readings mapped using the fixed allied-left HUD layout."""

    association_basis: Literal["fixed_hud_sides"] = "fixed_hud_sides"
    ally_remaining: int | None = Field(default=None, ge=0, le=100)
    opponent_remaining: int | None = Field(default=None, ge=0, le=100)
    ally_penalty: int | None = Field(default=None, gt=0)
    opponent_penalty: int | None = Field(default=None, gt=0)
    evidence_id: str = Field(min_length=1)


class TowerScorePodReading(BaseModel):
    """One localized Tower score pod, retaining native screen geometry/color."""

    value: int | None = Field(default=None, ge=0, le=100)
    bbox: tuple[float, float, float, float]
    center_x: float = Field(ge=0, le=1)
    hue: float | None = Field(default=None, ge=0, lt=180)
    hue_concentration: float = Field(default=0, ge=0, le=1)
    digit_scores: list[float] = Field(default_factory=list)
    usable: bool = False


class RainmakerCountPod(BaseModel):
    """Remaining-count digits and pod color in native screen geometry."""

    value: int | None = Field(default=None, ge=0, le=100)
    bbox: tuple[float, float, float, float]
    hue: float | None = None
    usable: bool = False
    digit_scores: list[float] = Field(default_factory=list)


class RainmakerScoreReading(BaseModel):
    """Per-frame remaining-count observations, before team association."""

    kind: Literal["rainmaker_score"] = "rainmaker_score"
    pods: list[RainmakerCountPod] = Field(default_factory=list)
    timestamp: float | None = None


class RainmakerScoreAssociation(BaseModel):
    """Pod-to-team binding supported by the match Ready color calibration."""

    evidence_id: str
    ally_pod: int | None = None
    opponent_pod: int | None = None
    calibration_time: float
    calibration_frame_ids: list[str] = Field(default_factory=list)


class RainmakerPossessionCue(BaseModel):
    """Language-neutral interpretation of a direct Rainmaker HUD message."""

    cue: Literal[
        "ally_acquired_rainmaker",
        "opponent_acquired_rainmaker",
        "ally_lost_rainmaker",
        "opponent_lost_rainmaker",
        "rainmaker_reset",
    ]
    template_score: float = Field(ge=0, le=1)
    language: Literal["en", "ja"]
    template_name: str


class RainmakerPossessionReading(BaseModel):
    """Per-frame direct message observation, before temporal grouping."""

    kind: Literal["rainmaker_possession"] = "rainmaker_possession"
    cue: RainmakerPossessionCue
    timestamp: float | None = None


class RainmakerCheckpointReading(BaseModel):
    """Direct localized checkpoint-breakthrough message on one frame."""

    kind: Literal["rainmaker_checkpoint"] = "rainmaker_checkpoint"
    team: Literal["ally", "opponent"]
    language: Literal["en", "ja"]
    template_name: str
    template_score: float = Field(ge=0, le=1)
    timestamp: float | None = None


class TowerMarkerReading(BaseModel):
    """Direct marker location within the configured HUD lane."""

    center_x: float = Field(ge=0, le=1)
    lane_position: float = Field(ge=0, le=1)
    bbox: tuple[float, float, float, float]
    appearance: Literal["colored_presentation", "neutral_like", "unknown"] = "unknown"
    hue: float | None = Field(default=None, ge=0, lt=180)
    hue_concentration: float = Field(default=0, ge=0, le=1)
    usable: bool = False


class TowerControlCue(BaseModel):
    """Directly observed Tower acquisition or loss message."""

    cue_type: Literal["ally_took", "opponent_took", "ally_lost", "opponent_lost"]
    team: Literal["ally", "opponent"]
    template_score: float = Field(ge=0, le=1)
    language: Literal["en", "ja"] | None = None


class TowerControlReading(BaseModel):
    """Per-frame Tower HUD observations; no movement or gameplay inference."""

    kind: Literal["tower_control"] = "tower_control"
    score_pods: list[TowerScorePodReading] = Field(default_factory=list)
    marker: TowerMarkerReading | None = None
    checkpoint_at_cue_score: float | None = Field(default=None, ge=0, le=1)
    checkpoint_passed_cue_score: float | None = Field(default=None, ge=0, le=1)
    control_cue: TowerControlCue | None = None
    timestamp: float | None = None


class ZoneControlReading(BaseModel):
    """Per-frame visual observation of the Splat Zones control highlight.

    ``*_signal`` is the lit-pixel fraction of each pod; ``*_dark`` is the
    dark-pixel fraction. ``*_pod`` is the per-pod classification.
    """

    kind: Literal["zone_control"] = "zone_control"
    observed_state: ZoneControlState = "unknown"
    left_signal: float = Field(default=0.0, ge=0, le=1)
    right_signal: float = Field(default=0.0, ge=0, le=1)
    left_dark: float = Field(default=0.0, ge=0, le=1)
    right_dark: float = Field(default=0.0, ge=0, le=1)
    left_pod: Literal["lit", "dim", "ambiguous"] = "ambiguous"
    right_pod: Literal["lit", "dim", "ambiguous"] = "ambiguous"
    confidence: float = Field(default=0.0, ge=0, le=1)


GameLanguageGate = Literal["intro_mode", "gameplay_callout"]
GameLanguageRejection = Literal["below_threshold", "ambiguous_margin", "no_templates"]


class GameLanguageCueReading(BaseModel):
    """One both-language localized cue match on one probe frame.

    ``candidate_language`` is the best-scoring language whether or not it was
    accepted; only ``accepted`` readings are language evidence. A missing or
    unreadable cue is never evidence for any language.
    """

    kind: Literal["game_language"] = "game_language"
    gate: GameLanguageGate
    candidate_language: Literal["en", "ja"] | None = None
    cue_id: str | None = None
    template_path: str | None = None
    score: float = Field(default=0.0, ge=-1, le=1)
    runner_up_language: Literal["en", "ja"] | None = None
    runner_up_score: float | None = Field(default=None, ge=-1, le=1)
    accepted: bool = False
    rejection: GameLanguageRejection | None = None


Reading = Annotated[
    TimerReading
    | DeathReading
    | SplatReading
    | RespawnReading
    | ActiveGameplayReading
    | MapOverlayReading
    | MatchIntroReading
    | ReadyReading
    | LowInkReading
    | PlayerCountReading
    | SpecialGaugeReading
    | ScoreReading
    | ClamBasketStateReading
    | TowerControlReading
    | RainmakerScoreReading
    | RainmakerPossessionReading
    | RainmakerCheckpointReading
    | ZoneControlReading
    | GameLanguageCueReading,
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


class BasketTeamObservation(BaseModel):
    """Team association derived from a state reading and color calibration."""

    state_result_id: str
    scoring_team: Literal["ally", "opponent", "unknown"] = "unknown"
    color_distance: float | None = Field(default=None, ge=0)
    calibration_time: float | None = None


class TowerTeamObservation(BaseModel):
    """Color-calibrated team association for a Tower detector result."""

    result_id: str
    control_presentation: TowerControlPresentation = "unknown"
    color_distance: float | None = Field(default=None, ge=0)
    calibration_time: float | None = None


class TowerOpponentLostSample(BaseModel):
    """A directly detected opponent-lost cue and canonical frame provenance."""

    video_time: float = Field(ge=0)
    source_frame: SourceFrameReference
    evidence_id: str = Field(min_length=1)
    language: Literal["en", "ja"] | None = None


class TowerOpponentLostObservation(BaseModel):
    """One candidate display; first_video_time is its observation timestamp."""

    cue: Literal["opponent_lost_tower"] = "opponent_lost_tower"
    first_video_time: float = Field(ge=0)
    last_video_time: float = Field(ge=0)
    max_gap_seconds: float = Field(gt=0)
    samples: list[TowerOpponentLostSample] = Field(min_length=1)


class TowerTookSample(BaseModel):
    """A directly detected Tower acquisition cue with canonical provenance."""

    video_time: float = Field(ge=0)
    source_frame: SourceFrameReference
    evidence_id: str = Field(min_length=1)
    cue: TowerControlCue


class TowerTookObservation(BaseModel):
    """One displayed acquisition cue; its first sample is the observation time."""

    cue: Literal["ally_took_tower", "opponent_took_tower"]
    first_video_time: float = Field(ge=0)
    last_video_time: float = Field(ge=0)
    max_gap_seconds: float = Field(gt=0)
    samples: list[TowerTookSample] = Field(min_length=1)


class RainmakerPossessionSample(BaseModel):
    """One detected message frame with timestamp and detector provenance."""

    video_time: float = Field(ge=0)
    source_frame: SourceFrameReference
    evidence_id: str = Field(min_length=1)
    cue: RainmakerPossessionCue


class RainmakerPossessionObservation(BaseModel):
    """One displayed Rainmaker message; time is first accepted evidence time."""

    cue: Literal[
        "ally_acquired_rainmaker",
        "opponent_acquired_rainmaker",
        "ally_lost_rainmaker",
        "opponent_lost_rainmaker",
        "rainmaker_reset",
    ]
    first_video_time: float = Field(ge=0)
    last_video_time: float = Field(ge=0)
    max_gap_seconds: float = Field(gt=0)
    samples: list[RainmakerPossessionSample] = Field(min_length=1)


class RainmakerPossessionEvidence(BaseModel):
    """Provenance for a currently held Rainmaker possession fact."""

    state: Literal["neutral", "ally_possession", "opponent_possession"]
    video_time: float = Field(ge=0)
    source_frame: SourceFrameReference
    detector_evidence_ids: list[str] = Field(default_factory=list)
    cue_observation: RainmakerPossessionObservation | None = None
    initialization: Literal["observed_match_start"] | None = None


class RainmakerCheckpointSample(BaseModel):
    """A directly detected milestone with canonical decoder provenance."""

    video_time: float = Field(ge=0)
    source_frame: SourceFrameReference
    evidence_id: str = Field(min_length=1)
    reading: RainmakerCheckpointReading


class RainmakerCheckpointObservation(BaseModel):
    """One displayed breakthrough message, timed by first accepted evidence."""

    team: Literal["ally", "opponent"]
    first_video_time: float = Field(ge=0)
    last_video_time: float = Field(ge=0)
    max_gap_seconds: float = Field(gt=0)
    samples: list[RainmakerCheckpointSample] = Field(min_length=1)


class RainmakerCheckpointState(BaseModel):
    """Cleared facts supported only by observed breakthrough messages."""

    ally: Literal["cleared", "unknown"] = "unknown"
    opponent: Literal["cleared", "unknown"] = "unknown"
    ally_evidence: RainmakerCheckpointObservation | None = None
    opponent_evidence: RainmakerCheckpointObservation | None = None


class TowerControlEvidence(BaseModel):
    """Direct cue and source establishing the current Tower control state."""

    video_time: float = Field(ge=0)
    source_frame: SourceFrameReference
    detector_evidence_id: str
    cue: TowerControlCue
    opponent_lost_observation: TowerOpponentLostObservation | None = None
    took_observation: TowerTookObservation | None = None


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
    # None means legacy or otherwise unavailable scheduling provenance.
    basket_observation_scheduled: bool | None = None
    basket_team: BasketTeamObservation | None = None
    tower_team: TowerTeamObservation | None = None
    rainmaker_score_association: RainmakerScoreAssociation | None = None


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
    # True/False = usable low-ink evidence; None = no usable observation.
    low_ink_present: bool | None = None
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
    # Fused SZ remainings: left→ally / right→opponent (fusion interpretation).
    # Quality is per-side — a frame may be observed on one side and held on the other.
    # Only populated when the match's battle mode is Splat Zones.
    ally_remaining: int | None = None
    opponent_remaining: int | None = None
    ally_score_quality: ScoreQuality = "unknown"
    opponent_score_quality: ScoreQuality = "unknown"
    # Fused SZ ``+N`` penalties, tracked separately from the remaining counts.
    ally_penalty: int | None = None
    opponent_penalty: int | None = None
    ally_penalty_quality: PenaltyQuality = "unknown"
    opponent_penalty_quality: PenaltyQuality = "unknown"
    # Fused SZ ownership state; absent outside Splat Zones or without evidence.
    zone_control_state: ZoneControlState = "unknown"
    zone_control_quality: ZoneControlQuality = "unknown"
    basket_state: Literal["open", "closed", "unknown"] = "unknown"
    basket_state_evidence_ids: list[str] = Field(default_factory=list)
    # Fresh per-frame Clam score; never reused by the Splat Zones fuser.
    clam_score: ClamScoreObservation | None = None
    # Direct Tower HUD evidence. No hold and no movement interpretation.
    tower_score_pods: list[TowerScorePodReading] = Field(default_factory=list)
    rainmaker_score_reading: RainmakerScoreReading | None = None
    rainmaker_score_association: RainmakerScoreAssociation | None = None
    rainmaker_ally_remaining: int | None = None
    rainmaker_opponent_remaining: int | None = None
    rainmaker_score_evidence_ids: list[str] = Field(default_factory=list)
    rainmaker_possession_state: Literal[
        "unknown", "neutral", "ally_possession", "opponent_possession"
    ] = "unknown"
    rainmaker_possession_evidence: RainmakerPossessionEvidence | None = None
    rainmaker_checkpoint_state: RainmakerCheckpointState | None = None
    tower_marker: TowerMarkerReading | None = None
    tower_control_presentation: TowerControlPresentation = "unknown"
    tower_evidence_ids: list[str] = Field(default_factory=list)
    tower_checkpoint_identity: int | None = Field(default=None, ge=1, le=3)
    tower_checkpoint_state: TowerCheckpointState | None = None
    tower_control_state: Literal[
        "ally_control", "opponent_control", "returning", "unknown"
    ] = "unknown"
    tower_control_evidence: TowerControlEvidence | None = None
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
    from_zone_control: ZoneControlState | None = None
    to_zone_control: ZoneControlState | None = None
    splat_fingerprint: str | None = None
    # MATCH_END only: secondary visual confirmation (no detector yet).
    confirmation: Literal["finish_screen"] | None = None
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    source_frames: list[SourceFrameReference] = Field(default_factory=list)


# Manifests written before ``AnalysisIdentity.language`` existed (commit
# d2b3841, 2026-09-08) ran with English templates: Japanese templates entered
# the repository in that same commit. Read-side compatibility only.
LEGACY_ANALYSIS_LANGUAGE: Literal["en"] = "en"


class AnalysisIdentity(BaseModel):
    """Reproducible analysis provenance."""

    analysis_id: str
    pipeline_version: str = PIPELINE_VERSION
    package_version: str
    # Run provenance only. Never part of analysis or evidence IDs.
    git_sha: str | None = None
    detector_versions: dict[str, str] = Field(default_factory=dict)
    # Empty for cadence-only analysis (no extraction manifest).
    extraction_manifest_sha256: str = ""
    vision_config_sha256: str
    video_identity: str
    # Game language that selected detector templates (the detected language
    # since automatic detection); also hashed into vision_config_sha256.
    # Required: only VisionManifest's legacy reader fills it for old artifacts.
    language: Literal["en", "ja"]
    # Set when state_snapshots / game_events were re-fused from the stored
    # frame_results: hash of the config whose fusion and event settings produced
    # them. vision_config_sha256 still describes the run that made the readings.
    refused_vision_config_sha256: str | None = None


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
    ready_detector_seconds: float = Field(default=0.0, ge=0)
    match_intro_detector_seconds: float = Field(default=0.0, ge=0)
    timer_detector_invocations: int = Field(default=0, ge=0)
    death_detector_invocations: int = Field(default=0, ge=0)
    splat_detector_invocations: int = Field(default=0, ge=0)
    respawn_detector_invocations: int = Field(default=0, ge=0)
    active_gameplay_detector_invocations: int = Field(default=0, ge=0)
    map_overlay_detector_invocations: int = Field(default=0, ge=0)
    player_count_detector_invocations: int = Field(default=0, ge=0)
    special_gauge_detector_invocations: int = Field(default=0, ge=0)
    ready_detector_invocations: int = Field(default=0, ge=0)
    match_intro_detector_invocations: int = Field(default=0, ge=0)
    temporal_seconds: float = Field(ge=0)
    total_seconds: float = Field(ge=0)

    @computed_field
    @property
    def realtime_factor(self) -> float:
        """Video duration divided by total analysis time."""
        if self.total_seconds <= 0:
            return 0.0
        return self.video_duration_seconds / self.total_seconds


class GameLanguageGateSummary(BaseModel):
    """Counts and time span of one probe gate's observations (no per-frame trace).

    ``status`` is closed vocabulary: ``accepted``; ``unreadable`` (every
    observation below threshold, or none observed); ``ambiguous`` (at least one
    observation failed only the language margin); ``not_attempted``.
    """

    attempted: bool = False
    observations: int = Field(default=0, ge=0)
    first_time: float | None = None
    last_time: float | None = None
    span_seconds: float = Field(default=0.0, ge=0)
    below_threshold: int = Field(default=0, ge=0)
    ambiguous_margin: int = Field(default=0, ge=0)
    no_templates: int = Field(default=0, ge=0)
    best_score: float | None = None
    status: Literal["accepted", "unreadable", "ambiguous", "not_attempted"] = (
        "not_attempted"
    )


class GameLanguageIntroSummary(GameLanguageGateSummary):
    """Gate 1 (intro battle-mode label) summary."""

    close_reason: (
        Literal["accepted", "in_match", "deadline", "disabled", "video_ended"] | None
    ) = None


class GameLanguageFallbackSummary(GameLanguageGateSummary):
    """Gate 2 (in-match D-pad signal label) summary.

    ``observations`` counts eligible frames (in-match with a usable timer read);
    ``span_seconds`` is the video time from the first to the last of them.
    """

    entered: bool = False
    entered_at: float | None = None
    bound_reached: Literal["observations", "live_seconds", "no_live_frames"] | None = (
        None
    )


class GameLanguagePolicy(BaseModel):
    """Effective probe thresholds and bounds for one run."""

    intro_roi: tuple[float, float, float, float]
    intro_threshold: float
    intro_language_margin: float
    intro_deadline_seconds: float
    callout_roi: tuple[float, float, float, float]
    callout_cues: list[str]
    callout_threshold: float
    callout_language_margin: float
    max_fallback_observations: int
    max_fallback_live_seconds: float
    max_seconds_before_live: float
    sample_fps: float
    languages: list[str]


class GameLanguageResult(BaseModel):
    """Authoritative game language for one analysis and its establishing evidence.

    ``language`` is the only language that selects detector templates; there
    is no supplied game language. Phase 1 artifacts may still carry
    ``supplied_language`` / ``supplied_agreement``; they are ignored on load.
    Detailed per-frame attempts live in ``game_language.json``.
    """

    language: Literal["en", "ja", "unknown"]
    source: Literal["intro_mode", "gameplay_callout", "none"]
    termination: Literal[
        "intro_accepted", "callout_accepted", "fallback_limit", "video_ended"
    ]
    established_at: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    evidence: DetectorResult | None = None
    source_frame: SourceFrameReference | None = None
    intro: GameLanguageIntroSummary = Field(default_factory=GameLanguageIntroSummary)
    fallback: GameLanguageFallbackSummary = Field(
        default_factory=GameLanguageFallbackSummary
    )
    video_ended_before_resolution: bool = False
    policy: GameLanguagePolicy


class VisionManifest(BaseModel):
    """Versioned output of the Phase 3 vision pipeline."""

    schema_version: int = 1
    analysis: AnalysisIdentity
    video_identity: str
    extraction_manifest_path: str | None = None
    frame_results: list[VisionFrameResult] = Field(default_factory=list)
    tower_opponent_lost_observations: list[TowerOpponentLostObservation] = Field(
        default_factory=list
    )
    tower_took_observations: list[TowerTookObservation] = Field(default_factory=list)
    rainmaker_possession_observations: list[RainmakerPossessionObservation] = Field(
        default_factory=list
    )
    rainmaker_checkpoint_observations: list[RainmakerCheckpointObservation] = Field(
        default_factory=list
    )
    state_snapshots: list[GameStateSnapshot] = Field(default_factory=list)
    game_events: list[GameEvent] = Field(default_factory=list)
    timing: VisionTimingMetrics | None = None
    video: VideoRunMetadata | None = None
    # None for manifests written before automatic game-language detection.
    game_language: GameLanguageResult | None = None

    @model_validator(mode="before")
    @classmethod
    def _fill_legacy_analysis_language(cls, data: Any) -> Any:
        """Give stored manifests that predate ``analysis.language`` their language.

        Applies only to a raw ``analysis`` mapping without the key, i.e. a
        manifest written before ``AnalysisIdentity.language`` existed; analysis
        always records the detected language explicitly.
        """
        if isinstance(data, dict):
            analysis = data.get("analysis")
            if isinstance(analysis, dict) and "language" not in analysis:
                analysis = {**analysis, "language": LEGACY_ANALYSIS_LANGUAGE}
                return {**data, "analysis": analysis}
        return data
