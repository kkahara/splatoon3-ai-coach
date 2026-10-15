"""Experiment 2: measure and test neighbor contamination in roster ROIs.

This module is deliberately parallel to production. It keeps the original
pixels, templates and 0.03 threshold, and never writes vision or coaching
artifacts. Variant A restricts template placement; Variant B translates a
divider-bounded native-scale crop into the production canvas.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from pydantic import BaseModel

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.vision.player_count import (
    _fit_template_and_mask,
    _load_masked_templates,
)
from splatoon3_ai_coach.vision.roi import pixel_box_from_normalized

SLOTS = ("a1", "a2", "a3", "a4", "o1", "o2", "o3", "o4")
THRESHOLD = 0.03
VIDEO_SUFFIXES = (".mov", ".mp4", ".mkv", ".avi")
TERRITORIES = ("target territory", "divider/overlap territory", "neighbor territory")


@dataclass(frozen=True)
class Box:
    """Pixel-space rectangle."""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return max(1, self.x2 - self.x1)

    @property
    def height(self) -> int:
        return max(1, self.y2 - self.y1)

    @property
    def center_x(self) -> float:
        return (self.x1 + self.x2) / 2


class Reading(BaseModel):
    """Per-slot fixed and isolation measurements."""

    sample_id: str
    source_class: str
    slot: str
    side: str
    human_label: str
    fixed_label: str | None
    fixed_score: float | None
    fixed_minloc_x: float | None
    fixed_minloc_territory: str | None
    target_left: float | None
    target_right: float | None
    target_coverage_frac: float | None
    target_occupancy_frac: float | None
    neighbor_contamination_frac: float | None
    center_offset_px: float | None
    minloc_to_target_center_px: float | None
    isolation_status: str
    divider_confident: bool
    divider_confidence: float
    variant_a_score: float | None
    variant_a_label: str | None
    variant_b_score: float | None
    variant_b_label: str | None
    predicted_label: str | None = None


@dataclass(frozen=True)
class Match:
    """Masked template match with a meaningful center."""

    score: float
    top_left_x: int
    top_left_y: int
    center_x: float
    center_y: float
    template_name: str


def fixed_boxes(config: AppConfig, shape: tuple[int, ...]) -> list[Box]:
    """Return production slot boxes in pixel coordinates."""
    height, width = shape[:2]
    values = [
        *config.vision.player_count.ally_slots,
        *config.vision.player_count.opponent_slots,
    ]
    return [Box(*pixel_box_from_normalized(value, width, height)) for value in values]


def canonical_dividers(boxes: list[Box], side: int) -> tuple[float, float, float]:
    """Return three midpoints between the calibrated slot centers."""
    side_boxes = boxes[side * 4 : side * 4 + 4]
    return tuple(
        (side_boxes[index].center_x + side_boxes[index + 1].center_x) / 2
        for index in range(3)
    )


def _column_profile(image: np.ndarray, row: Box) -> np.ndarray:
    """Compute a smoothed foreground/activity profile across one roster row."""
    crop = image[row.y1 : row.y2, row.x1 : row.x2]
    if crop.size == 0:
        return np.zeros(max(1, row.width), dtype=np.float32)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    saturation = (hsv[:, :, 1] > 45).astype(np.float32)
    edges = (cv2.Canny(gray, 35, 130) > 0).astype(np.float32)
    midgray = ((gray >= 70) & (gray <= 140)).astype(np.float32)
    profile = saturation.mean(axis=0) + 0.8 * edges.mean(axis=0)
    profile += 0.35 * midgray.mean(axis=0)
    return cv2.GaussianBlur(profile.reshape(1, -1), (1, 9), 0).ravel()


def estimate_dividers(
    image: np.ndarray,
    boxes: list[Box],
    side: int,
    *,
    search_px: int = 18,
) -> tuple[tuple[float, float, float], float, bool]:
    """Find three low-activity ordered dividers near the calibrated prior."""
    side_boxes = boxes[side * 4 : side * 4 + 4]
    row = Box(
        side_boxes[0].x1 - 12,
        min(b.y1 for b in side_boxes),
        side_boxes[-1].x2 + 12,
        max(b.y2 for b in side_boxes),
    )
    profile = _column_profile(image, row)
    prior = canonical_dividers(boxes, side)
    candidates = [
        np.arange(round(value) - search_px, round(value) + search_px + 1)
        for value in prior
    ]
    chosen: list[int] = []
    costs: list[float] = []
    previous = row.x1
    for index, values in enumerate(candidates):
        valid = values[values > previous + 20]
        if index < 2:
            valid = valid[valid < prior[index + 1] - 20]
        if valid.size == 0:
            return prior, 0.0, False
        local = np.array([profile[int(value - row.x1)] for value in valid])
        chosen_value = int(valid[int(np.argmin(local))])
        chosen.append(chosen_value)
        costs.append(float(np.min(local)))
        previous = chosen_value
    baseline = np.array([profile[int(value - row.x1)] for value in prior])
    contrast = float(np.mean(baseline) - np.mean(costs))
    confidence = max(0.0, min(1.0, contrast / max(0.05, float(np.mean(baseline)))))
    return tuple(float(value) for value in chosen), confidence, confidence >= 0.08


def territories(
    boxes: list[Box],
    side: int,
    dividers: tuple[float, float, float],
) -> list[tuple[float, float]]:
    """Build four ordered territories from three internal dividers."""
    side_boxes = boxes[side * 4 : side * 4 + 4]
    outer_left = float(side_boxes[0].x1)
    outer_right = float(side_boxes[-1].x2)
    return [
        (outer_left, dividers[0]),
        (dividers[0], dividers[1]),
        (dividers[1], dividers[2]),
        (dividers[2], outer_right),
    ]


def _mask_centroid(mask: np.ndarray) -> tuple[float, float]:
    """Return the centroid of nonzero mask support, not geometric center."""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        height, width = mask.shape[:2]
        return width / 2, height / 2
    return float(xs.mean()), float(ys.mean())


def _matches(
    crop: np.ndarray,
    templates: list[Any],
    *,
    origin_x: int = 0,
    origin_y: int = 0,
    allowed_center_x: tuple[float, float] | None = None,
) -> Match | None:
    """Match templates while optionally restricting their mask-centroid center."""
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    best: Match | None = None
    for item in templates:
        fitted = _fit_template_and_mask(gray, item.image, item.mask)
        if fitted is None:
            continue
        template, mask = fitted
        centroid_x, centroid_y = _mask_centroid(mask)
        result = cv2.matchTemplate(gray, template, cv2.TM_SQDIFF_NORMED, mask=mask)
        x_start, x_end = 0, result.shape[1]
        if allowed_center_x:
            x_start = max(0, int(np.ceil(allowed_center_x[0] - origin_x - centroid_x)))
            x_end = min(
                result.shape[1],
                int(np.floor(allowed_center_x[1] - origin_x - centroid_x)) + 1,
            )
        if x_start >= x_end:
            continue
        window = result[:, x_start:x_end]
        _minimum, _, minimum_location, _ = cv2.minMaxLoc(window)
        x = x_start + minimum_location[0]
        y = minimum_location[1]
        center_x = origin_x + float(x) + centroid_x
        candidate = Match(
            score=float(_minimum),
            top_left_x=origin_x + int(x),
            top_left_y=origin_y + int(y),
            center_x=center_x,
            center_y=origin_y + float(y) + centroid_y,
            template_name=item.name,
        )
        if best is None or candidate.score < best.score:
            best = candidate
    return best


def _label(score: float | None) -> str | None:
    """Convert a score to the production X/alive vocabulary."""
    if score is None:
        return None
    return "x" if score <= THRESHOLD else "alive"


def _territory(
    match: Match | None,
    target: tuple[float, float],
) -> str | None:
    """Classify a match center relative to target and neighboring territory."""
    if match is None:
        return None
    if target[0] <= match.center_x <= target[1]:
        return "target territory"
    overlap = 10.0
    if (
        target[0] - overlap <= match.center_x <= target[0] + overlap
        or target[1] - overlap <= match.center_x <= target[1] + overlap
    ):
        return "divider/overlap territory"
    return "neighbor territory"


def _slot_metrics(
    profile: np.ndarray,
    row: Box,
    box: Box,
    target: tuple[float, float],
    territories_all: list[tuple[float, float]],
    side_index: int,
    match: Match | None,
) -> tuple[float, float, float, float, float, str]:
    """Estimate target extent, coverage and neighbor contamination."""
    x1 = max(0, int(target[0] - row.x1))
    x2 = min(len(profile), int(target[1] - row.x1))
    if x2 <= x1:
        return (0.0, 0.0, 0.0, 0.0, 0.0, "uncertain")
    local = profile[x1:x2]
    threshold = max(0.08, float(np.percentile(local, 40)))
    active = np.flatnonzero(local >= threshold)
    if len(active) < 3:
        return (0.0, 0.0, 0.0, 0.0, 0.0, "uncertain")
    left = row.x1 + x1 + int(active[0])
    right = row.x1 + x1 + int(active[-1]) + 1
    extent = max(1.0, float(right - left))
    clipped = max(0.0, float(box.x1 - left)) + max(0.0, float(right - box.x2))
    coverage = max(0.0, 1.0 - clipped / extent)
    occupancy = min(1.0, extent / box.width)
    neighbor_energy = 0.0
    total_energy = float(
        profile[max(0, box.x1 - row.x1) : min(len(profile), box.x2 - row.x1)].sum()
    )
    for index, territory in enumerate(territories_all):
        if index == side_index:
            continue
        overlap_left = max(box.x1, territory[0])
        overlap_right = min(box.x2, territory[1])
        if overlap_right > overlap_left:
            neighbor_energy += float(
                profile[
                    max(0, int(overlap_left - row.x1)) : min(
                        len(profile), int(overlap_right - row.x1)
                    )
                ].sum()
            )
    contamination = min(1.0, neighbor_energy / max(1e-6, total_energy))
    center = (left + right) / 2
    offset = center - box.center_x
    if coverage < 0.85:
        status = "target_clipped"
    elif contamination >= 0.10:
        status = "target_covered_neighbor_contaminated"
    else:
        status = "well_isolated"
    return extent, coverage, occupancy, contamination, offset, status


def _variant_b(image: np.ndarray, box: Box, territory: tuple[float, float]) -> np.ndarray:
    """Place an unscaled territory-bounded slice into the production canvas."""
    left = max(box.x1, round(territory[0]))
    right = min(box.x2, round(territory[1]))
    source = image[box.y1 : box.y2, left:right]
    canvas = np.zeros((box.height, box.width, 3), dtype=image.dtype)
    fill = (
        np.median(source.reshape(-1, 3), axis=0).astype(image.dtype) if source.size else 0
    )
    canvas[...] = fill
    offset = max(0, (box.width - source.shape[1]) // 2)
    end = min(box.width, offset + source.shape[1])
    canvas[:, offset:end] = source[:, : end - offset]
    return canvas


def _source_video(match: str, video_dir: Path) -> Path:
    """Resolve a sample match to a local source video."""
    for suffix in VIDEO_SUFFIXES:
        path = video_dir / f"{match}{suffix}"
        if path.exists():
            return path
    raise FileNotFoundError(f"source video not found for {match}")


def _read_frame(video: Path, seconds: float) -> np.ndarray:
    """Read one frame at a sample timestamp."""
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open {video}")
    capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
    ok, frame = capture.read()
    capture.release()
    if not ok or frame is None:
        raise RuntimeError(f"cannot decode {video} at {seconds}")
    return frame


def _draw_panel(
    frame: np.ndarray,
    boxes: list[Box],
    territories_all: list[tuple[float, float]],
    dividers: list[tuple[float, float, float]],
    reading_rows: list[Reading],
    title: str,
) -> np.ndarray:
    """Draw ROI, divider, extent and minLoc audit information."""
    image = frame.copy()
    for index, box in enumerate(boxes):
        color = (0, 220, 0) if index < 4 else (255, 160, 0)
        cv2.rectangle(image, (box.x1, box.y1), (box.x2, box.y2), color, 2)
        territory = territories_all[index]
        cv2.line(
            image,
            (round(territory[0]), box.y1),
            (round(territory[0]), box.y2),
            (255, 0, 0),
            2,
        )
        cv2.line(
            image,
            (round(territory[1]), box.y1),
            (round(territory[1]), box.y2),
            (255, 0, 0),
            2,
        )
        cv2.putText(
            image,
            SLOTS[index],
            (box.x1, max(15, box.y1 - 3)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            2,
            cv2.LINE_AA,
        )
        row = reading_rows[index]
        if row.fixed_minloc_x is not None:
            cv2.circle(
                image, (round(row.fixed_minloc_x), box.y1 + 45), 5, (0, 255, 255), -1
            )
    cv2.putText(
        image,
        title,
        (20, 145),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return image


def _write_sheet(images: list[np.ndarray], path: Path, columns: int = 1) -> None:
    """Write a vertically stacked audit sheet."""
    if not images:
        return
    height = max(image.shape[0] for image in images)
    width = max(image.shape[1] for image in images)
    padded = [
        cv2.copyMakeBorder(
            image,
            0,
            height - image.shape[0],
            0,
            width - image.shape[1],
            cv2.BORDER_CONSTANT,
            value=0,
        )
        for image in images
    ]
    rows = []
    for start in range(0, len(padded), columns):
        batch = padded[start : start + columns]
        if len(batch) < columns:
            batch += [np.zeros_like(padded[0])] * (columns - len(batch))
        rows.append(np.hstack(batch))
    cv2.imwrite(str(path), np.vstack(rows))


def _render_selected_sheets(
    samples: list[dict[str, Any]],
    rows: list[Reading],
    video_dir: Path,
    config: AppConfig,
    output: Path,
) -> None:
    """Render the required miss, control, A1/O4 and special-state sheets."""
    by_sample: dict[str, list[Reading]] = defaultdict(list)
    for row in rows:
        by_sample[row.sample_id].append(row)
    miss_ids = [
        sample["sample_id"]
        for sample in samples
        if sample["source_class"] == "live_gameplay"
        and any(
            row.human_label in {"x", "alive"}
            and row.fixed_label is not None
            and row.human_label != row.fixed_label
            for row in by_sample[sample["sample_id"]]
        )
    ]
    correct_x_ids: list[str] = []
    for sample in samples:
        if sample["source_class"] != "live_gameplay":
            continue
        if any(
            row.human_label == row.fixed_label == "x"
            for row in by_sample[sample["sample_id"]]
        ):
            correct_x_ids.append(sample["sample_id"])
    correct_x_ids = correct_x_ids[:13]
    layout_ids = [
        sample["sample_id"]
        for sample in samples
        if sample["source_class"] == "live_gameplay"
        and sample["sample_id"]
        in {row.sample_id for row in rows if row.slot in {"a1", "o4"}}
    ][:12]
    special_ids = [
        sample["sample_id"]
        for sample in samples
        if sample["source_class"] == "live_gameplay" and sample["stratum"] == "margin"
    ][:12]
    categories = {
        "live_misses_13": miss_ids,
        "matched_correct_x": correct_x_ids,
        "a1_o4_layouts": layout_ids,
        "special_ready": special_ids,
    }
    sample_by_id = {sample["sample_id"]: sample for sample in samples}
    for category, sample_ids in categories.items():
        panels: list[np.ndarray] = []
        for sample_id in sample_ids:
            sample = sample_by_id[sample_id]
            frame = _read_frame(
                _source_video(sample["match"], video_dir), sample["video_time"]
            )
            boxes = fixed_boxes(config, frame.shape)
            dividers = []
            territories_all = []
            for side in (0, 1):
                divider, _confidence, _ok = estimate_dividers(frame, boxes, side)
                dividers.append(divider)
                territories_all.extend(territories(boxes, side, divider))
            panels.append(
                _draw_panel(
                    frame,
                    boxes,
                    territories_all,
                    dividers,
                    by_sample[sample_id],
                    f"{category} {sample_id}",
                )
            )
        _write_sheet(panels, output / "sheets" / f"{category}.png")


def analyze_sample(
    sample: dict[str, Any],
    labels_row: dict[str, str],
    key_row: dict[str, Any],
    video_dir: Path,
    config: AppConfig,
    templates: list[Any],
    output: Path,
) -> list[Reading]:
    """Analyze all eight slots in one frozen development sample."""
    frame = _read_frame(_source_video(sample["match"], video_dir), sample["video_time"])
    boxes = fixed_boxes(config, frame.shape)
    dividers: list[tuple[float, float, float]] = []
    confident: list[bool] = []
    confidence_values: list[float] = []
    all_territories: list[tuple[float, float]] = []
    profiles: list[np.ndarray] = []
    rows: list[Box] = []
    for side in (0, 1):
        side_boxes = boxes[side * 4 : side * 4 + 4]
        row = Box(
            side_boxes[0].x1 - 12,
            min(b.y1 for b in side_boxes),
            side_boxes[-1].x2 + 12,
            max(b.y2 for b in side_boxes),
        )
        rows.append(row)
        profile = _column_profile(frame, row)
        profiles.append(profile)
        divider, score, is_confident = estimate_dividers(frame, boxes, side)
        dividers.append(divider)
        confidence_values.append(score)
        confident.append(is_confident)
        all_territories.extend(territories(boxes, side, divider))
    result: list[Reading] = []
    for index, box in enumerate(boxes):
        side = index // 4
        local_index = index % 4
        target = all_territories[index]
        row = rows[side]
        fixed_crop = frame[box.y1 : box.y2, box.x1 : box.x2]
        fixed_match = _matches(fixed_crop, templates, origin_x=box.x1, origin_y=box.y1)
        target_extent, coverage, occupancy, contamination, offset, status = _slot_metrics(
            profiles[side],
            row,
            box,
            target,
            all_territories[side * 4 : side * 4 + 4],
            local_index,
            fixed_match,
        )
        territory = _territory(
            fixed_match,
            target,
        )
        variant_a = _matches(
            fixed_crop,
            templates,
            origin_x=box.x1,
            origin_y=box.y1,
            allowed_center_x=target,
        )
        variant_b_crop = _variant_b(frame, box, target)
        variant_b = _matches(variant_b_crop, templates, origin_x=box.x1, origin_y=box.y1)
        human = labels_row[SLOTS[index]]
        fixed_label = _label(fixed_match.score if fixed_match else None)
        if human == "?":
            fixed_label = fixed_label
        result.append(
            Reading(
                sample_id=sample["sample_id"],
                source_class=sample["source_class"],
                slot=SLOTS[index],
                side="ally" if side == 0 else "opponent",
                human_label=human,
                fixed_label=fixed_label,
                fixed_score=fixed_match.score if fixed_match else None,
                fixed_minloc_x=fixed_match.center_x if fixed_match else None,
                fixed_minloc_territory=territory
                if fixed_label == "x" and human == "alive"
                else None,
                target_left=target[0],
                target_right=target[1],
                target_coverage_frac=coverage,
                target_occupancy_frac=occupancy,
                neighbor_contamination_frac=contamination,
                center_offset_px=offset,
                minloc_to_target_center_px=(
                    fixed_match.center_x - (target[0] + target[1]) / 2
                    if fixed_match
                    else None
                ),
                isolation_status=status,
                divider_confident=confident[side],
                divider_confidence=confidence_values[side],
                variant_a_score=variant_a.score if variant_a else None,
                variant_a_label=_label(variant_a.score if variant_a else None),
                variant_b_score=variant_b.score if variant_b else None,
                variant_b_label=_label(variant_b.score if variant_b else None),
            )
        )
    miss = any(
        labels_row[slot] in {"x", "alive"}
        and key_row["raw_slots"]
        and labels_row[slot] != key_row["raw_slots"][index]
        for index, slot in enumerate(SLOTS)
    )
    if miss:
        panel = _draw_panel(
            frame, boxes, all_territories, dividers, result, sample["sample_id"]
        )
        cv2.imwrite(str(output / "miss_audits" / f"{sample['sample_id']}.png"), panel)
    return result


def _metrics(rows: list[Reading]) -> dict[str, Any]:
    """Compute exact slot, side-count and AvB metrics for one variant."""
    groups: dict[str, list[Reading]] = defaultdict(list)
    for row in rows:
        groups["overall"].append(row)
        groups[row.source_class].append(row)
        groups[row.slot].append(row)
        groups[row.isolation_status].append(row)
    result: dict[str, Any] = {}
    for name, items in groups.items():
        comparable = [row for row in items if row.human_label in {"x", "alive"}]
        tp = sum(row.human_label == row.predicted_label == "x" for row in comparable)
        fp = sum(
            row.predicted_label == "x" and row.human_label == "alive"
            for row in comparable
        )
        fn = sum(
            row.predicted_label == "alive" and row.human_label == "x"
            for row in comparable
        )
        result[name] = {
            "n": len(items),
            "comparable": len(comparable),
            "tp_x": tp,
            "fp_x": fp,
            "fn_x": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "error_rate": (fp + fn) / len(comparable) if comparable else None,
        }
    return result


def _attach_predictions(rows: list[Reading], variant: str) -> None:
    """Expose one variant label to the generic metric helper."""
    for row in rows:
        value = getattr(row, f"{variant}_label")
        object.__setattr__(row, "predicted_label", value)


def run(
    manifest_path: Path,
    labels_path: Path,
    key_path: Path,
    video_dir: Path,
    output: Path,
    config_path: Path,
) -> dict[str, Any]:
    """Run measurement, both variants and development comparison."""
    config = load_config(config_path)
    manifest = json.loads(manifest_path.read_text("utf-8"))
    labels_rows = {row["sample_id"]: row for row in csv.DictReader(labels_path.open())}
    keys = {
        row["sample_id"]: row
        for row in json.loads(key_path.read_text("utf-8"))["samples"]
    }
    templates = _load_masked_templates(config.vision.player_count.template_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "miss_audits").mkdir(exist_ok=True)
    (output / "sheets").mkdir(exist_ok=True)
    rows: list[Reading] = []
    for sample in manifest["samples"]:
        rows.extend(
            analyze_sample(
                sample,
                labels_rows[sample["sample_id"]],
                keys[sample["sample_id"]],
                video_dir,
                config,
                templates,
                output,
            )
        )
    metrics: dict[str, Any] = {}
    for variant in ("fixed", "variant_a", "variant_b"):
        if variant == "fixed":
            for row in rows:
                object.__setattr__(row, "predicted_label", row.fixed_label)
        else:
            _attach_predictions(rows, variant)
        metrics[variant] = _metrics(rows)
    _render_selected_sheets(manifest["samples"], rows, video_dir, config, output)
    payload = {
        "schema_version": 1,
        "development_only": True,
        "primary_threshold": THRESHOLD,
        "mask_center_method": "centroid of nonzero X-mask support",
        "rows": [row.model_dump(mode="json") for row in rows],
        "metrics": metrics,
    }
    (output / "isolation.json").write_text(json.dumps(payload, indent=2) + "\n", "utf-8")
    return payload


def main() -> int:
    """Run the slot-isolation experiment."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--video-dir", type=Path, default=Path.home() / "Movies")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=default_config_path())
    args = parser.parse_args()
    run(args.manifest, args.labels, args.key, args.video_dir, args.output, args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
