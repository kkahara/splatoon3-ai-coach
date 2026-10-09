"""Deterministic quality-gate metrics for persisted Review evidence."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from .lane_glyphs import UNKNOWN_ID
from .models import ReviewTimelineDataset, SlotState, SpecialUseEvent


class ValidationThresholds(BaseModel):
    """Single source of truth for the mechanical quality gate.

    The ``*special*`` values are per-recording gates measured on the frozen
    event-lane detector (analysis/review_validation/specials/QUALITY_GATE.md).
    Rates allow one error in the smallest held-out recording (12 specials);
    typed false claims and slot errors are never allowed.
    """

    min_clock_accuracy: float = Field(default=1.0, ge=0, le=1)
    max_cursor_error_px: float = Field(default=8, ge=0)
    min_roster_accuracy: float = Field(default=0.8, ge=0, le=1)
    max_false_acceptances: int = Field(default=0, ge=0)
    max_provenance_failures: int = Field(default=0, ge=0)
    max_label_alignment_failures: int = Field(default=0, ge=0)
    max_unknown_decision_errors: int = Field(default=0, ge=0)
    min_death_interval_iou: float = Field(default=0, ge=0, le=1)
    min_control_second_agreement: float = Field(default=0, ge=0, le=1)
    min_special_candidate_precision: float = Field(default=0.9, ge=0, le=1)
    min_special_candidate_recall: float = Field(default=0.9, ge=0, le=1)
    min_special_slot_accuracy: float = Field(default=1.0, ge=0, le=1)
    min_special_type_accuracy: float = Field(default=0.9, ge=0, le=1)
    max_special_timestamp_mae: float = Field(default=0.5, ge=0)
    min_special_timestamp_recall_2s: float = Field(default=0.9, ge=0, le=1)
    max_special_false_claims: int = Field(default=0, ge=0)
    min_respawn_bound_containment: float = Field(default=0, ge=0, le=1)
    min_ally_row_accuracy: float = Field(default=0, ge=0, le=1)
    min_death_slot_accuracy: float = Field(default=0, ge=0, le=1)


class LabeledSample(BaseModel):
    """Hand-labeled expectations for one artifact or adapter sample."""

    sample_id: str
    # Source video frame the label was made on; a different frame under the
    # same sample_id means the labels belong to another import.
    frame_index: int | None = None
    expected_acceptance: Literal["accepted", "rejected"]
    expected_clock_state: Literal["readable", "unclocked_diagnostic", "not_applicable"]
    expected_elapsed_seconds: int | None = None
    expected_cursor_x: float | None = None
    expected_slots: dict[str, SlotState] = Field(default_factory=dict)


class ExpectedDeathInterval(BaseModel):
    """Hand-labeled onset and recovery bounds for one slot."""

    slot_id: str
    onset_start: int
    onset_end: int
    recovery_start: int | None = None
    recovery_end: int | None = None


class ExpectedControlSegment(BaseModel):
    """Hand-labeled static control interval."""

    start_elapsed: int
    end_elapsed: int
    controller: Literal["top", "bottom", "neutral"]


class ExpectedTimelineEvent(BaseModel):
    """Hand-labeled player event at an elapsed timeline estimate."""

    elapsed: float
    slot: str
    special_id: str | None = None


class ValidationLabels(BaseModel):
    """Hand-labeled validation set and its measurement tolerances."""

    schema_version: str = "1"
    source_artifact: str
    source_manifest: str
    cursor_tolerance_px: float = Field(default=8, ge=0)
    samples: list[LabeledSample]
    death_intervals: list[ExpectedDeathInterval] = Field(default_factory=list)
    control_segments: list[ExpectedControlSegment] = Field(default_factory=list)
    deaths: list[ExpectedTimelineEvent] = Field(default_factory=list)
    specials: list[ExpectedTimelineEvent] = Field(default_factory=list)
    ally_row: Literal["top", "bottom"] | None = None


class ValidationMetrics(BaseModel):
    """Measured quality-gate values."""

    labeled_samples: int
    acceptance_accuracy: float
    false_acceptances: int
    false_rejections: int
    readable_clock_accuracy: float | None
    cursor_mean_error_px: float | None
    cursor_max_error_px: float | None
    roster_accuracy: float | None
    roster_expected_decisions: int
    roster_unknown_expected: int
    unknown_decision_errors: int
    provenance_failures: int
    label_alignment_failures: int = 0
    death_interval_iou: float | None = None
    control_second_agreement: float | None = None
    special_candidate_precision: float | None = None
    special_candidate_recall: float | None = None
    special_slot_accuracy: float | None = None
    special_type_accuracy: float | None = None
    special_timestamp_mae: float | None = None
    special_timestamp_recall_2s: float | None = None
    special_false_claims: int | None = None
    respawn_bound_containment: float | None = None
    ally_row_accuracy: float | None = None
    death_slot_accuracy: float | None = None


class ValidationResult(BaseModel):
    """Metrics, threshold checks, and deterministic gate decision."""

    thresholds: ValidationThresholds
    metrics: ValidationMetrics
    checks: dict[str, bool]
    go: bool
    discrepancies: list[str] = Field(default_factory=list)


def load_labels(path: Path) -> ValidationLabels:
    """Load and validate a YAML hand-label file."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ValidationLabels.model_validate(data)


