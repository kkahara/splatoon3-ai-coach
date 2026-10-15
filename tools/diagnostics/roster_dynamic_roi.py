"""Development-only dynamic ROI roster comparison.

Localization is performed by :mod:`roster_geometry_survey`; classification
uses the production masked-X matcher and the unchanged primary threshold.
Nothing from this tool is wired into vision fusion or coaching.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import cv2
import roster_geometry_survey as geometry
from pydantic import BaseModel

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.vision.player_count import (
    _best_masked_sqdiff,
    _load_masked_templates,
    slot_edge_energy,
)

SLOTS = geometry.SLOTS


class DynamicReading(BaseModel):
    """Dynamic detector output for one development sample."""

    sample_id: str
    source_class: str
    stratum: str
    models: list[str]
    fallback: bool
    localization_confidence: float
    visible: bool
    slots: list[str] | None
    scores: list[float]
    boxes: list[dict[str, int]]


def _normalized_crop(image, box: geometry.Box) -> Any:
    """Crop and normalize one dynamic slot to a canonical image size."""
    crop = image[box.y1 : box.y2, box.x1 : box.x2]
    if crop.size == 0:
        return crop
    return cv2.resize(crop, (96, 96), interpolation=cv2.INTER_AREA)


def classify(
    image,
    boxes: list[geometry.Box],
    templates: list[Any],
    threshold: float,
    min_edge_energy: float,
) -> tuple[bool, list[str] | None, list[float]]:
    """Normalize dynamic crops and classify X/alive at threshold 0.03."""
    crops = [_normalized_crop(image, box) for box in boxes]
    edges = [slot_edge_energy(crop) for crop in crops]
    if any(crop.size == 0 for crop in crops) or min(edges) < min_edge_energy:
        return False, None, []
    scores = [float(_best_masked_sqdiff(crop, templates)[0]) for crop in crops]
    slots = ["x" if score <= threshold else "alive" for score in scores]
    return True, slots, scores


def _source_video(match: str, video_dir: Path) -> Path:
    """Resolve a same-stem source video."""
    for suffix in geometry.labels.VIDEO_SUFFIXES:
        path = video_dir / f"{match}{suffix}"
        if path.exists():
            return path
    raise FileNotFoundError(f"source video not found for {match}")


def _read_frame(video: Path, seconds: float):
    """Read one frame at a stored sample timestamp."""
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open {video}")
    capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
    ok, frame = capture.read()
    capture.release()
    if not ok or frame is None:
        raise RuntimeError(f"cannot decode {video} at {seconds}")
    return frame


def _run_sample(
    sample: dict[str, Any],
    video_dir: Path,
    config: AppConfig,
    templates: list[Any],
    threshold: float,
) -> DynamicReading:
    """Localize and classify one sample."""
    image = _read_frame(_source_video(sample["match"], video_dir), sample["video_time"])
    base = geometry.normalized_boxes(config, image.shape)
    fitted: list[geometry.Box] = []
    models: list[str] = []
    confidences: list[float] = []
    fallback = False
    for side in (0, 1):
        boxes, model, score = geometry.fit_layout(image, base, side)
        if score < 0.8:
            boxes = base[side * 4 : side * 4 + 4]
            fallback = True
        fitted.extend(boxes)
        models.append(model)
        confidences.append(score)
    visible, slots, scores = classify(
        image,
        fitted,
        templates,
        threshold,
        config.vision.player_count.min_slot_edge_energy,
    )
    normalized_confidence = float(
        min(1.0, max(0.0, sum(confidences) / max(1, len(confidences)) / 8.0))
    )
    return DynamicReading(
        sample_id=sample["sample_id"],
        source_class=sample["source_class"],
        stratum=sample["stratum"],
        models=models,
        fallback=fallback,
        localization_confidence=normalized_confidence,
        visible=visible,
        slots=slots,
        scores=scores,
        boxes=[geometry.asdict(box) for box in fitted],
    )


def _human_slots(row: dict[str, str]) -> list[str] | None:
    """Return fully determinable human slot labels."""
    values = [row[slot] for slot in SLOTS]
    return None if "?" in values else values


def _metrics(
    readings: list[DynamicReading],
    labels: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Compute slot, side-count and raw-AvB development metrics."""
    groups: dict[str, list[DynamicReading]] = defaultdict(list)
    for reading in readings:
        groups["overall"].append(reading)
        groups[reading.source_class].append(reading)
        groups[reading.stratum].append(reading)
    result: dict[str, Any] = {}
    for name, items in groups.items():
        tp = fp = fn = tn = 0
        side_total = side_exact = 0
        avb_total = avb_exact = 0
        visible_total = visible_exact = 0
        for item in items:
            row = labels[item.sample_id]
            visible_total += row["hud_visible"] != "?"
            visible_exact += row["hud_visible"] != "?" and item.visible == (
                row["hud_visible"] == "visible"
            )
            human = _human_slots(row)
            if human is None or item.slots is None:
                continue
            for predicted, actual in zip(item.slots, human, strict=True):
                tp += predicted == actual == "x"
                fp += predicted == "x" and actual == "alive"
                fn += predicted == "alive" and actual == "x"
                tn += predicted == actual == "alive"
            for start in (0, 4):
                side_total += 1
                side_exact += sum(
                    value == "alive" for value in item.slots[start : start + 4]
                ) == sum(value == "alive" for value in human[start : start + 4])
            avb_total += 1
            avb_exact += [
                sum(value == "alive" for value in item.slots[:4]),
                sum(value == "alive" for value in item.slots[4:]),
            ] == [
                sum(value == "alive" for value in human[:4]),
                sum(value == "alive" for value in human[4:]),
            ]
        result[name] = {
            "samples": len(items),
            "visible_exact": visible_exact,
            "visible_total": visible_total,
            "slot_tp_x": tp,
            "slot_fp_x": fp,
            "slot_fn_x": fn,
            "slot_tn_alive": tn,
            "slot_precision": tp / (tp + fp) if tp + fp else None,
            "slot_recall": tp / (tp + fn) if tp + fn else None,
            "side_count_exact": side_exact,
            "side_count_total": side_total,
            "side_count_accuracy": side_exact / side_total if side_total else None,
            "raw_avb_exact": avb_exact,
            "raw_avb_total": avb_total,
            "raw_avb_accuracy": avb_exact / avb_total if avb_total else None,
        }
    return result


def compare(
    manifest_path: Path,
    labels_path: Path,
    video_dir: Path,
    output: Path,
    config_path: Path,
) -> dict[str, Any]:
    """Run the dynamic comparison on the frozen development sample."""
    config = load_config(config_path)
    manifest = json.loads(manifest_path.read_text("utf-8"))
    labels = {row["sample_id"]: row for row in csv.DictReader(labels_path.open())}
    templates = _load_masked_templates(config.vision.player_count.template_dir)
    threshold = config.vision.player_count.sqdiff_match_threshold
    readings = [
        _run_sample(item, video_dir, config, templates, threshold)
        for item in manifest["samples"]
    ]
    payload = {
        "schema_version": 1,
        "development_only": True,
        "threshold": threshold,
        "threshold_role": "primary unchanged production threshold",
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "readings": [item.model_dump(mode="json") for item in readings],
        "metrics": _metrics(readings, labels),
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "dynamic_readings.json").write_text(
        json.dumps(payload, indent=2) + "\n", "utf-8"
    )
    return payload


def main() -> int:
    """Run the dynamic ROI comparison CLI."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--video-dir", type=Path, default=Path.home() / "Movies")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=default_config_path())
    args = parser.parse_args()
    compare(args.manifest, args.labels, args.video_dir, args.output, args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
