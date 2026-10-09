"""Orchestration for local Review timeline screenshot imports."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import cv2
import yaml

from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.vision.stage_maps import MATCH_IDENTITY_FILENAME

from .clock import (
    ClockSource,
    ReviewClock,
    clock_phase,
    filename_clock_hint,
    resolve_clock,
)
from .control_bands import extract_control_segments
from .coverage import build_coverage, derive_death_episodes
from .cursor import detect_cursor
from .cursor_score import CursorScoreReader
from .event_lane import (
    EVENT_LANE_DETECTOR_VERSION,
    calibrate_axis,
    extract_lane_events,
    kit_consistency_warnings,
    select_static_samples,
)
from .label_reader import read_display_text
from .models import ReviewTimelineDataset, ReviewTimelineSample, ScrollDirection
from .roster import extract_cursor_slots
from .second_states import annotate_coverage, build_second_states
from .team_identity import observe_row_hues, resolve_team_identity


def import_timeline(
    directory: Path,
    *,
    config: ReviewTimelineConfig | None = None,
    manifest_path: Path | None = None,
    output_dir: Path | None = None,
    source_recording_id: str | None = None,
    gameplay_analysis_dir: Path | None = None,
    ally_row: Literal["top", "bottom"] | None = None,
    battle_mode_id: str | None = None,
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
    axis = calibrate_axis(samples, config)
    top_h, bottom_h = observe_row_hues(images, selected, config)
    _attach_scores(samples, images, top_h, bottom_h, config)
    identity = resolve_team_identity(
        top_h,
        bottom_h,
        config=config,
        gameplay_analysis_dir=gameplay_analysis_dir,
        ally_row=ally_row,
    )
    control_segments = extract_control_segments(images, selected, axis, identity, config)
    markers, special_events = extract_lane_events(images, selected, axis, config)
    states = build_second_states(samples, markers, special_events, control_segments)
    coverage = annotate_coverage(build_coverage(samples), states)
    episodes = derive_death_episodes(states, markers)
    warnings = _warnings(coverage, samples)
    warnings.extend(warning for episode in episodes for warning in episode.warnings)
    if axis is None:
        warnings.append("timeline axis calibration rejected or unavailable")
    warnings.extend(kit_consistency_warnings(special_events))
    if special_events and all(event.special_id == "unknown" for event in special_events):
        warnings.append(
            "event-lane candidates were retained as unknown; "
            "no template cleared the configured score and margin"
        )
    dataset = ReviewTimelineDataset(
        source_recording_id=source_recording_id,
        battle_mode_id=battle_mode_id or _mode_from_gameplay(gameplay_analysis_dir),
        regulation_seconds=config.regulation_seconds,
        samples=samples,
        second_states=states,
        death_episodes=episodes,
        match_history_events=markers,
        axis_calibration=axis,
        team_identity=identity,
        control_segments=control_segments,
        special_events=special_events,
        coverage=coverage,
        warnings=sorted(set(warnings)),
        detector_versions={"review_event_lane": EVENT_LANE_DETECTOR_VERSION},
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
    axis_summary: object = "unavailable"
    if dataset.axis_calibration is not None:
        axis_summary = dataset.axis_calibration.model_dump()
    lines = [
        "# Review timeline import",
        "",
        f"- images: {coverage.sample_count}",
        f"- clocked samples: {coverage.clocked_sample_count}",
        f"- elapsed range: {coverage.first_elapsed_seconds} to "
        f"{coverage.last_elapsed_seconds}",
        f"- missing seconds: {coverage.missing_elapsed_seconds or 'none'}",
        f"- duplicate seconds: {coverage.duplicate_elapsed_seconds or 'none'}",
        f"- non-monotonic samples: {coverage.non_monotonic_capture_indexes or 'none'}",
        f"- coverage: {'complete' if coverage.complete else 'incomplete'}",
        f"- disagreement seconds: {coverage.disagreement_seconds or 'none'}",
        f"- bounded death episodes: {len(dataset.death_episodes)}",
        f"- static event markers: {len(dataset.match_history_events)}",
        f"- control segments: {len(dataset.control_segments)}",
        f"- special events: {len(dataset.special_events)}",
        f"- ally row: {dataset.team_identity.ally_row or 'unknown'} "
        f"({dataset.team_identity.source})",
        f"- axis calibration: {axis_summary}",
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
    displayed = clock.display if clock else None
    if clock is not None and not plausible_clock(clock, config):
        clock, status, warning = None, "implausible", f"implausible clock {displayed}"
    sample = ReviewTimelineSample(
        sample_id=sample_id,
        capture_index=index,
        image_path=str(path.resolve()),
        image_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        elapsed_seconds=clock.elapsed_seconds if clock else None,
        displayed_clock=displayed,
        clock_source=clock.source.value if clock else None,
        clock_status=status,
        clock_confidence=clock.confidence if clock else 0,
        cursor={
            "x": detection.x,
            "x_norm": (detection.x / image.shape[1] if detection.x is not None else None),
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
    _apply_provenance(sample, entry)
    if warning:
        sample.warnings.append(warning)
    return sample


def _apply_provenance(sample: ReviewTimelineSample, entry: dict[str, Any]) -> None:
    """Copy adapter provenance. Video time never replaces the Review clock."""
    direction = entry.get("scroll_direction")
    if direction in {item.value for item in ScrollDirection}:
        sample.scroll_direction = ScrollDirection(direction)
    if entry.get("source_video_time") is not None:
        sample.source_video_time = float(entry["source_video_time"])
    if entry.get("frame_index") is not None:
        sample.frame_index = int(entry["frame_index"])
    if entry.get("source_pts") is not None:
        sample.source_pts = int(entry["source_pts"])
    confidence = entry.get("clock_confidence")
    if confidence is not None and sample.elapsed_seconds is not None:
        sample.clock_confidence = min(sample.clock_confidence, float(confidence))


def plausible_clock(clock: ReviewClock, config: ReviewTimelineConfig) -> bool:
    """Whether a read label may become per-second evidence (see ``clock_phase``)."""
    return (
        clock_phase(
            clock.display,
            clock.elapsed_seconds,
            regulation_seconds=config.regulation_seconds,
            max_overtime_seconds=config.max_overtime_seconds,
            max_countdown_seconds=config.max_countdown_seconds,
        )
        is not None
    )


def _clock_from_text(text: Any) -> ReviewClock | None:
    read = read_display_text(text, confidence=1.0)
    if read.clock is None:
        return None
    return read.clock.model_copy(update={"source": ClockSource.manifest})


def _image_paths(directory: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in directory.iterdir()
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


def _attach_scores(
    samples: list[ReviewTimelineSample],
    images: dict[str, Any],
    top_hue: float | None,
    bottom_hue: float | None,
    config: ReviewTimelineConfig,
) -> None:
    """Read remaining values and penalty pills after team-row hues are known."""
    reader = CursorScoreReader()
    for sample in samples:
        image = images.get(sample.sample_id)
        if image is None or sample.cursor.x is None:
            continue
        sample.score = reader.read(
            image,
            int(sample.cursor.x),
            top_hue=top_hue,
            bottom_hue=bottom_hue,
            config=config,
        )


def _mode_from_gameplay(directory: Path | None) -> str | None:
    """Read battle_mode_id from a gameplay analysis directory, when present."""
    if directory is None:
        return None
    path = directory / MATCH_IDENTITY_FILENAME
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    mode = payload.get("battle_mode_id")
    return str(mode) if isinstance(mode, str) else None


def _warnings(coverage: Any, samples: list[ReviewTimelineSample]) -> list[str]:
    warnings = [warning for sample in samples for warning in sample.warnings]
    if coverage.missing_elapsed_seconds:
        warnings.append("one or more integer elapsed-clock samples are missing")
        warnings.append("rescan more slowly so every game-clock second is sampled")
    if coverage.duplicate_elapsed_seconds:
        warnings.append("duplicate elapsed-clock samples were retained")
    if coverage.non_monotonic_capture_indexes:
        warnings.append("capture order is not monotonically increasing")
    return sorted(set(warnings))