def load_dataset(path: Path) -> ReviewTimelineDataset:
    """Load a persisted Phase 1 Review artifact."""
    return ReviewTimelineDataset.model_validate_json(path.read_text(encoding="utf-8"))


def validate_artifact(
    dataset: ReviewTimelineDataset,
    labels: ValidationLabels,
    *,
    thresholds: ValidationThresholds | None = None,
    manifest_path: Path | None = None,
) -> ValidationResult:
    """Compare labeled expectations with an immutable persisted artifact."""
    thresholds = thresholds or ValidationThresholds(
        max_cursor_error_px=labels.cursor_tolerance_px
    )
    actual = {sample.sample_id: sample for sample in dataset.samples}
    false_acceptances = 0
    false_rejections = 0
    readable_correct = 0
    readable_total = 0
    cursor_errors: list[float] = []
    roster_correct = 0
    roster_total = 0
    unknown_expected = 0
    unknown_decision_errors = 0
    discrepancies: list[str] = []

    for expected in labels.samples:
        sample = actual.get(expected.sample_id)
        # Implausible-clock samples stay in the artifact for provenance only.
        is_accepted = sample is not None and sample.clock_status != "implausible"
        if expected.expected_acceptance == "accepted" and not is_accepted:
            false_rejections += 1
            discrepancies.append(f"{expected.sample_id}: expected accepted")
        if expected.expected_acceptance == "rejected" and is_accepted:
            false_acceptances += 1
            discrepancies.append(f"{expected.sample_id}: expected rejected")
        if sample is None or not is_accepted:
            continue
        if expected.expected_clock_state == "readable":
            readable_total += 1
            if sample.elapsed_seconds == expected.expected_elapsed_seconds:
                readable_correct += 1
            else:
                discrepancies.append(
                    f"{expected.sample_id}: clock "
                    f"{sample.elapsed_seconds} != "
                    f"{expected.expected_elapsed_seconds}"
                )
        if expected.expected_cursor_x is not None and sample.cursor.x is not None:
            cursor_errors.append(abs(sample.cursor.x - expected.expected_cursor_x))
        for slot_id, expected_state in expected.expected_slots.items():
            actual_slot = next(
                (slot for slot in sample.slots if slot.slot_id == slot_id),
                None,
            )
            if expected_state == SlotState.unknown:
                unknown_expected += 1
                if actual_slot is not None and actual_slot.state != SlotState.unknown:
                    unknown_decision_errors += 1
                continue
            roster_total += 1
            if actual_slot is not None and actual_slot.state == expected_state:
                roster_correct += 1

    provenance_failures = check_provenance(dataset, manifest_path)
    misaligned = _label_alignment_failures(labels, actual)
    discrepancies.extend(misaligned)
    interval_iou = _death_interval_iou(dataset, labels)
    extended = _extended_metrics(dataset, labels)
    metrics = ValidationMetrics(
        labeled_samples=len(labels.samples),
        acceptance_accuracy=(
            1 - (false_acceptances + false_rejections) / len(labels.samples)
            if labels.samples
            else 0
        ),
        false_acceptances=false_acceptances,
        false_rejections=false_rejections,
        readable_clock_accuracy=(
            readable_correct / readable_total if readable_total else None
        ),
        cursor_mean_error_px=(
            sum(cursor_errors) / len(cursor_errors) if cursor_errors else None
        ),
        cursor_max_error_px=max(cursor_errors) if cursor_errors else None,
        roster_accuracy=(roster_correct / roster_total if roster_total else None),
        roster_expected_decisions=roster_total,
        roster_unknown_expected=unknown_expected,
        unknown_decision_errors=unknown_decision_errors,
        provenance_failures=provenance_failures,
        label_alignment_failures=len(misaligned),
        death_interval_iou=interval_iou,
        **extended,
    )
    checks = _checks(metrics, thresholds)
    return ValidationResult(
        thresholds=thresholds,
        metrics=metrics,
        checks=checks,
        go=all(checks.values()),
        discrepancies=sorted(discrepancies),
    )


