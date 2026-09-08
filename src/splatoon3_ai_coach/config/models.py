"""Configuration schema for the whole application."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from splatoon3_ai_coach.types import NormalizedBox


class VisionLanguage(StrEnum):
    """UI / OCR language for language-dependent vision assets.

    Selected once per analysis via ``VisionConfig.language``. Detectors must
    not infer or mutate language from frames, OCR, or template matches.
    Text templates load only from ``template_dir/<language>/``.
    """

    EN = "en"
    JA = "ja"


class VideoConfig(BaseModel):
    """Limits applied to decoded frames before analysis."""

    max_width: int = Field(gt=0)
    max_height: int = Field(gt=0)


class PathsConfig(BaseModel):
    """Default output locations, used when the CLI is not given explicit ones."""

    frame_output: Path
    manifest_output: Path


class HudRegions(BaseModel):
    """Normalized HUD regions, each as x1, y1, x2, y2 in [0, 1]."""

    killfeed: NormalizedBox
    special_gauge: NormalizedBox
    objective_timer: NormalizedBox
    death_text: NormalizedBox

    @field_validator("*")
    @classmethod
    def _check_box(cls, box: NormalizedBox) -> NormalizedBox:
        x1, y1, x2, y2 = box
        if not all(0.0 <= value <= 1.0 for value in box):
            raise ValueError(f"region coordinates must be in [0, 1]: {box}")
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"region must have positive area: {box}")
        return box


class ExtractionConfig(BaseModel):
    """Settings controlling meaningful-frame extraction."""

    analysis_fps: float = Field(gt=0)
    scene_threshold: float = Field(gt=0)
    ssim_threshold: float = Field(gt=0, le=1)
    motion_threshold: float = Field(gt=0)
    hud_threshold: float = Field(gt=0)
    min_event_gap_seconds: float = Field(ge=0)
    context_before_seconds: float = Field(ge=0)
    context_after_seconds: float = Field(ge=0)
    max_frames_per_minute: int = Field(gt=0)
    save_jpeg_quality: int = Field(ge=1, le=100)
    hud: HudRegions

    @property
    def analysis_step_seconds(self) -> float:
        """Minimum spacing between frames handed to the change triggers."""
        return 1.0 / self.analysis_fps


class DeathDetectorConfig(BaseModel):
    """Settings for bottom-HUD death-screen detection."""

    ouch_roi: NormalizedBox = (20 / 1920, 880 / 1080, 200 / 1920, 940 / 1080)
    banner_roi: NormalizedBox = (660 / 1920, 980 / 1080, 1260 / 1920, 1050 / 1080)
    template_dir: Path | None = None
    # Real deaths sit ~0.95–0.98; white-heuristic FPs ~0.38–0.45.
    ouch_match_threshold: float = Field(default=0.70, ge=0, le=1)
    banner_match_threshold: float = Field(default=0.55, ge=0, le=1)
    # When True (default), white-pixel heuristic alone cannot assert Ouch.
    ouch_require_glyph: bool = True
    # Diagnostic / legacy support only when ouch_require_glyph is False.
    ouch_white_ratio: float = Field(default=0.12, ge=0, le=1)
    ouch_max_saturation: float = Field(default=0.30, ge=0, le=1)
    ouch_max_yellow_ratio: float = Field(default=0.15, ge=0, le=1)
    # Supporting cue only — not tuned as a solo death separator.
    banner_dark_threshold: float = Field(default=0.72, ge=0, le=1)
    debounce_seconds: float = Field(default=3.0, gt=0)
    min_usable_confidence: float = Field(default=0.50, ge=0, le=1)

    @field_validator("ouch_roi", "banner_roi")
    @classmethod
    def _check_box(cls, box: NormalizedBox) -> NormalizedBox:
        x1, y1, x2, y2 = box
        if not all(0.0 <= value <= 1.0 for value in box):
            raise ValueError(f"region coordinates must be in [0, 1]: {box}")
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"region must have positive area: {box}")
        return box


class SplatDetectorConfig(BaseModel):
    """Settings for bottom-center local-kill banner detection."""

    # 1080p: tall enough for a 3-stack local-kill banner, not the kill-feed.
    banner_roi: NormalizedBox = (0.35, 0.80, 0.65, 0.98)
    template_dir: Path | None = None
    # Real local-kill icons ~0.95; kill-feed skulls in the same ROI ~0.60–0.70.
    skull_match_threshold: float = Field(default=0.80, ge=0, le=1)
    # 「をたおした」/Splatted ~0.99 on real banners; kill-feed names ~0.30–0.50.
    text_match_threshold: float = Field(default=0.70, ge=0, le=1)
    white_value_threshold: int = Field(default=230, ge=0, le=255)
    # Diagnostic only — not required to trip detected (kill-feed is very colorful).
    adjacent_color_min_ratio: float = Field(default=0.20, ge=0, le=1)
    adjacent_min_saturation: int = Field(default=50, ge=0, le=255)
    # Last-resort anti-glitch only. Must not define splat incidents.
    debounce_seconds: float = Field(default=0.0, ge=0)
    min_usable_confidence: float = Field(default=0.50, ge=0, le=1)

    @field_validator("banner_roi")
    @classmethod
    def _check_box(cls, box: NormalizedBox) -> NormalizedBox:
        x1, y1, x2, y2 = box
        if not all(0.0 <= value <= 1.0 for value in box):
            raise ValueError(f"region coordinates must be in [0, 1]: {box}")
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"region must have positive area: {box}")
        return box


class RespawnDetectorConfig(BaseModel):
    """Settings for bottom-right respawn/waiting-UI detection."""

    # BR “Respawn in N” / 「復活まであと」 (1080p EN ~y=0.83; JA ~y=0.90).
    roi: NormalizedBox = (0.840, 0.825, 1.000, 0.950)
    template_dir: Path | None = None
    match_threshold: float = Field(default=0.60, ge=0, le=1)
    # Supporting: structure-ok + near-miss template when primary is missed.
    support_match_threshold: float = Field(default=0.55, ge=0, le=1)
    dark_frac_min: float = Field(default=0.25, ge=0, le=1)
    bright_frac_min: float = Field(default=0.08, ge=0, le=1)
    p95_min: float = Field(default=0.75, ge=0, le=1)
    # Color enrichment only; never solo-trips detected=True when templates exist.
    yellow_frac_digit_box_min: float = Field(default=0.12, ge=0, le=1)
    min_usable_confidence: float = Field(default=0.50, ge=0, le=1)
    # Prefer 0 so lifecycle can count consecutive observations.
    debounce_seconds: float = Field(default=0.0, ge=0)
    # Keep detected=True after the plate drops so landing frames still count.
    # Lifecycle treats evidence_type="hold" as countdown absent.
    hold_seconds: float = Field(default=2.0, ge=0)

    @field_validator("roi")
    @classmethod
    def _check_box(cls, box: NormalizedBox) -> NormalizedBox:
        x1, y1, x2, y2 = box
        if not all(0.0 <= value <= 1.0 for value in box):
            raise ValueError(f"region coordinates must be in [0, 1]: {box}")
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"region must have positive area: {box}")
        return box


class ActiveGameplayDetectorConfig(BaseModel):
    """HUD/weapon/center evidence thresholds. Not match-phase policy."""

    # Lower-center band where the third-person weapon / ink volume sits.
    weapon_roi: NormalizedBox = (0.32, 0.48, 0.68, 0.90)
    # Bottom-left D-pad / communication HUD chrome.
    hud_roi: NormalizedBox = (0.01, 0.78, 0.22, 0.98)
    # Center ink-tank / reticle strip (return_control latch, not detected).
    center_roi: NormalizedBox = (0.46, 0.40, 0.54, 0.62)
    weapon_edge_min: float = Field(default=0.035, ge=0, le=1)
    weapon_luma_std_min: float = Field(default=0.08, ge=0, le=1)
    hud_edge_min: float = Field(default=0.020, ge=0, le=1)
    center_edge_min: float = Field(default=0.045, ge=0, le=1)
    min_usable_confidence: float = Field(default=0.50, ge=0, le=1)
    debounce_seconds: float = Field(default=0.0, ge=0)

    @field_validator("weapon_roi", "hud_roi", "center_roi")
    @classmethod
    def _check_box(cls, box: NormalizedBox) -> NormalizedBox:
        x1, y1, x2, y2 = box
        if not all(0.0 <= value <= 1.0 for value in box):
            raise ValueError(f"region coordinates must be in [0, 1]: {box}")
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"region must have positive area: {box}")
        return box


class MapOverlayDetectorConfig(BaseModel):
    """Settings for map-viewing detection (independent coaching evidence)."""

    # Top-left close “X” in a circle (1080p ~[27:341, 19:182]).
    map_roi: NormalizedBox = (0.0138889, 0.0171875, 0.1777778, 0.16875)
    # Optional diagnostics only — not used to assert present.
    tank_roi: NormalizedBox = (0.46, 0.40, 0.54, 0.62)
    periphery_roi: NormalizedBox = (0.02, 0.02, 0.98, 0.98)
    template_dir: Path | None = None
    match_threshold: float = Field(default=0.70, ge=0, le=1)
    map_edge_min: float = Field(default=0.045, ge=0, le=1)
    tank_edge_max: float = Field(default=0.055, ge=0, le=1)
    periphery_blur_min: float = Field(default=0.55, ge=0, le=1)
    min_usable_confidence: float = Field(default=0.50, ge=0, le=1)
    debounce_seconds: float = Field(default=0.0, ge=0)

    @field_validator("map_roi", "tank_roi", "periphery_roi")
    @classmethod
    def _check_box(cls, box: NormalizedBox) -> NormalizedBox:
        x1, y1, x2, y2 = box
        if not all(0.0 <= value <= 1.0 for value in box):
            raise ValueError(f"region coordinates must be in [0, 1]: {box}")
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"region must have positive area: {box}")
        return box


class LifecycleFusionConfig(BaseModel):
    """Player lifecycle + match-phase fusion (authoritative states)."""

    countdown_present_min_observations: int = Field(default=2, ge=1)
    countdown_absent_min_observations: int = Field(default=2, ge=1)
    # Unused: RESPAWNED now arms awaiting_control directly, since the plate
    # ending already means control has not returned. Kept for config compat.
    awaiting_control_absent_min_observations: int = Field(default=2, ge=1)
    # Positives needed to exit awaiting_control → alive (ACTIVE_AGAIN).
    active_again_min_observations: int = Field(default=3, ge=1)
    # Allowed consecutive control-absent frames without clearing the present
    # streak (0 = strict consecutive). One gap absorbs HUD flicker.
    active_again_max_gap_observations: int = Field(default=1, ge=0)
    # HUD+timer mid-tier cue is ignored until this many seconds after the
    # latch arms, so spawn-plate HUD chrome cannot ACTIVE_AGAIN at ~1s.
    # Strict weapon+center return_control ignores this delay.
    active_again_mid_tier_delay_seconds: float = Field(default=2.5, ge=0)
    # Water / wipeout deaths skip the Respawn-in-N plate. After this delay,
    # sustained control evidence may leave DEAD without countdown. Must stay
    # longer than a normal death-cam (plate usually appears by ~3s).
    dead_skip_countdown_min_seconds: float = Field(default=6.5, ge=0)
    dead_skip_countdown_min_observations: int = Field(default=3, ge=1)
    # Elapsed analysis time since confirmed DEATH → UNKNOWN only.
    max_respawn_observation_seconds: float = Field(default=30.0, gt=0)
    stale_to_unknown: bool = True
    # Ignore DeathReading unless match_phase is in_match.
    death_requires_match_context: bool = True
    # Hold in_match (and death eligibility) after the last accepted timer.
    match_context_hold_seconds: float = Field(default=20.0, gt=0)
    # Frozen spawn clock before GO (Turf 3:00 / Ranked 5:00).
    opening_clock_seconds: tuple[int, ...] = (180, 300)


class TimerDetectorConfig(BaseModel):
    """Settings for calibrated timer template matching."""

    roi: NormalizedBox
    template_dir: Path
    match_threshold: float = Field(default=0.55, ge=0, le=1)
    min_usable_confidence: float = Field(default=0.50, ge=0, le=1)


class StateFusionConfig(BaseModel):
    """Settings for temporal state fusion."""

    smoothing_window: int = Field(default=5, ge=1)
    max_hold_duration: float = Field(default=2.0, gt=0)
    dedupe_tolerance_seconds: float = Field(default=0.05, ge=0)


class EventFusionConfig(BaseModel):
    """Settings for state-to-event transitions."""

    debounce_ms: int = Field(default=300, ge=0)
    # Close a splat episode after this many unmatched cadence frames.
    splat_absent_min_observations: int = Field(default=2, ge=1)
    # Hamming distance on the 64-bit banner fingerprint (same banner / animation).
    # Slot continuity still merges larger drift while a stack row stays occupied.
    splat_fingerprint_max_distance: int = Field(default=10, ge=0, le=64)
    # Normalized ROI-y distance treated as the same stack slot.
    splat_slot_nearby: float = Field(default=0.18, ge=0, le=1)


class VisionOcrConfig(BaseModel):
    """OCR engine settings keyed by :class:`VisionLanguage`.

    Primary detection should remain language-neutral. OCR is for optional
    text enrichment (e.g. future splat victim names), not for kill/death gates.
    """

    engine: str = "tesseract"
    # VisionLanguage value → engine language pack (Tesseract codes by default).
    tesseract_languages: dict[str, str] = Field(
        default_factory=lambda: {
            VisionLanguage.EN.value: "eng",
            VisionLanguage.JA.value: "jpn",
        }
    )


class VisionConfig(BaseModel):
    """Settings for the semantic vision layer (Phase 3)."""

    language: VisionLanguage = VisionLanguage.EN
    ocr: VisionOcrConfig = Field(default_factory=VisionOcrConfig)
    enabled_detectors: list[str] = Field(default_factory=lambda: ["timer"])
    timer: TimerDetectorConfig
    death: DeathDetectorConfig = Field(default_factory=DeathDetectorConfig)
    splat: SplatDetectorConfig = Field(default_factory=SplatDetectorConfig)
    respawn: RespawnDetectorConfig = Field(default_factory=RespawnDetectorConfig)
    active_gameplay: ActiveGameplayDetectorConfig = Field(
        default_factory=ActiveGameplayDetectorConfig
    )
    map_overlay: MapOverlayDetectorConfig = Field(
        default_factory=MapOverlayDetectorConfig
    )
    hud_cadence_fps: float = Field(default=2.0, gt=0)
    state_fusion: StateFusionConfig = Field(default_factory=StateFusionConfig)
    lifecycle: LifecycleFusionConfig = Field(default_factory=LifecycleFusionConfig)
    events: EventFusionConfig = Field(default_factory=EventFusionConfig)

    def tesseract_lang(self) -> str:
        """Return the OCR language pack for the active vision language."""
        code = self.ocr.tesseract_languages.get(self.language.value)
        if code:
            return code
        return self.ocr.tesseract_languages.get(VisionLanguage.EN.value, "eng")


class ScenarioBuilderConfig(BaseModel):
    """Windows for grouping GameEvents into scenarios. Not detector debounce."""

    post_death_follow_seconds: float = Field(default=8.0, ge=0)
    post_death_max_seconds: float = Field(default=30.0, gt=0)
    engagement_gap_seconds: float = Field(default=3.0, ge=0)
    engagement_include_following_death_seconds: float = Field(default=2.0, ge=0)


class CoachConfig(BaseModel):
    """Settings for the LLM coaching layer (Phase 5)."""

    provider: str = "openai"
    model: str = "gpt-4o-mini"


class AppConfig(BaseModel):
    """Top-level application configuration."""

    video: VideoConfig
    paths: PathsConfig
    extraction: ExtractionConfig
    vision: VisionConfig
    scenarios: ScenarioBuilderConfig = Field(default_factory=ScenarioBuilderConfig)
    coach: CoachConfig = Field(default_factory=CoachConfig)
