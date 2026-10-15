"""Measure and visualize gameplay HUD roster geometry without changing production.

The tool keeps localization separate from X classification.  It uses a
constrained classical-CV fit around the configured layout and emits auditable
panels for every known live-gameplay slot miss in the sealed development set.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import roster_label_sample as labels

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.vision.player_count import (
    _fit_template_and_mask,
    _load_masked_templates,
)
from splatoon3_ai_coach.vision.roi import pixel_box_from_normalized

SLOTS = ("a1", "a2", "a3", "a4", "o1", "o2", "o3", "o4")
AUDIT_CATEGORIES = (
    "clear localization or clipping",
    "classification with adequate icon coverage",
    "visibility",
    "ambiguous",
    "localization-method uncertain",
)


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
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)


@dataclass(frozen=True)
class Component:
    """Detected icon-like component in a side search region."""

    x: int
    y: int
    width: int
    height: int
    area: int

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)


def normalized_boxes(config: AppConfig, shape: tuple[int, int]) -> list[Box]:
    """Convert the eight configured normalized boxes to pixel rectangles."""
    height, width = shape[:2]
    values = [
        *config.vision.player_count.ally_slots,
        *config.vision.player_count.opponent_slots,
    ]
    return [Box(*pixel_box_from_normalized(value, width, height)) for value in values]


def _activity(image: np.ndarray, box: Box) -> float:
    """Score icon-like color and edge energy in a candidate box."""
    crop = image[box.y1 : box.y2, box.x1 : box.x2]
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    saturation = float(np.mean(hsv[:, :, 1] > 45))
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 40, 120)
    return saturation + float(np.mean(edges > 0)) * 1.5


def _layout_boxes(
    base: list[Box], side: int, dx: float, dy: float, scale: float
) -> list[Box]:
    """Apply an anchor shift and uniform horizontal scale to one side."""
    selected = base[side * 4 : side * 4 + 4]
    anchor = np.mean([box.center[0] for box in selected])
    result: list[Box] = []
    for box in selected:
        cx, cy = box.center
        width = max(8, round(box.width * scale))
        height = max(8, round(box.height * scale))
        center_x = anchor + (cx - anchor) * scale + dx
        center_y = cy + dy
        result.append(
            Box(
                round(center_x - width / 2),
                round(center_y - height / 2),
                round(center_x + width / 2),
                round(center_y + height / 2),
            )
        )
    return result


def fit_layout(
    image: np.ndarray, base: list[Box], side: int
) -> tuple[list[Box], str, float]:
    """Fit M1 and M2 around the fixed layout, returning the simpler winner."""
    height, width = image.shape[:2]
    best: tuple[float, str, list[Box]] | None = None
    for model, scales in (("M1", [1.0]), ("M2", np.arange(0.94, 1.061, 0.03))):
        for scale in scales:
            for dx in np.arange(-16, 17, 8):
                for dy in np.arange(-6, 7, 6):
                    boxes = _layout_boxes(base, side, float(dx), float(dy), float(scale))
                    score = sum(_activity(image, box) for box in boxes)
                    # Prefer M1 unless M2 clearly improves the measured activity.
                    penalty = 0.015 * abs(scale - 1.0) * sum(b.width for b in boxes)
                    candidate = score - penalty
                    if best is None or candidate > best[0]:
                        best = (candidate, model, boxes)
    assert best is not None
    return best[2], best[1], best[0]


def _components(image: np.ndarray, search: Box) -> list[Component]:
    """Find coarse icon components using saturation plus edge morphology."""
    crop = image[search.y1 : search.y2, search.x1 : search.x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    colorful = ((hsv[:, :, 1] > 55) & (hsv[:, :, 2] > 45)).astype(np.uint8)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    edges = (cv2.Canny(gray, 35, 130) > 0).astype(np.uint8)
    mask = np.maximum(colorful, cv2.dilate(edges, np.ones((3, 3), np.uint8)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask)
    output: list[Component] = []
    for row in stats[1:]:
        x, y, w, h, area = map(int, row)
        if area < 80 or h < 25 or w < 20 or h > search.height:
            continue
        output.append(Component(search.x1 + x, search.y1 + y, w, h, area))
    return sorted(output, key=lambda item: item.x)


def _boundary(image: np.ndarray, box: Box) -> Component | None:
    """Return the largest plausible component inside a fitted slot box."""
    components = _components(image, box)
    return max(components, key=lambda item: item.area, default=None)


def _minloc(image: np.ndarray, box: Box, templates: list[Any]) -> tuple[int, int] | None:
    """Return the masked-X best-match origin in full-image coordinates."""
    crop = image[box.y1 : box.y2, box.x1 : box.x2]
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    best: tuple[float, tuple[int, int]] | None = None
    for item in templates:
        fitted = _fit_template_and_mask(gray, item.image, item.mask)
        if fitted is None:
            continue
        template, mask = fitted
        result = cv2.matchTemplate(gray, template, cv2.TM_SQDIFF_NORMED, mask=mask)
        value, _maximum, location, _max_location = cv2.minMaxLoc(result)
        if best is None or value < best[0]:
            best = (float(value), (box.x1 + location[0], box.y1 + location[1]))
    return best[1] if best else None


def _draw_overlay(
    image: np.ndarray,
    base: list[Box],
    dynamic: list[Box],
    components: list[Component | None],
    minlocs: list[tuple[int, int] | None],
    sample_id: str,
) -> np.ndarray:
    """Draw fixed boxes, fitted boxes, observed boundaries and X minLocs."""
    canvas = image.copy()
    for index, (fixed, fitted, component, loc) in enumerate(
        zip(base, dynamic, components, minlocs, strict=True)
    ):
        color = (0, 210, 0) if index < 4 else (255, 120, 0)
        cv2.rectangle(canvas, (fixed.x1, fixed.y1), (fixed.x2, fixed.y2), color, 2)
        cv2.rectangle(
            canvas, (fitted.x1, fitted.y1), (fitted.x2, fitted.y2), (255, 0, 0), 2
        )
        if component:
            cv2.rectangle(
                canvas,
                (component.x, component.y),
                (component.x + component.width, component.y + component.height),
                (0, 0, 255),
                2,
            )
            cv2.circle(canvas, tuple(map(round, component.center)), 5, (0, 0, 255), -1)
        if loc:
            cv2.circle(canvas, loc, 5, (0, 255, 255), -1)
        cv2.putText(
            canvas,
            SLOTS[index],
            (fixed.x1, max(16, fixed.y1 - 3)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
            cv2.LINE_AA,
        )
    cv2.putText(
        canvas,
        f"{sample_id} fixed=green dynamic=blue boundary=red minLoc=yellow",
        (20, max(28, int(image.shape[0] * 0.14))),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return canvas


def _read_frame(video: Path, seconds: float) -> np.ndarray:
    """Read one source frame near a sample timestamp."""
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open {video}")
    capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
    ok, frame = capture.read()
    capture.release()
    if not ok or frame is None:
        raise RuntimeError(f"cannot decode {video} at {seconds}")
    return frame


def _source_video(match: str, video_dir: Path) -> Path:
    """Resolve a same-stem source video."""
    for suffix in labels.VIDEO_SUFFIXES:
        path = video_dir / f"{match}{suffix}"
        if path.exists():
            return path
    raise FileNotFoundError(f"source video not found for {match}")


def survey(
    manifest_path: Path,
    labels_path: Path,
    key_path: Path,
    video_dir: Path,
    output: Path,
    config_path: Path,
) -> dict[str, Any]:
    """Run the geometry survey and write JSON plus audited miss overlays."""
    config = load_config(config_path)
    manifest = json.loads(manifest_path.read_text("utf-8"))
    label_rows = {row["sample_id"]: row for row in csv.DictReader(labels_path.open())}
    key_rows = {
        row["sample_id"]: row
        for row in json.loads(key_path.read_text("utf-8"))["samples"]
    }
    output.mkdir(parents=True, exist_ok=True)
    overlays = output / "known_miss_overlays"
    overlays.mkdir(exist_ok=True)
    templates = _load_masked_templates(config.vision.player_count.template_dir)
    records: list[dict[str, Any]] = []
    base_by_shape: dict[tuple[int, int], list[Box]] = {}
    for item in manifest["samples"]:
        frame = _read_frame(_source_video(item["match"], video_dir), item["video_time"])
        shape_key = frame.shape[:2]
        base = base_by_shape.setdefault(shape_key, normalized_boxes(config, frame.shape))
        fitted: list[Box] = []
        components: list[Component | None] = []
        minlocs: list[tuple[int, int] | None] = []
        model_names: list[str] = []
        for side in (0, 1):
            side_boxes, model, _score = fit_layout(frame, base, side)
            fitted.extend(side_boxes)
            model_names.append(model)
        for box in fitted:
            components.append(_boundary(frame, box))
            minlocs.append(_minloc(frame, box, templates))
        centers = [box.center for box in fitted]
        gaps = [centers[index + 1][0] - centers[index][0] for index in range(7)]
        records.append(
            {
                "sample_id": item["sample_id"],
                "source_class": item["source_class"],
                "match": item["match"],
                "stratum": item["stratum"],
                "video_time": item["video_time"],
                "models": model_names,
                "boxes": [asdict(box) for box in fitted],
                "centers": centers,
                "widths": [box.width for box in fitted],
                "heights": [box.height for box in fitted],
                "gaps": gaps,
                "observed_components": [
                    asdict(component) if component else None for component in components
                ],
                "fixed_center_offsets": [
                    [
                        round(fitted[index].center[0] - base[index].center[0], 3),
                        round(fitted[index].center[1] - base[index].center[1], 3),
                    ]
                    for index in range(8)
                ],
            }
        )
        row = label_rows[item["sample_id"]]
        key = key_rows[item["sample_id"]]
        mismatches = [
            slot
            for index, slot in enumerate(SLOTS)
            if item["source_class"] == "live_gameplay"
            and row[slot] in {"x", "alive"}
            and key["raw_slots"]
            and row[slot] != key["raw_slots"][index]
        ]
        if mismatches:
            overlay = _draw_overlay(
                frame, base, fitted, components, minlocs, item["sample_id"]
            )
            cv2.imwrite(str(overlays / f"{item['sample_id']}.png"), overlay)
            records[-1]["known_live_misses"] = mismatches
            records[-1]["audit_category"] = "localization-method uncertain"
            records[-1]["audit_note"] = "Automatic segmentation is not causal evidence."
    result = {
        "schema_version": 1,
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "fixed_threshold": config.vision.player_count.sqdiff_match_threshold,
        "records": records,
        "audit_categories": list(AUDIT_CATEGORIES),
        "audit_rule": (
            "Known misses require human review of overlays; segmentation alone "
            "cannot assign cause."
        ),
    }
    (output / "geometry.json").write_text(json.dumps(result, indent=2) + "\n", "utf-8")
    return result


def main() -> int:
    """Run the geometry survey CLI."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--video-dir", type=Path, default=Path.home() / "Movies")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=default_config_path())
    args = parser.parse_args()
    survey(
        args.manifest,
        args.labels,
        args.key,
        args.video_dir,
        args.output,
        args.config,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