def check_provenance(dataset: ReviewTimelineDataset, manifest_path: Path | None) -> int:
    """Check image paths/hashes and optional adapter manifest locators."""
    failures = 0
    manifest_samples = _manifest_samples(manifest_path)
    for sample in dataset.samples:
        path = Path(sample.image_path)
        if not path.exists():
            failures += 1
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != sample.image_sha256:
            failures += 1
        if manifest_path is not None:
            name = path.name
            item = next(
                (value for value in manifest_samples if value.get("image_path") == name),
                None,
            )
            if item is None or not _has_source_locator(item):
                failures += 1
    return failures


def render_report(result: ValidationResult) -> str:
    """Render thresholds, measurements, discrepancies, and GO/NO-GO."""
    metrics = result.metrics.model_dump()
    thresholds = result.thresholds.model_dump()
    lines = [
        "# Review timeline quality gate",
        "",
        f"## Decision: {'GO' if result.go else 'NO-GO'}",
        "",
        "## Configured thresholds",
        "",
    ]
    lines.extend(f"- `{key}`: {value}" for key, value in thresholds.items())
    lines.extend(["", "## Measured values", ""])
    lines.extend(f"- `{key}`: {value}" for key, value in metrics.items())
    lines.extend(["", "## Checks", ""])
    lines.extend(
        f"- `{key}`: {'PASS' if value else 'FAIL'}"
        for key, value in result.checks.items()
    )
    lines.extend(["", "## Discrepancies", ""])
    lines.extend(f"- {item}" for item in result.discrepancies)
    if not result.discrepancies:
        lines.append("- none")
    lines.extend(
        [
            "",
            "PNG byte identity is not treated as a requirement; provenance "
            "checks verify the persisted image/hash and source locator fields.",
        ]
    )
    return "\n".join(lines) + "\n"


def _checks(
    metrics: ValidationMetrics, thresholds: ValidationThresholds
) -> dict[str, bool]:
    """Calculate the mechanical gate checks."""
    return {
        "clock_accuracy": (
            metrics.readable_clock_accuracy is not None
            and metrics.readable_clock_accuracy >= thresholds.min_clock_accuracy
        ),
        "cursor_error": (
            metrics.cursor_max_error_px is not None
            and metrics.cursor_max_error_px <= thresholds.max_cursor_error_px
        ),
        "roster_accuracy": (
            metrics.roster_accuracy is not None
            and metrics.roster_accuracy >= thresholds.min_roster_accuracy
        ),
        "false_acceptances": (
            metrics.false_acceptances <= thresholds.max_false_acceptances
        ),
        "provenance": (metrics.provenance_failures <= thresholds.max_provenance_failures),
        "label_alignment": (
            metrics.label_alignment_failures <= thresholds.max_label_alignment_failures
        ),
        "unknown_handling": (
            metrics.unknown_decision_errors <= thresholds.max_unknown_decision_errors
        ),
        "death_intervals": (
            metrics.death_interval_iou is None
            or metrics.death_interval_iou >= thresholds.min_death_interval_iou
        ),
        "control_segments": _minimum_or_unlabeled(
            metrics.control_second_agreement,
            thresholds.min_control_second_agreement,
        ),
        "special_candidate_precision": _minimum_or_unlabeled(
            metrics.special_candidate_precision,
            thresholds.min_special_candidate_precision,
        ),
        "special_candidate_recall": _minimum_or_unlabeled(
            metrics.special_candidate_recall,
            thresholds.min_special_candidate_recall,
        ),
        "special_slots": _minimum_or_unlabeled(
            metrics.special_slot_accuracy,
            thresholds.min_special_slot_accuracy,
        ),
        "special_types": _minimum_or_unlabeled(
            metrics.special_type_accuracy,
            thresholds.min_special_type_accuracy,
        ),
        "special_timestamp_mae": (
            metrics.special_timestamp_mae is None
            or metrics.special_timestamp_mae <= thresholds.max_special_timestamp_mae
        ),
        "special_timestamp_recall_2s": _minimum_or_unlabeled(
            metrics.special_timestamp_recall_2s,
            thresholds.min_special_timestamp_recall_2s,
        ),
        "special_false_claims": (
            metrics.special_false_claims is None
            or metrics.special_false_claims <= thresholds.max_special_false_claims
        ),
        "respawn_bounds": _minimum_or_unlabeled(
            metrics.respawn_bound_containment,
            thresholds.min_respawn_bound_containment,
        ),
        "ally_row": _minimum_or_unlabeled(
            metrics.ally_row_accuracy,
            thresholds.min_ally_row_accuracy,
        ),
        "death_slots": _minimum_or_unlabeled(
            metrics.death_slot_accuracy,
            thresholds.min_death_slot_accuracy,
        ),
    }


