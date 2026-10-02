"""Orchestration for local Review timeline screenshot imports."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import yaml

from splatoon3_ai_coach.config.models import ReviewTimelineConfig

from .clock import (
    ClockSource,
    ReviewClock,
    filename_clock_hint,
    resolve_clock,
)
from .coverage import build_coverage, derive_death_episodes
from .cursor import detect_cursor
from .event_lane import calibrate_axis, extract_static_markers, select_static_samples
from .label_reader import read_display_text
from .models import ReviewTimelineDataset, ReviewTimelineSample
from .roster import extract_cursor_slots


def import_timeline(
    directory: Path,
    *,
    config: ReviewTimelineConfig | None = None,
    manifest_path: Path | None = None,
    output_dir: Path | None = None,
    source_recording_id: str | None = None,
) -> ReviewTimelineDataset:
    """Import a local screenshot sweep into deterministic evidence."""
    config = config or ReviewTimelineConfig()
    manifest = _load_manifest(manifest_path)
    if source_recording_id is None and manifest:
        source_recording_id = manifest.get("source_recording_id")
    if manifest and "regulation_seconds" in manifest:
        config = config.model_copy(
            update={"regulation_seconds": int(manifest["regulation_seconds"])}
        )
    paths = _manifest_paths(directory, manifest) if manifest else _image_paths(directory)
    samples: list[ReviewTimelineSample] = []
    images: dict[str, Any] = {}
    for index, path in enumerate(paths):
        image = cv2.imread(str(path))
        sample_id = f"sample-{index:06d}"
        if image is None:
            samples.append(_unreadable_sample(sample_id, index, path))
            continue
        images[sample_id] = image
        samples.append(
            _read_sample(
                image,
                path,
                sample_id,
                index,
                manifest.get("entries", []) if manifest else [],
                config,
            )
        )
    selected = select_static_samples(samples, config.static_frame_count)
    markers = extract_static_markers(images, selected, config)
    axis = calibrate_axis(samples)
    if axis is not None:
        for marker in markers:
            estimated = round(axis.elapsed_at(marker.x_norm))
            marker.event_elapsed_min = estimated - 1
            marker.event_elapsed_max = estimated + 1
    coverage = build_coverage(samples)
    episodes = derive_death_episodes(samples)
    dataset = ReviewTimelineDataset(
        source_recording_id=source_recording_id,
        regulation_seconds=config.regulation_seconds,
        samples=samples,
        death_episodes=episodes,
        match_history_events=markers,
        coverage=coverage,
        warnings=_warnings(coverage, samples),
    )
    if output_dir is not None:
        write_artifacts(dataset, output_dir)
    return dataset


def write_artifacts(dataset: ReviewTimelineDataset, output_dir: Path) -> None:
    """Write stable JSON and Markdown artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = dataset.model_dump(mode="json", exclude_none=True)
    (output_dir / "review_timeline.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "review_timeline_report.md").write_text(
        render_report(dataset),
        encoding="utf-8",
    )


def render_report(dataset: ReviewTimelineDataset) -> str:
    """Render the concise human-readable import report."""
    coverage = dataset.coverage
    lines = [
        "# Review timeline import",
        "",
        f"- images: {coverage.sample_count}",
        f"- clocked samples: {coverage.clocked_sample_count}",
        f"- elapsed range: {coverage.first_elapsed_seconds} to "
        f"{coverage.last_elapsed_seconds}",
        f"- missing seconds: {coverage.missing_elapsed_seconds or 'none'}",
        f"- duplicate seconds: {coverage.duplicate_elapsed_seconds or 'none'}",
        f"- non-monotonic samples: "
        f"{coverage.non_monotonic_capture_indexes or 'none'}",
        f"- bounded death episodes: {len(dataset.death_episodes)}",
        f"- static event markers: {len(dataset.match_history_events)}",
        "",
        "## Warnings",
        "",
    ]
    lines.extend(f"- {warning}" for warning in dataset.warnings)
    return "\n".join(lines) + "\n"


def _read_sample(
    image: Any,
    path: Path,
    sample_id: str,
    index: int,
    entries: list[dict[str, Any]],
    config: ReviewTimelineConfig,
) -> ReviewTimelineSample:
    detection = detect_cursor(image, config)
    entry = next(
        (item for item in entries if item.get("capture_index", index) == index),
        {},
    )
    manifest_clock = _clock_from_text(entry.get("elapsed_clock"))
    filename_clock = filename_clock_hint(path.name)
    label = read_display_text(entry.get("observed_clock"))
    clock, status, warning = resolve_clock(
        manifest=manifest_clock,
        filename=filename_clock,
        observed=label.clock,
    )
    sample = ReviewTimelineSample(
        sample_id=sample_id,
        capture_index=index,
        image_path=str(path.resolve()),
        image_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        elapsed_seconds=clock.elapsed_seconds if clock else None,
        displayed_clock=clock.display if clock else None,
        clock_source=clock.source.value if clock else None,
        clock_status=status,
        clock_confidence=clock.confidence if clock else 0,
        cursor={
            "x": detection.x,
            "confidence": detection.confidence,
            "runner_up_margin": detection.runner_up_margin,
        },
        slots=extract_cursor_slots(
            image,
            detection.x,
            config=config,
            sample_id=sample_id,
            elapsed_seconds=clock.elapsed_seconds if clock else None,
        ),
    )
    if warning:
        sample.warnings.append(warning)
    return sample


def _clock_from_text(text: Any) -> ReviewClock | None:
    read = read_display_text(text, confidence=1.0)
    if read.clock is None:
        return None
    return read.clock.model_copy(update={"source": ClockSource.manifest})


def _image_paths(directory: Path) -> list[Path]:
    return sorted(
        [
            path for path in directory.iterdir()
            if path.suffix.lower() in {".png", ".jpg", ".jpeg"}
        ],
        key=lambda path: path.name,
    )


def _manifest_paths(directory: Path, manifest: dict[str, Any]) -> list[Path]:
    return [directory / str(item["image_path"]) for item in manifest.get("entries", [])]


def _load_manifest(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data if isinstance(data, dict) else {}


def _unreadable_sample(sample_id: str, index: int, path: Path) -> ReviewTimelineSample:
    return ReviewTimelineSample(
        sample_id=sample_id,
        capture_index=index,
        image_path=str(path.resolve()),
        image_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _warnings(coverage: Any, samples: list[ReviewTimelineSample]) -> list[str]:
    warnings = [
        warning for sample in samples for warning in sample.warnings
    ]
    if coverage.missing_elapsed_seconds:
        warnings.append("one or more integer elapsed-clock samples are missing")
    if coverage.duplicate_elapsed_seconds:
        warnings.append("duplicate elapsed-clock samples were retained")
    if coverage.non_monotonic_capture_indexes:
        warnings.append("capture order is not monotonically increasing")
    return sorted(set(warnings))

