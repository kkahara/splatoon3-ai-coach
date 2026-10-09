"""Static-layout event-lane extraction and cursor-axis calibration.

The event lane is a static history; only the cursor moves between frames.
Observations from the selected static frames are clustered by icon row and x
neighborhood. A frame where the cursor covered a position contributes
``cursor_obscured_sample_ids`` only: never positive or negative evidence.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np

from splatoon3_ai_coach.config.models import ReviewTimelineConfig

from .clock import is_overtime_display
from .lane_glyphs import (
    DEATH_TEMPLATE_ID,
    SPECIAL_TEMPLATE_FILES,
    UNKNOWN_ID,
    LaneGlyphCandidate,
    LaneGlyphScore,
    accept_lane_match,
    compute_row_responses,
    cursor_band,
    detect_lane_glyph_candidates,
    lane_origin,
    load_lane_templates,
    score_lane_glyph,
)
from .models import (
    AxisCalibrationEvidence,
    ReviewTimelineSample,
    SpecialUseEvent,
    TimelineEventMarker,
)

EVENT_LANE_DETECTOR_VERSION = "2-row-template"

__all__ = [
    "EVENT_LANE_DETECTOR_VERSION",
    "SPECIAL_TEMPLATE_FILES",
    "LaneObservation",
    "calibrate_axis",
    "cluster_observations",
    "deduplicate_events",
    "events_from_observations",
    "extract_lane_events",
    "kit_consistency_warnings",
    "observe_frames",
    "observe_lane",
    "reaccept",
    "select_static_samples",
]


@dataclass(frozen=True)
class LaneObservation:
    """One clear glyph observation in one frame, with its acceptance result."""

    candidate: LaneGlyphCandidate
    score: LaneGlyphScore
    label: str

    @property
    def sample_id(self) -> str:
        """Frame this observation came from."""
        return self.candidate.sample_id


def calibrate_axis(
    samples: list[ReviewTimelineSample],
    config: ReviewTimelineConfig | None = None,
) -> AxisCalibrationEvidence | None:
    """Least-squares fit match time from all valid in-match observations.

    Negative countdown labels share the left boundary with the opening match
    clock and therefore are not observations of the match-history x axis.
    Overtime labels (also negative) lie on the axis; they enter the fit only
    when ``axis_include_overtime`` is set (off in the frozen baseline).
    """
    config = config or ReviewTimelineConfig()
    points = [
        (
            sample.cursor.x_norm if sample.cursor.x_norm is not None else sample.cursor.x,
            sample.elapsed_seconds,
        )
        for sample in samples
        if (sample.cursor.x_norm is not None or sample.cursor.x is not None)
        and sample.elapsed_seconds is not None
        and (
            sample.elapsed_seconds >= 0
            or (
                config.axis_include_overtime
                and is_overtime_display(sample.displayed_clock)
            )
        )
    ]
    if len(points) < config.axis_min_points:
        return None
    xs = np.asarray([float(point[0]) for point in points], dtype=np.float64)
    clocks = np.asarray([float(point[1]) for point in points], dtype=np.float64)
    keep = _trim_axis_outliers(xs, clocks, config)
    inliers = int(keep.sum())
    if (
        inliers < config.axis_min_points
        or inliers < config.axis_min_inlier_fraction * len(xs)
    ):
        return None
    slope, intercept = _fit_line(xs[keep], clocks[keep])
    residuals = np.abs(clocks[keep] - (slope * xs[keep] + intercept))
    mean_error = float(np.mean(residuals))
    max_error = float(np.max(residuals))
    if (
        mean_error > config.axis_max_mean_error_seconds
        or max_error > config.axis_max_error_seconds
    ):
        return None
    return AxisCalibrationEvidence(
        slope=float(slope),
        intercept=float(intercept),
        point_count=inliers,
        rejected_point_count=len(xs) - inliers,
        mean_absolute_error_seconds=mean_error,
        max_absolute_error_seconds=max_error,
    )


def _fit_line(xs: np.ndarray, clocks: np.ndarray) -> tuple[float, float]:
    design = np.column_stack((xs, np.ones_like(xs)))
    slope, intercept = np.linalg.lstsq(design, clocks, rcond=None)[0]
    return float(slope), float(intercept)


def _trim_axis_outliers(
    xs: np.ndarray, clocks: np.ndarray, config: ReviewTimelineConfig
) -> np.ndarray:
    """Inlier mask after dropping impossible clocks and iterative misreads.

    The cut starts loose (median-scaled) so a few wild misreads cannot drag
    the first fit, then tightens to ``axis_max_error_seconds``.
    """
    keep = clocks <= config.regulation_seconds
    for _ in range(8):
        if keep.sum() < 2:
            break
        slope, intercept = _fit_line(xs[keep], clocks[keep])
        residuals = np.abs(clocks - (slope * xs + intercept))
        cut = max(config.axis_max_error_seconds, 3.0 * float(np.median(residuals[keep])))
        updated = (clocks <= config.regulation_seconds) & (residuals <= cut)
        if np.array_equal(updated, keep):
            break
        keep = updated
    if keep.sum() >= 2:
        slope, intercept = _fit_line(xs[keep], clocks[keep])
        keep &= np.abs(clocks - (slope * xs + intercept)) <= config.axis_max_error_seconds
    return keep


def select_static_samples(
    samples: list[ReviewTimelineSample], limit: int
) -> list[ReviewTimelineSample]:
    """Select high-confidence, cursor-diverse samples deterministically."""
    valid = [
        sample
        for sample in samples
        if sample.cursor.x is not None and sample.elapsed_seconds is not None
    ]
    ranked = sorted(
        valid,
        key=lambda sample: (
            -sample.cursor.confidence,
            -sample.clock_confidence,
            sample.capture_index,
        ),
    )
    selected: list[ReviewTimelineSample] = []
    for sample in ranked:
        if not selected or all(
            abs((sample.cursor.x or 0) - (item.cursor.x or 0)) > 20 for item in selected
        ):
            selected.append(sample)
        if len(selected) >= limit:
            break
    return sorted(selected, key=lambda sample: sample.capture_index)


def observe_lane(
    image: np.ndarray,
    sample_id: str,
    cursor_x: float | None,
    config: ReviewTimelineConfig,
) -> list[LaneObservation]:
    """Discover, score and accept clear glyphs in one frame."""
    templates = load_lane_templates(
        config.special_template_dir,
        image.shape[0],
        config.event_glyph_representation,
        tuple(config.special_template_scales),
    )
    rows = compute_row_responses(image, config, templates, cursor_x)
    out: list[LaneObservation] = []
    for candidate in detect_lane_glyph_candidates(image, sample_id, config, rows):
        score = score_lane_glyph(candidate, rows[candidate.row_id])
        out.append(LaneObservation(candidate, score, accept_lane_match(score, config)))
    return out


def cluster_observations(
    observations: list[LaneObservation], tolerance: float
) -> list[list[LaneObservation]]:
    """Group observations by same row and same x neighborhood across frames."""
    clusters: list[list[LaneObservation]] = []
    ordered = sorted(observations, key=lambda o: (o.candidate.row_id, o.candidate.x_norm))
    for observation in ordered:
        last = clusters[-1] if clusters else None
        if (
            last is not None
            and last[0].candidate.row_id == observation.candidate.row_id
            and abs(_mean_x(last) - observation.candidate.x_norm) <= tolerance
        ):
            last.append(observation)
        else:
            clusters.append([observation])
    return clusters


def _mean_x(cluster: list[LaneObservation]) -> float:
    return float(np.mean([o.candidate.x_norm for o in cluster]))


def extract_lane_events(
    images: dict[str, np.ndarray],
    selected: list[ReviewTimelineSample],
    axis: AxisCalibrationEvidence | None,
    config: ReviewTimelineConfig,
) -> tuple[list[TimelineEventMarker], list[SpecialUseEvent]]:
    """Detect, classify, timestamp and deduplicate event-lane icons."""
    if axis is None:
        return [], []
    observations = observe_frames(images, selected, config)
    return events_from_observations(observations, images, selected, axis, config)


def observe_frames(
    images: dict[str, np.ndarray],
    selected: list[ReviewTimelineSample],
    config: ReviewTimelineConfig,
) -> list[LaneObservation]:
    """Clear glyph observations across the selected static frames."""
    observations: list[LaneObservation] = []
    for sample in selected:
        image = images.get(sample.sample_id)
        if image is not None:
            observations.extend(
                observe_lane(image, sample.sample_id, sample.cursor.x, config)
            )
    return observations


def reaccept(
    observations: list[LaneObservation], config: ReviewTimelineConfig
) -> list[LaneObservation]:
    """Re-apply the acceptance rule of ``config`` to existing scores."""
    return [
        LaneObservation(o.candidate, o.score, accept_lane_match(o.score, config))
        for o in observations
    ]


def events_from_observations(
    observations: list[LaneObservation],
    images: dict[str, np.ndarray],
    selected: list[ReviewTimelineSample],
    axis: AxisCalibrationEvidence,
    config: ReviewTimelineConfig,
) -> tuple[list[TimelineEventMarker], list[SpecialUseEvent]]:
    """Cluster, timestamp, deduplicate and split frame observations."""
    clustered = cluster_observations(observations, config.special_deduplication_tolerance)
    events = [_cluster_event(cluster, axis, config) for cluster in clustered]
    events = deduplicate_events(events, config)
    _attach_cursor_provenance(events, images, selected, config)
    return _split_outputs(events)


def _cluster_event(
    cluster: list[LaneObservation],
    axis: AxisCalibrationEvidence,
    config: ReviewTimelineConfig,
) -> SpecialUseEvent:
    accepted = [o for o in cluster if o.label != UNKNOWN_ID]
    pool = accepted or cluster
    representative = max(pool, key=lambda o: o.score.score)
    label = representative.label if accepted else UNKNOWN_ID
    agreeing = [o for o in pool if o.label == label]
    candidate, score = representative.candidate, representative.score
    width = candidate.frame_width
    anchor_x = {
        "center": candidate.center_x,
        "left": candidate.left_x,
        "right": candidate.right_x,
    }[config.event_timestamp_anchor]
    return SpecialUseEvent(
        slot_id=candidate.slot_id,
        team_row=candidate.team_row,  # type: ignore[arg-type]
        special_id=label,
        elapsed_estimate=axis.elapsed_at(anchor_x / width),
        x_norm=candidate.x_norm,
        center_x=candidate.center_x,
        score=score.score,
        runner_up_score=score.runner_up_score,
        runner_up_margin=score.margin,
        template_id=score.best_id,
        runner_up_template_id=score.runner_up_id,
        row_id=candidate.row_id,
        left_x_norm=candidate.left_x / width,
        right_x_norm=min(1.0, candidate.right_x / width),
        timestamp_anchor=config.event_timestamp_anchor,
        scale=score.scale,
        supporting_sample_ids=sorted({o.sample_id for o in agreeing}),
    )


def deduplicate_events(
    events: list[SpecialUseEvent],
    config: ReviewTimelineConfig,
) -> list[SpecialUseEvent]:
    """Merge adjacent-frame observations of one lane icon."""
    kept: list[SpecialUseEvent] = []
    for event in sorted(events, key=lambda item: (item.slot_id, item.x_norm)):
        duplicate = next(
            (
                item
                for item in reversed(kept)
                if item.slot_id == event.slot_id
                and item.special_id == event.special_id
                and abs(item.x_norm - event.x_norm)
                <= config.special_deduplication_tolerance
            ),
            None,
        )
        if duplicate is None:
            kept.append(event)
            continue
        duplicate.supporting_sample_ids = sorted(
            set(duplicate.supporting_sample_ids + event.supporting_sample_ids)
        )
        if event.score > duplicate.score:
            duplicate.score = event.score
            duplicate.runner_up_score = event.runner_up_score
            duplicate.runner_up_margin = event.runner_up_margin
            duplicate.template_id = event.template_id
    return kept


def _attach_cursor_provenance(
    events: list[SpecialUseEvent],
    images: dict[str, np.ndarray],
    selected: list[ReviewTimelineSample],
    config: ReviewTimelineConfig,
) -> None:
    """Record frames whose cursor band covered an event's horizontal extent."""
    for sample in selected:
        image = images.get(sample.sample_id)
        if image is None or sample.cursor.x is None:
            continue
        lane_x = lane_origin(image, config)[0]
        width = image.shape[1]
        band = cursor_band(sample.cursor.x, lane_x, width, config)
        if band is None:
            continue
        lo, hi = band[0] + lane_x, band[1] + lane_x
        for event in events:
            left = (
                event.left_x_norm if event.left_x_norm is not None else event.x_norm
            ) * width
            right = (
                event.right_x_norm if event.right_x_norm is not None else event.x_norm
            ) * width
            if (
                right > lo
                and left <= hi
                and sample.sample_id not in event.supporting_sample_ids
            ):
                event.cursor_obscured_sample_ids = sorted(
                    {*event.cursor_obscured_sample_ids, sample.sample_id}
                )


