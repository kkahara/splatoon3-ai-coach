"""Deterministic quality-gate metrics for persisted Review evidence."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from .models import ReviewTimelineDataset, SlotState


class ValidationThresholds(BaseModel):
    """Single source of truth for the mechanical quality gate."""

    min_clock_accuracy: float = Field(default=1.0, ge=0, le=1)
    max_cursor_error_px: float = Field(default=8, ge=0)
    min_roster_accuracy: float = Field(default=0.8, ge=0, le=1)
    max_false_acceptances: int = Field(default=0, ge=0)
    max_provenance_failures: int = Field(default=0, ge=0)
    max_unknown_decision_errors: int = Field(default=0, ge=0)
    min_death_interval_iou: float = Field(default=0, ge=0, le=1)


class LabeledSample(BaseModel):
    """Hand-labeled expectations for one artifact or adapter sample."""

    sample_id: str
    expected_acceptance: Literal["accepted", "rejected"]
    expected_clock_state: Literal[
        "readable", "unclocked_diagnostic", "not_applicable"
    ]
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


class ValidationLabels(BaseModel):
    """Hand-labeled validation set and its measurement tolerances."""

    schema_version: str = "1"
    source_artifact: str
    source_manifest: str
    cursor_tolerance_px: float = Field(default=8, ge=0)
    samples: list[LabeledSample]
    death_intervals: list[ExpectedDeathInterval] = Field(default_factory=list)


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
    death_interval_iou: float | None = None


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
        is_accepted = sample is not None
        if expected.expected_acceptance == "accepted" and not is_accepted:
            false_rejections += 1
            discrepancies.append(f"{expected.sample_id}: expected accepted")
        if expected.expected_acceptance == "rejected" and is_accepted:
            false_acceptances += 1
            discrepancies.append(f"{expected.sample_id}: expected rejected")
        if sample is None:
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
            cursor_errors.append(
                abs(sample.cursor.x - expected.expected_cursor_x)
            )
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
    interval_iou = _death_interval_iou(dataset, labels)
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
        roster_accuracy=(
            roster_correct / roster_total if roster_total else None
        ),
        roster_expected_decisions=roster_total,
        roster_unknown_expected=unknown_expected,
        unknown_decision_errors=unknown_decision_errors,
        provenance_failures=provenance_failures,
        death_interval_iou=interval_iou,
    )
    checks = _checks(metrics, thresholds)
    return ValidationResult(
        thresholds=thresholds,
        metrics=metrics,
        checks=checks,
        go=all(checks.values()),
        discrepancies=sorted(discrepancies),
    )


def check_provenance(
    dataset: ReviewTimelineDataset, manifest_path: Path | None
) -> int:
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
                (
                    value for value in manifest_samples
                    if value.get("image_path") == name
                ),
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
        "provenance": (
            metrics.provenance_failures <= thresholds.max_provenance_failures
        ),
        "unknown_handling": (
            metrics.unknown_decision_errors
            <= thresholds.max_unknown_decision_errors
        ),
        "death_intervals": (
            metrics.death_interval_iou is None
            or metrics.death_interval_iou >= thresholds.min_death_interval_iou
        ),
    }


def _death_interval_iou(
    dataset: ReviewTimelineDataset, labels: ValidationLabels
) -> float | None:
    """Compare labeled onset bounds with same-slot persisted bounds."""
    if not labels.death_intervals:
        return None
    scores: list[float] = []
    for expected in labels.death_intervals:
        candidates = [
            episode for episode in dataset.death_episodes
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


def _interval_iou(
    left: tuple[int, int], right: tuple[int, int]
) -> float:
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
        item.get("source_video_time") is not None
        and item.get("frame_index") is not None
    )

