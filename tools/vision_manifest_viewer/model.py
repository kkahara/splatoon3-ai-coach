"""Normalized view-model types for the vision manifest viewer."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MarkerCategory = Literal[
    "death",
    "countdown",
    "splat",
    "active_gameplay",
    "spawn_view",
    "timer",
    "lifecycle",
    "other",
]

LayerName = Literal["observation", "fused", "event"]

CausalLayer = Literal["detector", "lifecycle", "event"]

DeathDecision = Literal["confirmed", "rejected", "suppressed", "near_miss"]

GroundTruthLabel = Literal[
    "unknown",
    "real_death",
    "not_a_death",
    "damage_recovery",
    "results_screen",
    "lobby",
]


class RoiBox(BaseModel):
    """Normalized ROI box for overlay/zoom."""

    x1: float
    y1: float
    x2: float
    y2: float
    label: str = "roi"


class EvidenceCheck(BaseModel):
    """One required-evidence line in a transition explanation."""

    ok: bool
    label: str


class CueBar(BaseModel):
    """One scored cue in the Death Diagnostic panel."""

    label: str
    value: float | None = None
    threshold: float | None = None
    passed: bool | None = None
    detail: str = ""


class CausalStep(BaseModel):
    """One step in Detector → Lifecycle → Event explanation."""

    layer: CausalLayer
    ok: bool
    title: str
    detail: str
    reason: str = ""


class DeathDiagnostic(BaseModel):
    """Per-death-observation diagnostic for the debugger UI."""

    id: str
    observation_id: str
    timestamp: float
    cues: list[CueBar] = Field(default_factory=list)
    rule_lines: list[EvidenceCheck] = Field(default_factory=list)
    causal: list[CausalStep] = Field(default_factory=list)
    decision: DeathDecision = "rejected"
    decision_reason: str = ""
    flags: list[str] = Field(default_factory=list)
    event_emitted: bool = False
    event_timestamp: float | None = None
    lifecycle_phase: str | None = None


class TransitionExplanation(BaseModel):
    """Why the lifecycle advanced at a given timestamp."""

    id: str
    timestamp: float
    event_type: str | None = None
    from_phase: str | None = None
    to_phase: str | None = None
    title: str
    checks: list[EvidenceCheck] = Field(default_factory=list)
    player_alive: bool | None = None
    latch: bool | None = None
    meaning: str = ""
    observation_id: str | None = None
    anomaly: bool = False
    anomaly_message: str | None = None


class LatchPoint(BaseModel):
    """Latch value at a notable episode moment."""

    timestamp: float
    value: bool
    label: str
    observation_id: str | None = None


class DetectorSample(BaseModel):
    """One detector sample on the detector timeline."""

    timestamp: float
    positive: bool
    confidence: float | None = None
    observation_id: str | None = None
    score: float | None = None


class DetectorLane(BaseModel):
    """Per-detector row for the detector timeline."""

    detector: str
    label: str
    samples: list[DetectorSample] = Field(default_factory=list)


class NearMiss(BaseModel):
    """Borderline / rejected observation useful for calibration."""

    id: str
    timestamp: float
    kind: str
    summary: str
    observation_id: str | None = None
    score: float | None = None
    detail: str = ""


class ObservationView(BaseModel):
    """One selectable evidence observation (frame + optional detection)."""

    id: str
    timestamp: float
    frame_index: int | None = None
    frame_id: str | None = None
    frame_path: str | None = None
    image_relpath: str | None = None
    detector: str | None = None
    category: MarkerCategory = "other"
    confidence: float | None = None
    positive: bool = False
    reading: dict[str, Any] = Field(default_factory=dict)
    reading_kind: str | None = None
    roi: RoiBox | None = None
    lifecycle: str | None = None
    player_alive: bool | None = None
    ally_alive_count: int | None = None
    opponent_alive_count: int | None = None
    countdown_present: bool | None = None
    active_gameplay: bool | None = None
    match_phase: str | None = None
    latch: bool | None = None
    source: str | None = None
    layer: LayerName = "observation"
    transition_id: str | None = None
    diagnostic_id: str | None = None
    diagnostic_flags: list[str] = Field(default_factory=list)


class TimelineMarker(BaseModel):
    """A point on the timeline."""

    id: str
    timestamp: float
    category: MarkerCategory
    label: str
    observation_id: str | None = None
    confidence: float | None = None
    transition_id: str | None = None


class LifecycleSegment(BaseModel):
    """A contiguous lifecycle phase on the lifecycle lane."""

    phase: str
    start: float
    end: float


class LifecycleTransitionMark(BaseModel):
    """Labeled lifecycle transition on the top lifecycle strip."""

    timestamp: float
    phase_label: str
    event_label: str
    transition_id: str | None = None


class EpisodeStep(BaseModel):
    """One step inside a death/respawn episode."""

    timestamp: float
    label: str
    detail: str = ""
    observation_id: str | None = None
    kind: str = "note"
    transition_id: str | None = None


class DeathEpisode(BaseModel):
    """Grouped death → countdown → respawn → active-again episode."""

    index: int
    start: float
    end: float | None = None
    steps: list[EpisodeStep] = Field(default_factory=list)
    latch_points: list[LatchPoint] = Field(default_factory=list)
    has_countdown: bool = False
    has_respawn: bool = False
    has_active_again: bool = False
    warning: str | None = None
    completeness: dict[str, bool] = Field(default_factory=dict)
    completeness_score: int = 0
    completeness_total: int = 4


class ManifestWarning(BaseModel):
    """Diagnostic warning for the summary header."""

    level: Literal["info", "warn", "ok"] = "warn"
    message: str


class ManifestSummary(BaseModel):
    """Compact diagnostic summary."""

    video_label: str
    video_identity: str
    duration_seconds: float
    frame_count: int
    detection_count: int
    death_detections: int
    countdown_observations: int
    splat_detections: int
    active_observations: int
    spawn_observations: int
    lifecycle_episodes: int
    event_counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[ManifestWarning] = Field(default_factory=list)
    detector_positive_event_negative: int = 0
    death_confirmed: int = 0
    death_rejected: int = 0
    death_suppressed: int = 0


class ScenarioEvidenceView(BaseModel):
    """Joined Scenario metadata plus ScenarioContext facts for display."""

    scenario_id: str
    scenario_type: str = ""
    start_time: float | None = None
    end_time: float | None = None
    outcome: str | None = None
    event_ids: list[str] = Field(default_factory=list)
    following_death_id: str | None = None
    timeline: dict[str, Any] | None = None
    map: dict[str, Any] | None = None
    combat: dict[str, Any] | None = None
    recovery: dict[str, Any] | None = None
    relations: dict[str, Any] | None = None


class RosterSampleView(BaseModel):
    """Authoritative fused roster counts from one state_snapshot."""

    video_time: float
    ally_alive_count: int
    opponent_alive_count: int


class ManifestView(BaseModel):
    """Normalized viewer payload derived from a vision manifest."""

    summary: ManifestSummary
    observations: list[ObservationView] = Field(default_factory=list)
    roster_timeline: list[RosterSampleView] = Field(default_factory=list)
    markers: list[TimelineMarker] = Field(default_factory=list)
    lifecycle_segments: list[LifecycleSegment] = Field(default_factory=list)
    lifecycle_marks: list[LifecycleTransitionMark] = Field(default_factory=list)
    detector_lanes: list[DetectorLane] = Field(default_factory=list)
    transitions: list[TransitionExplanation] = Field(default_factory=list)
    near_misses: list[NearMiss] = Field(default_factory=list)
    episodes: list[DeathEpisode] = Field(default_factory=list)
    death_diagnostics: list[DeathDiagnostic] = Field(default_factory=list)
    detectors: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    default_rois: dict[str, RoiBox] = Field(default_factory=dict)
    lifecycle_config: dict[str, int | float | bool] = Field(default_factory=dict)
    death_thresholds: dict[str, float] = Field(default_factory=dict)
    analysis_dir: str = ""
    manifest_path: str = ""
    scenario_evidence: list[ScenarioEvidenceView] = Field(default_factory=list)
    review_video_url: str = ""
    ground_truth_labels: list[str] = Field(
        default_factory=lambda: [
            "unknown",
            "real_death",
            "not_a_death",
            "damage_recovery",
            "results_screen",
            "lobby",
        ]
    )