def _split_outputs(
    events: list[SpecialUseEvent],
) -> tuple[list[TimelineEventMarker], list[SpecialUseEvent]]:
    """Death-best icons become markers only; never ``SpecialUseEvent``."""
    markers: list[TimelineEventMarker] = []
    specials: list[SpecialUseEvent] = []
    for index, event in enumerate(events):
        if (
            event.template_id == DEATH_TEMPLATE_ID
            or event.special_id == DEATH_TEMPLATE_ID
        ):
            markers.append(_death_marker(event, index))
        else:
            specials.append(event)
    return markers, specials


def _death_marker(event: SpecialUseEvent, index: int) -> TimelineEventMarker:
    kind = "death" if event.special_id == DEATH_TEMPLATE_ID else "unknown"
    return TimelineEventMarker(
        marker_id=f"{kind}-{event.slot_id}-{index:04d}",
        team_row=event.team_row,
        kind=kind,
        x_norm=event.x_norm,
        elapsed_estimate=event.elapsed_estimate,
        event_elapsed_min=round(event.elapsed_estimate) - 1,
        event_elapsed_max=round(event.elapsed_estimate) + 1,
        confidence=event.score,
        supporting_sample_ids=event.supporting_sample_ids,
        cursor_obscured_sample_ids=event.cursor_obscured_sample_ids,
        player_slot_id=event.slot_id,
    )


def kit_consistency_warnings(specials: list[SpecialUseEvent]) -> list[str]:
    """Warn when one slot shows more than one special. Never rewrites events.

    A player's special is fixed for the match, so a second distinct special in
    a slot is a detection conflict worth reviewing, not a correction source.
    """
    by_slot: dict[str, Counter[str]] = {}
    for event in specials:
        if event.special_id == UNKNOWN_ID:
            continue
        by_slot.setdefault(event.slot_id, Counter())[event.special_id] += 1
    return sorted(
        f"special_slot_conflict:{slot}"
        for slot, counts in by_slot.items()
        if len(counts) > 1
    )