def _label_alignment_failures(
    labels: ValidationLabels, actual: dict[str, Any]
) -> list[str]:
    """Labels whose sample_id now names a different source frame."""
    failures = []
    for expected in labels.samples:
        sample = actual.get(expected.sample_id)
        if expected.frame_index is None or sample is None:
            continue
        if sample.frame_index != expected.frame_index:
            failures.append(
                f"{expected.sample_id}: labeled frame {expected.frame_index} "
                f"!= artifact frame {sample.frame_index}"
            )
    return failures


def _minimum_or_unlabeled(value: float | None, threshold: float) -> bool:
    return value is None or value >= threshold


def _extended_metrics(
    dataset: ReviewTimelineDataset,
    labels: ValidationLabels,
) -> dict[str, float | None]:
    special_pairs = _match_events(
        [
            (event.elapsed_estimate, event.slot_id, event)
            for event in dataset.special_events
        ],
        labels.specials,
    )
    death_pairs = _match_events(
        [
            (float(marker.elapsed_estimate), marker.player_slot_id, marker)
            for marker in dataset.match_history_events
            if marker.kind == "death" and marker.elapsed_estimate is not None
        ],
        labels.deaths,
    )
    special_errors = [
        abs(actual.elapsed_estimate - expected.elapsed)
        for actual, expected in special_pairs
        if actual.slot_id == expected.slot and actual.special_id == expected.special_id
    ]
    return {
        "control_second_agreement": _control_second_agreement(dataset, labels),
        "special_candidate_precision": (
            (
                len(special_pairs) / len(dataset.special_events)
                if dataset.special_events
                else 0.0
            )
            if labels.specials
            else None
        ),
        "special_candidate_recall": (
            len(special_pairs) / len(labels.specials) if labels.specials else None
        ),
        "special_slot_accuracy": _labeled_pair_accuracy(
            special_pairs,
            labels.specials,
            lambda actual, expected: actual.slot_id == expected.slot,
        ),
        "special_type_accuracy": _labeled_pair_accuracy(
            special_pairs,
            labels.specials,
            lambda actual, expected: actual.special_id == expected.special_id,
        ),
        "special_timestamp_mae": (
            sum(special_errors) / len(special_errors) if special_errors else None
        ),
        "special_timestamp_recall_2s": (
            sum(error <= 2 for error in special_errors) / len(labels.specials)
            if labels.specials
            else None
        ),
        "special_false_claims": (
            _special_false_claims(dataset.special_events, special_pairs)
            if labels.specials
            else None
        ),
        "respawn_bound_containment": _respawn_containment(dataset, labels),
        "ally_row_accuracy": (
            float(dataset.team_identity.ally_row == labels.ally_row)
            if labels.ally_row is not None
            else None
        ),
        "death_slot_accuracy": _labeled_pair_accuracy(
            death_pairs,
            labels.deaths,
            lambda actual, expected: actual.player_slot_id == expected.slot,
        ),
    }


def _match_events(
    actual: list[tuple[float, str | None, object]],
    expected: list[ExpectedTimelineEvent],
) -> list[tuple[Any, ExpectedTimelineEvent]]:
    """One-to-one match of candidates to labels within two seconds.

    Same-slot pairs are taken before cross-slot pairs, then the closest in
    time, so simultaneous events in different slots are not cross-paired.
    A cross-slot pair is still allowed and then counts as a slot error.
    """
    options = sorted(
        (slot != label.slot, abs(elapsed - label.elapsed), a_index, l_index)
        for a_index, (elapsed, slot, _) in enumerate(actual)
        for l_index, label in enumerate(expected)
        if abs(elapsed - label.elapsed) <= 2
    )
    used_actual: set[int] = set()
    used_labels: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _, _, a_index, l_index in options:
        if a_index in used_actual or l_index in used_labels:
            continue
        used_actual.add(a_index)
        used_labels.add(l_index)
        pairs.append((l_index, a_index))
    return [(actual[a][2], expected[lbl]) for lbl, a in sorted(pairs)]


