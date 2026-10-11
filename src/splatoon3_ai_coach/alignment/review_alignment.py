"""Layer 2: Review remaining clock to a gameplay video interval.

Contract: ``ALIGNMENT_CONTRACT.md``. Composition only: Review ``r`` goes
through the gameplay calibration ``video_t = c - (r + delta)`` widened by the
Review axis error. Nothing here is fitted from Review data or deaths, and no
SPECIAL_USE input is read.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from splatoon3_ai_coach.alignment.gameplay_clock import (
    GameplayClockCalibration,
    calibrate_gameplay_clock,
)
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.media.vision_manifest import (
    VISION_MANIFEST_FILENAME,
    load_vision_manifest,
)
from splatoon3_ai_coach.review.clock import is_countdown_display, is_overtime_display
from splatoon3_ai_coach.review.models import ReviewTimelineDataset
from splatoon3_ai_coach.vision.models import (
    GameEventReason,
    GameEventType,
    VisionManifest,
)

ALIGNMENT_FILENAME = "review_gameplay_alignment.json"
TIMELINE_FILENAME = "review_timeline.json"
MATCH_IDENTITY_FILENAME = "match_identity.json"

PairReason = Literal[
    "mode_mismatch",
    "overtime_mismatch",
    "insufficient_clock_coverage",
    "clock_domain_mismatch",
]
MappingReason = Literal[
    "no_review_axis",
    "pair_inconsistent",
    "fit_unknown",
    "outside_observed_gameplay_clock",
    "overtime_no_gameplay_timer",
]


class PairCheck(BaseModel):
    """Structural pair consistency. Not proof of match identity."""

    pair_status: Literal["consistent", "inconsistent"]
    reasons: list[PairReason] = Field(default_factory=list)
    review_battle_mode: str | None = None
    gameplay_battle_mode: str | None = None
    review_overtime: bool
    gameplay_overtime: bool
    gameplay_clock_zero_end: bool
    review_regulation_label: int | None = None
    gameplay_highest_hud_read: int | None = None
    coverage_fraction: float | None = None


class AlignedReviewTime(BaseModel):
    """One Review remaining value mapped to gameplay video time (interval)."""

    review_remaining: float
    video_interval: tuple[float, float] | None = None
    video_seconds: float | None = None
    alignment_error_s: float | None = None
    alignment_status: Literal["aligned", "unknown"]
    reason: MappingReason | None = None


class ReviewGameplayAlignment(BaseModel):
    """Alignment of one declared pair; see ``map_review_remaining``."""

    schema_version: str = "1.0"
    review_recording_id: str | None = None
    review_dataset_sha256: str | None = None
    gameplay_analysis_id: str | None = None
    gameplay_manifest_sha256: str | None = None
    pair: PairCheck
    calibration: GameplayClockCalibration
    delta: float
    e_axis: float | None = None
    max_edge_gap_seconds: float

    def map_review_remaining(self, r: float) -> AlignedReviewTime:
        """Map Review remaining ``r`` (larger = earlier) to a video interval."""
        reason = self._unknown_reason(r)
        if reason is not None:
            return AlignedReviewTime(
                review_remaining=r, alignment_status="unknown", reason=reason
            )
        assert self.calibration.c_interval is not None and self.e_axis is not None
        c_lo, c_hi = self.calibration.c_interval
        lo = c_lo - (r + self.delta) - self.e_axis
        hi = c_hi - (r + self.delta) + self.e_axis
        return AlignedReviewTime(
            review_remaining=r,
            video_interval=(lo, hi),
            video_seconds=(lo + hi) / 2,
            alignment_error_s=(hi - lo) / 2,
            alignment_status="aligned",
        )

    def _unknown_reason(self, r: float) -> MappingReason | None:
        if self.e_axis is None:
            return "no_review_axis"
        if self.pair.pair_status != "consistent":
            return "pair_inconsistent"
        if self.calibration.fit_status != "calibrated":
            return "fit_unknown"
        if r < 0:
            return "overtime_no_gameplay_timer"
        if not _bracketed(
            self.calibration.consistent_remaining, r, self.max_edge_gap_seconds
        ):
            return "outside_observed_gameplay_clock"
        return None


def _bracketed(values: list[int], r: float, gap: float) -> bool:
    """Consistent HUD edges within ``gap`` seconds on both sides of ``r``."""
    above = any(r <= v <= r + gap for v in values)
    below = any(r - gap <= v <= r for v in values)
    return above and below


def review_clock_profile(
    ds: ReviewTimelineDataset,
) -> tuple[bool, int | None, int | None]:
    """(overtime label seen, highest regulation label, lowest regulation label)."""
    usable = [
        s
        for s in ds.samples
        if s.elapsed_seconds is not None and s.clock_status != "implausible"
    ]
    overtime = any(is_overtime_display(s.displayed_clock) for s in usable)
    regulation = [
        s.elapsed_seconds
        for s in usable
        if s.elapsed_seconds >= 0 and not is_countdown_display(s.displayed_clock)
    ]
    return overtime, max(regulation, default=None), min(regulation, default=None)


def check_pair(
    ds: ReviewTimelineDataset,
    manifest: VisionManifest,
    gameplay_battle_mode: str | None,
    calibration: GameplayClockCalibration,
    config: AppConfig,
) -> PairCheck:
    """Structural checks only (mode, overtime, coverage, clock domain)."""
    cfg = config.alignment
    review_ot, reg_hi, reg_lo = review_clock_profile(ds)
    clock_zero = any(
        e.event_type is GameEventType.MATCH_END and e.reason is GameEventReason.CLOCK_ZERO
        for e in manifest.game_events
    )
    low = calibration.lowest_hud_read
    gameplay_ot = not clock_zero and low is not None and low <= 1
    coverage = _coverage(calibration.raw_edge_remaining_span, reg_lo, reg_hi)
    reasons: list[PairReason] = []
    if (
        ds.battle_mode_id
        and gameplay_battle_mode
        and ds.battle_mode_id != gameplay_battle_mode
    ):
        reasons.append("mode_mismatch")
    if review_ot != gameplay_ot:
        reasons.append("overtime_mismatch")
    if coverage is None or coverage < cfg.min_coverage_fraction:
        reasons.append("insufficient_clock_coverage")
    high = calibration.highest_hud_read
    if (
        reg_hi is None
        or high is None
        or abs(reg_hi - high) > cfg.clock_domain_tolerance_seconds
    ):
        reasons.append("clock_domain_mismatch")
    return PairCheck(
        pair_status="inconsistent" if reasons else "consistent",
        reasons=reasons,
        review_battle_mode=ds.battle_mode_id,
        gameplay_battle_mode=gameplay_battle_mode,
        review_overtime=review_ot,
        gameplay_overtime=gameplay_ot,
        gameplay_clock_zero_end=clock_zero,
        review_regulation_label=reg_hi,
        gameplay_highest_hud_read=high,
        coverage_fraction=None if coverage is None else round(coverage, 4),
    )


def _coverage(
    span: tuple[int, int] | None, reg_lo: int | None, reg_hi: int | None
) -> float | None:
    """Share of the Review regulation range covered by raw HUD edges."""
    if span is None or reg_lo is None or reg_hi is None or reg_hi <= reg_lo:
        return None
    overlap = min(span[1], reg_hi) - max(span[0], reg_lo)
    return max(overlap, 0) / (reg_hi - reg_lo)


def align_pair(
    ds: ReviewTimelineDataset,
    manifest: VisionManifest,
    gameplay_battle_mode: str | None,
    config: AppConfig,
) -> ReviewGameplayAlignment:
    """Calibrate the gameplay clock, check the pair and compose the mapping."""
    calibration = calibrate_gameplay_clock(
        manifest,
        min_usable_confidence=config.vision.timer.min_usable_confidence,
        config=config.alignment,
        playback=config.coach.playback_clock,
    )
    axis = ds.axis_calibration
    return ReviewGameplayAlignment(
        review_recording_id=ds.source_recording_id,
        gameplay_analysis_id=manifest.analysis.analysis_id,
        pair=check_pair(ds, manifest, gameplay_battle_mode, calibration, config),
        calibration=calibration,
        delta=config.alignment.delta,
        e_axis=None if axis is None else axis.max_absolute_error_seconds,
        max_edge_gap_seconds=config.alignment.max_edge_gap_seconds,
    )


def load_pair(
    review_dir: Path, gameplay_dir: Path
) -> tuple[ReviewTimelineDataset, VisionManifest, str | None, str, str]:
    """Read both inputs: dataset, manifest, gameplay mode and both sha256s."""
    raw = (review_dir / TIMELINE_FILENAME).read_bytes()
    ds = ReviewTimelineDataset.model_validate_json(raw)
    manifest = load_vision_manifest(gameplay_dir)
    identity = gameplay_dir / MATCH_IDENTITY_FILENAME
    mode = None
    if identity.exists():
        mode = json.loads(identity.read_text("utf-8")).get("battle_mode_id")
    manifest_sha = hashlib.sha256(
        (gameplay_dir / VISION_MANIFEST_FILENAME).read_bytes()
    ).hexdigest()
    return ds, manifest, mode, hashlib.sha256(raw).hexdigest(), manifest_sha


def write_alignment(
    review_dir: Path, gameplay_dir: Path, out_dir: Path, config: AppConfig
) -> tuple[Path, ReviewGameplayAlignment]:
    """Align a declared pair and write ``review_gameplay_alignment.json``."""
    ds, manifest, mode, review_sha, manifest_sha = load_pair(review_dir, gameplay_dir)
    alignment = align_pair(ds, manifest, mode, config).model_copy(
        update={
            "review_dataset_sha256": review_sha,
            "gameplay_manifest_sha256": manifest_sha,
        }
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ALIGNMENT_FILENAME
    out.write_text(alignment.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return out, alignment