def _special_false_claims(
    events: list[SpecialUseEvent],
    pairs: list[tuple[Any, ExpectedTimelineEvent]],
) -> int:
    """Typed specials that are unpaired or paired with a wrong slot or type.

    A retained ``unknown`` is an abstention, never a false claim.
    """
    paired = {id(actual): expected for actual, expected in pairs}
    false_claims = 0
    for event in events:
        if event.special_id == UNKNOWN_ID:
            continue
        expected = paired.get(id(event))
        if (
            expected is None
            or expected.slot != event.slot_id
            or expected.special_id != event.special_id
        ):
            false_claims += 1
    return false_claims


def _pair_accuracy(
    pairs: list[tuple[Any, Any]],
    predicate: Callable[[Any, Any], bool],
) -> float | None:
    if not pairs:
        return None
    correct = sum(predicate(actual, expected) for actual, expected in pairs)
    return correct / len(pairs)


def _labeled_pair_accuracy(
    pairs: list[tuple[Any, Any]],
    labels: list[Any],
    predicate: Callable[[Any, Any], bool],
) -> float | None:
    if not labels:
        return None
    return _pair_accuracy(pairs, predicate) or 0.0


def _control_second_agreement(
    dataset: ReviewTimelineDataset,
    labels: ValidationLabels,
) -> float | None:
    expected: dict[int, str] = {}
    for segment in labels.control_segments:
        for second in range(segment.start_elapsed, segment.end_elapsed + 1):
            expected[second] = segment.controller
    if not expected:
        return None
    correct = 0
    for second, controller in expected.items():
        actual = next(
            (
                segment.controller
                for segment in dataset.control_segments
                if segment.start_elapsed <= second <= segment.end_elapsed
            ),
            None,
        )
        correct += actual == controller
    return correct / len(expected)


def _respawn_containment(
    dataset: ReviewTimelineDataset,
    labels: ValidationLabels,
) -> float | None:
    expected = [
        item
        for item in labels.death_intervals
        if item.recovery_start is not None and item.recovery_end is not None
    ]
    if not expected:
        return None
    contained = 0
    for label in expected:
        episode = next(
            (
                item
                for item in dataset.death_episodes
                if item.slot_id == label.slot_id
                and item.last_dead_elapsed is not None
                and item.next_alive_elapsed is not None
            ),
            None,
        )
        if episode is None:
            continue
        bound = sorted((episode.last_dead_elapsed, episode.next_alive_elapsed))
        recovery = sorted((label.recovery_start, label.recovery_end))
        contained += bound[0] <= recovery[0] and bound[1] >= recovery[1]
    return contained / len(expected)


def _death_interval_iou(
    dataset: ReviewTimelineDataset, labels: ValidationLabels
) -> float | None:
    """Compare labeled onset bounds with same-slot persisted bounds."""
    if not labels.death_intervals:
        return None
    scores: list[float] = []
    for expected in labels.death_intervals:
        candidates = [
            episode
            for episode in dataset.death_episodes
            if episode.slot_id == expected.slot_id
        ]
        if not candidates:
            scores.append(0.0)
            continue
        actual = candidates[0]
        if actual.last_alive_elapsed is None or actual.first_dead_elapsed is None:
            scores.append(0.0)
            continue
        scores.append(
            _interval_iou(
                (expected.onset_start, expected.onset_end),
                (actual.last_alive_elapsed, actual.first_dead_elapsed),
            )
        )
    return sum(scores) / len(scores)


def _interval_iou(left: tuple[int, int], right: tuple[int, int]) -> float:
    """Return inclusive integer interval intersection-over-union."""
    left_start, left_end = sorted(left)
    right_start, right_end = sorted(right)
    intersection = max(0, min(left_end, right_end) - max(left_start, right_start) + 1)
    union = max(left_end, right_end) - min(left_start, right_start) + 1
    return intersection / union if union else 1.0


def _manifest_samples(path: Path | None) -> list[dict[str, object]]:
    if path is None or not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("video_samples", []))


def _has_source_locator(item: dict[str, object]) -> bool:
    return (
        item.get("source_video_time") is not None and item.get("frame_index") is not None
    )
