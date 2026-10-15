"""Read-only X appearance study: live false-negative vs correctly detected X.

Development diagnostic over the sealed roster baseline. It re-decodes each
labeled frame exactly as the label sampler did (verified against the sealed
``image_sha256``), keeps the native-resolution production ROI, the production
templates/masks and the 0.03 threshold, and only measures and visualizes.
Nothing here changes the detector, templates, masks, threshold or fusion.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from pydantic import BaseModel
from roster_label_sample import sample_image

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.vision.player_count import (
    _X_GRAY_HI,
    _X_GRAY_LO,
    MaskedXTemplate,
    _fit_template_and_mask,
    _load_masked_templates,
    slot_edge_energy,
)
from splatoon3_ai_coach.vision.roi import pixel_box_from_normalized

SLOTS = ("a1", "a2", "a3", "a4", "o1", "o2", "o3", "o4")
THRESHOLD = 0.03
VIDEO_SUFFIXES = (".mov", ".mp4", ".mkv", ".avi")
CONTEXT_MARGIN_PX = 24
NEUTRAL_MAX_SAT = 50
BRIGHT_GRAY = 180
SCALE = 3


@dataclass(frozen=True)
class TemplateHit:
    """Best masked SQDIFF placement of one template inside one crop."""

    name: str
    score: float
    x: int
    y: int
    template: np.ndarray
    mask: np.ndarray


Visual = tuple[np.ndarray, TemplateHit, np.ndarray]


class SlotAppearance(BaseModel):
    """All measurements for one human-labeled X slot."""

    sample_id: str
    match: str
    video_time: float
    slot: str
    side: str
    group: str
    human_label: str
    frame_verified: bool
    stored_score: float
    stored_label: str
    roi_w: int
    roi_h: int
    best_template: str
    best_score: float
    recomputed_label: str
    second_template: str
    second_score: float
    template_scores: dict[str, float]
    minloc_x: int
    minloc_y: int
    template_w: int
    template_h: int
    template_right_gap_px: int
    template_bottom_gap_px: int
    mask_src_mean: float
    mask_src_std: float
    mask_tmpl_mean: float
    mask_tmpl_std: float
    mask_mean_diff: float
    mask_in_gate_frac: float
    mask_bright_frac: float
    mask_dark_frac: float
    mask_saturated_frac: float
    mask_zncc: float
    x_found: bool
    x_left: int | None
    x_top: int | None
    x_width: int | None
    x_height: int | None
    x_area: int | None
    x_arm_thickness_px: float | None
    x_touches: list[str]
    x_center_dx: float | None
    x_center_dy: float | None
    x_angle_down_deg: float | None
    x_angle_up_deg: float | None
    x_gray_mean: float | None
    x_ring_contrast: float | None
    diag_edge_frac: float
    diag_edge_mean: float
    roi_gray_mean: float
    roi_gray_std: float
    roi_bright_frac: float
    roi_saturated_frac: float
    team_hue: float | None
    edge_energy: float
    roi_x1: int
    roi_x2: int
    context_best_score: float
    context_best_template: str
    context_center_x: float
    context_center_y: float
    reachable_center_x: list[float]
    context_center_inside_roi: bool
    context_overhang_px: dict[str, int]


def match_templates(
    gray: np.ndarray, templates: list[MaskedXTemplate]
) -> list[TemplateHit]:
    """Return every template's production masked SQDIFF best placement."""
    hits: list[TemplateHit] = []
    for item in templates:
        fitted = _fit_template_and_mask(gray, item.image, item.mask)
        if fitted is None:
            continue
        template, mask = fitted
        result = cv2.matchTemplate(gray, template, cv2.TM_SQDIFF_NORMED, mask=mask)
        result = np.where(np.isfinite(result), result, 1.0)
        score, _, loc, _ = cv2.minMaxLoc(result)
        hits.append(TemplateHit(item.name, float(score), loc[0], loc[1], template, mask))
    return sorted(hits, key=lambda hit: hit.score)


def masked_support(
    gray: np.ndarray, hsv: np.ndarray, hit: TemplateHit
) -> dict[str, float]:
    """Compare source pixels and template pixels under the winning mask."""
    h, w = hit.template.shape[:2]
    support = hit.mask > 0
    src = gray[hit.y : hit.y + h, hit.x : hit.x + w][support].astype(np.float64)
    tmpl = hit.template[support].astype(np.float64)
    sat = hsv[hit.y : hit.y + h, hit.x : hit.x + w, 1][support]
    src_c, tmpl_c = src - src.mean(), tmpl - tmpl.mean()
    denom = float(np.sqrt((src_c**2).sum() * (tmpl_c**2).sum()))
    return {
        "mask_src_mean": float(src.mean()),
        "mask_src_std": float(src.std()),
        "mask_tmpl_mean": float(tmpl.mean()),
        "mask_tmpl_std": float(tmpl.std()),
        "mask_mean_diff": float((src - tmpl).mean()),
        "mask_in_gate_frac": float(((src >= _X_GRAY_LO) & (src <= _X_GRAY_HI)).mean()),
        "mask_bright_frac": float((src > _X_GRAY_HI).mean()),
        "mask_dark_frac": float((src < _X_GRAY_LO).mean()),
        "mask_saturated_frac": float((sat > NEUTRAL_MAX_SAT).mean()),
        "mask_zncc": float((src_c * tmpl_c).sum() / denom) if denom > 0 else 0.0,
    }


def neutral_gray_mask(gray: np.ndarray, hsv: np.ndarray) -> np.ndarray:
    """Pixels matching the X marker's neutral mid-gray paint (template-free)."""
    gate = (gray >= _X_GRAY_LO) & (gray <= _X_GRAY_HI) & (hsv[:, :, 1] <= NEUTRAL_MAX_SAT)
    raw = gate.astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    raw = cv2.morphologyEx(raw, cv2.MORPH_OPEN, kernel)
    return cv2.morphologyEx(raw, cv2.MORPH_CLOSE, kernel)


def _arm_angle(xs: np.ndarray, ys: np.ndarray) -> float | None:
    """Principal-axis angle in degrees (image y down) of one arm's pixels."""
    if len(xs) < 10:
        return None
    cov = np.cov(np.vstack([xs, ys]).astype(np.float64))
    values, vectors = np.linalg.eigh(cov)
    vx, vy = vectors[:, int(np.argmax(values))]
    angle = float(np.degrees(np.arctan2(vy, vx)))
    return angle + 180 if angle < -90 else angle - 180 if angle > 90 else angle


def x_geometry(gray: np.ndarray, hsv: np.ndarray) -> dict[str, Any]:
    """Locate the largest neutral-gray diagonal component and describe it.

    Automatic segmentation only; every value is audited visually in the
    contact sheets and never used alone to attribute a failure cause.
    """
    empty: dict[str, Any] = {"x_found": False, "x_touches": []}
    for key in (
        "x_left x_top x_width x_height x_area x_arm_thickness_px x_center_dx "
        "x_center_dy x_angle_down_deg x_angle_up_deg x_gray_mean x_ring_contrast"
    ).split():
        empty[key] = None
    mask = neutral_gray_mask(gray, hsv)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if n <= 1:
        return empty
    candidates = [
        i
        for i in range(1, n)
        if stats[i, cv2.CC_STAT_WIDTH] >= 20 and stats[i, cv2.CC_STAT_HEIGHT] >= 20
    ]
    if not candidates:
        return empty
    keep = max(candidates, key=lambda i: stats[i, cv2.CC_STAT_AREA])
    component = labels == keep
    return _describe_component(gray, component, stats[keep])


def _describe_component(
    gray: np.ndarray, component: np.ndarray, stat: np.ndarray
) -> dict[str, Any]:
    """Extent, boundary contact, arm orientation and contrast of one component."""
    h, w = gray.shape
    left, top, width, height, area = (int(value) for value in stat[:5])
    touches = [
        side
        for side, hit in (
            ("left", left <= 0),
            ("top", top <= 0),
            ("right", left + width >= w),
            ("bottom", top + height >= h),
        )
        if hit
    ]
    ys, xs = np.nonzero(component)
    cx, cy = xs.mean(), ys.mean()
    down = (xs - cx) * (ys - cy) > 0
    ring = cv2.dilate(component.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    ring &= ~component
    x_mean = float(gray[component].mean())
    return {
        "x_found": True,
        "x_left": left,
        "x_top": top,
        "x_width": width,
        "x_height": height,
        "x_area": area,
        "x_arm_thickness_px": area / max(1.0, 2.0 * float(np.hypot(width, height))),
        "x_touches": touches,
        "x_center_dx": float(left + width / 2 - w / 2),
        "x_center_dy": float(top + height / 2 - h / 2),
        "x_angle_down_deg": _arm_angle(xs[down], ys[down]),
        "x_angle_up_deg": _arm_angle(xs[~down], ys[~down]),
        "x_gray_mean": x_mean,
        "x_ring_contrast": abs(x_mean - float(gray[ring].mean())) if ring.any() else None,
    }


def diagonal_edges(gray: np.ndarray) -> tuple[float, float]:
    """Magnitude share and mean of gradients within 15 degrees of either diagonal."""
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    magnitude = np.hypot(gx, gy)
    angle = np.degrees(np.arctan2(gy, gx)) % 180
    diagonal = (np.abs(angle - 45) <= 15) | (np.abs(angle - 135) <= 15)
    total = float(magnitude.sum())
    share = float(magnitude[diagonal].sum() / total) if total > 0 else 0.0
    return share, float(magnitude[diagonal].mean()) if diagonal.any() else 0.0


def roi_tone(gray: np.ndarray, hsv: np.ndarray) -> dict[str, Any]:
    """Brightness, artwork and team-colour measurements over the whole ROI."""
    saturated = (hsv[:, :, 1] > 100) & (hsv[:, :, 2] > 100)
    bright = (gray > BRIGHT_GRAY) & (hsv[:, :, 1] <= NEUTRAL_MAX_SAT)
    return {
        "roi_gray_mean": float(gray.mean()),
        "roi_gray_std": float(gray.std()),
        "roi_bright_frac": float(bright.mean()),
        "roi_saturated_frac": float(saturated.mean()),
        "team_hue": float(np.median(hsv[:, :, 0][saturated]) * 2)
        if saturated.any()
        else None,
    }


def _context_crop(
    frame: np.ndarray, box: tuple[int, int, int, int]
) -> tuple[np.ndarray, int, int]:
    """Native pixels around the ROI with ``CONTEXT_MARGIN_PX`` on every side."""
    x1, y1, x2, y2 = box
    fh, fw = frame.shape[:2]
    cx1, cy1 = max(0, x1 - CONTEXT_MARGIN_PX), max(0, y1 - CONTEXT_MARGIN_PX)
    cx2, cy2 = min(fw, x2 + CONTEXT_MARGIN_PX), min(fh, y2 + CONTEXT_MARGIN_PX)
    return frame[cy1:cy2, cx1:cx2], cx1, cy1


def render_context(
    frame: np.ndarray,
    box: tuple[int, int, int, int],
    templates: list[MaskedXTemplate],
    label: str,
) -> np.ndarray:
    """ROI border (green) and the margin-allowed best template mask (magenta)."""
    crop, cx1, cy1 = _context_crop(frame, box)
    best = match_templates(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), templates)[0]
    canvas = _up(crop)
    h, w = best.template.shape[:2]
    full = np.zeros(crop.shape[:2], dtype=np.uint8)
    full[best.y : best.y + h, best.x : best.x + w] = best.mask
    full = _up(full)
    outline = full - cv2.erode(full, np.ones((3, 3)))
    canvas[outline > 0] = (255, 0, 255)
    x1, y1, x2, y2 = box
    cv2.rectangle(
        canvas,
        ((x1 - cx1) * SCALE, (y1 - cy1) * SCALE),
        ((x2 - cx1) * SCALE - 1, (y2 - cy1) * SCALE - 1),
        (0, 255, 0),
        2,
    )
    cv2.putText(
        canvas,
        f"{label} ctx {best.name} {best.score:.4f}",
        (6, 22),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return canvas


def context_probe(
    frame: np.ndarray, box: tuple[int, int, int, int], templates: list[MaskedXTemplate]
) -> dict[str, Any]:
    """Diagnostic only: same templates on native pixels with a margin around the ROI.

    Answers whether the X is template-compatible when placement is not limited
    by the ROI border. Not a proposed detector change.
    """
    x1, y1, x2, y2 = box
    crop, cx1, cy1 = _context_crop(frame, box)
    best = match_templates(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), templates)[0]
    h, w = best.template.shape[:2]
    left, top = cx1 + best.x, cy1 + best.y
    ys, xs = np.nonzero(best.mask)
    center_x, center_y = left + xs.mean(), top + ys.mean()
    return {
        "roi_x1": x1,
        "roi_x2": x2,
        "context_best_score": best.score,
        "context_best_template": best.name,
        "context_center_x": float(center_x),
        "context_center_y": float(center_y),
        "reachable_center_x": [float(x1 + xs.mean()), float(x2 - w + xs.mean())],
        "context_center_inside_roi": bool(x1 <= center_x <= x2 and y1 <= center_y <= y2),
        "context_overhang_px": {
            "left": max(0, x1 - left),
            "top": max(0, y1 - top),
            "right": max(0, left + w - x2),
            "bottom": max(0, top + h - y2),
        },
    }


def measure_slot(
    frame: np.ndarray,
    box: tuple[int, int, int, int],
    templates: list[MaskedXTemplate],
) -> tuple[dict[str, Any], list[TemplateHit]]:
    """Measure one native-resolution production ROI."""
    x1, y1, x2, y2 = box
    roi = frame[y1:y2, x1:x2]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hits = match_templates(gray, templates)
    best, second = hits[0], hits[1]
    th, tw = best.template.shape[:2]
    share, mean = diagonal_edges(gray)
    values: dict[str, Any] = {
        "roi_w": x2 - x1,
        "roi_h": y2 - y1,
        "best_template": best.name,
        "best_score": best.score,
        "recomputed_label": "x" if best.score <= THRESHOLD else "alive",
        "second_template": second.name,
        "second_score": second.score,
        "template_scores": {hit.name: hit.score for hit in hits},
        "minloc_x": best.x,
        "minloc_y": best.y,
        "template_w": tw,
        "template_h": th,
        "template_right_gap_px": (x2 - x1) - (best.x + tw),
        "template_bottom_gap_px": (y2 - y1) - (best.y + th),
        "diag_edge_frac": share,
        "diag_edge_mean": mean,
        "edge_energy": slot_edge_energy(roi),
    }
    values.update(masked_support(gray, hsv, best))
    values.update(x_geometry(gray, hsv))
    values.update(roi_tone(gray, hsv))
    values.update(context_probe(frame, box, templates))
    return values, hits


def _up(image: np.ndarray) -> np.ndarray:
    """Nearest-neighbour upscale so native pixels stay visible."""
    return cv2.resize(image, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_NEAREST)


def render_tiles(roi: np.ndarray, hit: TemplateHit) -> list[np.ndarray]:
    """Original ROI, template overlay at minLoc, mask support, gate classes, X mask."""
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    h, w = hit.template.shape[:2]
    full = np.zeros(gray.shape, dtype=bool)
    full[hit.y : hit.y + h, hit.x : hit.x + w] = hit.mask > 0
    overlay = roi.copy()
    placed = np.zeros_like(gray)
    placed[hit.y : hit.y + h, hit.x : hit.x + w] = hit.template
    overlay[full] = (0.45 * roi[full] + 0.55 * cv2.merge([placed] * 3)[full]).astype(
        np.uint8
    )
    outline = full.astype(np.uint8) - cv2.erode(full.astype(np.uint8), np.ones((3, 3)))
    overlay[outline > 0] = (255, 0, 255)
    cv2.rectangle(
        overlay, (hit.x, hit.y), (hit.x + w - 1, hit.y + h - 1), (0, 255, 255), 1
    )
    support = (roi * 0.2).astype(np.uint8)
    support[full] = roi[full]
    classes = np.zeros_like(roi)
    gate = (gray >= _X_GRAY_LO) & (gray <= _X_GRAY_HI)
    classes[full & gate] = (0, 200, 0)
    classes[full & (gray > _X_GRAY_HI)] = (0, 0, 255)
    classes[full & (gray < _X_GRAY_LO)] = (255, 80, 0)
    classes[full & (hsv[:, :, 1] > NEUTRAL_MAX_SAT)] = (0, 220, 255)
    xmask = cv2.cvtColor(neutral_gray_mask(gray, hsv), cv2.COLOR_GRAY2BGR)
    return [_up(tile) for tile in (roi, overlay, support, classes, xmask)]


def render_row(roi: np.ndarray, hit: TemplateHit, row: SlotAppearance) -> np.ndarray:
    """One labelled contact-sheet row for a slot."""
    tiles = render_tiles(roi, hit)
    height = max(tile.shape[0] for tile in tiles)
    padded = [
        cv2.copyMakeBorder(tile, 0, height - tile.shape[0], 0, 6, cv2.BORDER_CONSTANT)
        for tile in tiles
    ]
    strip = np.hstack(padded)
    lines = [
        f"{row.sample_id} {row.slot} [{row.group}] human={row.human_label} "
        f"stored={row.stored_score:.4f}->{row.stored_label}",
        f"best {row.best_template} {row.best_score:.4f} ({row.recomputed_label})  "
        f"2nd {row.second_template} {row.second_score:.4f}  minLoc=({row.minloc_x},"
        f"{row.minloc_y}) tmpl {row.template_w}x{row.template_h}",
        f"mask src/tmpl mean {row.mask_src_mean:.0f}/{row.mask_tmpl_mean:.0f} "
        f"in-gate {row.mask_in_gate_frac:.2f} bright {row.mask_bright_frac:.2f} "
        f"dark {row.mask_dark_frac:.2f} sat {row.mask_saturated_frac:.2f} "
        f"zncc {row.mask_zncc:.2f}",
        f"X {row.x_width}x{row.x_height} touches={','.join(row.x_touches) or '-'} "
        f"ctx best {row.context_best_score:.4f} overhang={row.context_overhang_px}",
    ]
    text = np.zeros((22 * len(lines) + 8, strip.shape[1], 3), dtype=np.uint8)
    for index, line in enumerate(lines):
        cv2.putText(
            text,
            line,
            (6, 20 + 22 * index),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (235, 235, 235),
            1,
            cv2.LINE_AA,
        )
    return np.vstack([strip, text, np.full((6, strip.shape[1], 3), 90, np.uint8)])


def write_sheet(rows: list[np.ndarray], path: Path, title: str) -> None:
    """Stack rendered rows under a title bar."""
    if not rows:
        return
    width = max(row.shape[1] for row in rows)
    padded = [
        cv2.copyMakeBorder(row, 0, 0, 0, width - row.shape[1], cv2.BORDER_CONSTANT)
        for row in rows
    ]
    header = np.zeros((40, width, 3), dtype=np.uint8)
    cv2.putText(
        header,
        title,
        (8, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.vstack([header, *padded]))


def template_gallery(templates: list[MaskedXTemplate], path: Path) -> None:
    """Production templates with their load-time X masks, native pixels upscaled."""
    tiles = []
    for item in templates:
        h, w = item.image.shape
        canvas = np.zeros((110 * SCALE + 30, 2 * 100 * SCALE, 3), dtype=np.uint8)
        canvas[: h * SCALE, : w * SCALE] = _up(
            cv2.cvtColor(item.image, cv2.COLOR_GRAY2BGR)
        )
        canvas[: h * SCALE, 100 * SCALE : 100 * SCALE + w * SCALE] = _up(
            cv2.cvtColor(item.mask, cv2.COLOR_GRAY2BGR)
        )
        cv2.putText(
            canvas,
            f"{item.name} {w}x{h}",
            (6, 110 * SCALE + 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        tiles.append(canvas)
    write_sheet(tiles, path, "production templates (left) and derived X masks (right)")


def _source_video(match: str, video_dir: Path) -> Path:
    """Resolve a sample match to a local source video."""
    for suffix in VIDEO_SUFFIXES:
        path = video_dir / f"{match}{suffix}"
        if path.exists():
            return path
    raise FileNotFoundError(f"source video not found for {match}")


def decode_verified(
    sample: dict[str, Any], key: dict[str, Any], video_dir: Path, config: AppConfig
) -> tuple[np.ndarray, bool]:
    """Decode with the label sampler's seek and verify against the sealed image hash."""
    capture = cv2.VideoCapture(str(_source_video(sample["match"], video_dir)))
    capture.set(cv2.CAP_PROP_POS_MSEC, sample["video_time"] * 1000.0)
    ok, frame = capture.read()
    capture.release()
    if not ok or frame is None:
        raise RuntimeError(f"cannot decode {sample['sample_id']}")
    rendered = sample_image(frame, config, sample["sample_id"])
    encoded = cv2.imencode(".png", rendered)[1].tobytes()
    return frame, hashlib.sha256(encoded).hexdigest() == key["image_sha256"]


def slot_boxes(
    config: AppConfig, shape: tuple[int, ...]
) -> list[tuple[int, int, int, int]]:
    """Production slot boxes in native pixel coordinates."""
    height, width = shape[:2]
    values = [
        *config.vision.player_count.ally_slots,
        *config.vision.player_count.opponent_slots,
    ]
    return [pixel_box_from_normalized(value, width, height) for value in values]


def x_slots(
    manifest: dict[str, Any], labels: dict[str, dict[str, str]], keys: dict[str, Any]
) -> list[tuple[dict[str, Any], int, str]]:
    """Every live human-X slot with a stored reading, tagged fn or tp."""
    result = []
    for sample in manifest["samples"]:
        key = keys[sample["sample_id"]]
        if sample["source_class"] != "live_gameplay" or not key["raw_slots"]:
            continue
        for index, slot in enumerate(SLOTS):
            if labels[sample["sample_id"]][slot] != "x":
                continue
            group = "fn" if key["raw_slots"][index] == "alive" else "tp"
            result.append((sample, index, group))
    return result


def pair_rows(rows: list[SlotAppearance]) -> list[dict[str, Any]]:
    """Within-frame FN/TP pairs: same side first, then cross side."""
    pairs = []
    for fn in (row for row in rows if row.group == "fn"):
        partners = [
            row for row in rows if row.group == "tp" and row.sample_id == fn.sample_id
        ]
        for tp in partners:
            pairs.append(
                {
                    "sample_id": fn.sample_id,
                    "fn_slot": fn.slot,
                    "tp_slot": tp.slot,
                    "same_side": tp.side == fn.side,
                    "fn": fn.model_dump(mode="json"),
                    "tp": tp.model_dump(mode="json"),
                }
            )
    return sorted(pairs, key=lambda pair: (not pair["same_side"], pair["sample_id"]))


def matched_control(
    fn: SlotAppearance, rows: list[SlotAppearance]
) -> SlotAppearance | None:
    """Deterministic TP control.

    Priority: same frame and side, same frame, same match and slot, same slot.
    """
    tps = [row for row in rows if row.group == "tp"]
    rules = (
        lambda row: row.sample_id == fn.sample_id and row.side == fn.side,
        lambda row: row.sample_id == fn.sample_id,
        lambda row: row.match == fn.match and row.slot == fn.slot,
        lambda row: row.slot == fn.slot,
    )
    for rule in rules:
        found = sorted((row for row in tps if rule(row)), key=lambda row: row.sample_id)
        if found:
            return found[0]
    return None


def summarize(rows: list[SlotAppearance]) -> dict[str, Any]:
    """FN vs TP distributions and categorical breakdowns."""
    numeric = (
        "best_score second_score mask_src_mean mask_mean_diff mask_in_gate_frac "
        "mask_bright_frac mask_dark_frac mask_saturated_frac mask_zncc x_width x_height "
        "x_arm_thickness_px x_center_dx x_center_dy x_angle_down_deg x_angle_up_deg "
        "x_gray_mean x_ring_contrast diag_edge_frac diag_edge_mean roi_gray_mean "
        "roi_gray_std roi_bright_frac roi_saturated_frac team_hue edge_energy "
        "template_right_gap_px context_best_score"
    ).split()
    out: dict[str, Any] = {"numeric": {}, "categorical": {}}
    for field in numeric:
        out["numeric"][field] = {}
        for group in ("fn", "tp"):
            values = [
                getattr(row, field)
                for row in rows
                if row.group == group and getattr(row, field) is not None
            ]
            out["numeric"][field][group] = _stats(values)
    for field in ("slot", "best_template", "touch_any"):
        table: dict[str, dict[str, int]] = {}
        for row in rows:
            value = (
                ",".join(row.x_touches) or "none"
                if field == "touch_any"
                else str(getattr(row, field))
            )
            table.setdefault(value, {"fn": 0, "tp": 0})[row.group] += 1
        out["categorical"][field] = dict(sorted(table.items()))
    out["positions"] = {
        slot: sorted(
            (
                {
                    "group": row.group,
                    "sample_id": row.sample_id,
                    "center_x": round(row.context_center_x, 1),
                    "reachable_center_x": [round(v, 1) for v in row.reachable_center_x],
                }
                for row in rows
                if row.slot == slot
            ),
            key=lambda item: item["center_x"],
        )
        for slot in SLOTS
    }
    return out


def _stats(values: list[float]) -> dict[str, float | int | None]:
    """n, min, quartiles and max."""
    if not values:
        return {"n": 0, "min": None, "q1": None, "median": None, "q3": None, "max": None}
    array = np.asarray(values, dtype=np.float64)
    q1, median, q3 = (float(value) for value in np.percentile(array, [25, 50, 75]))
    return {
        "n": len(values),
        "min": float(array.min()),
        "q1": q1,
        "median": median,
        "q3": q3,
        "max": float(array.max()),
    }


def _load(baseline: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load the sealed manifest, labels and key."""
    manifest = json.loads((baseline / "sample_manifest.json").read_text("utf-8"))
    with (baseline / "labels.csv").open(encoding="utf-8") as handle:
        labels = {row["sample_id"]: row for row in csv.DictReader(handle)}
    keys = {
        row["sample_id"]: row
        for row in json.loads((baseline / "key.json").read_text("utf-8"))["samples"]
    }
    return manifest, labels, keys


def measure_all(
    baseline: Path, video_dir: Path, config: AppConfig, templates: list[MaskedXTemplate]
) -> tuple[list[SlotAppearance], dict[tuple[str, str], Visual]]:
    """Measure every live human-X slot; keep crops and winning hits for rendering."""
    manifest, labels, keys = _load(baseline)
    frames: dict[str, tuple[np.ndarray, bool]] = {}
    rows: list[SlotAppearance] = []
    visuals: dict[tuple[str, str], Visual] = {}
    for sample, index, group in x_slots(manifest, labels, keys):
        sid = sample["sample_id"]
        if sid not in frames:
            frames[sid] = decode_verified(sample, keys[sid], video_dir, config)
        frame, verified = frames[sid]
        box = slot_boxes(config, frame.shape)[index]
        values, hits = measure_slot(frame, box, templates)
        key = keys[sid]
        rows.append(
            SlotAppearance(
                sample_id=sid,
                match=sample["match"],
                video_time=sample["video_time"],
                slot=SLOTS[index],
                side="ally" if index < 4 else "opponent",
                group=group,
                human_label="x",
                frame_verified=verified,
                stored_score=key["scores"][index],
                stored_label=key["raw_slots"][index],
                **values,
            )
        )
        visuals[(sid, SLOTS[index])] = (
            frame[box[1] : box[3], box[0] : box[2]],
            hits[0],
            render_context(frame, box, templates, f"{sid} {SLOTS[index]} {group}"),
        )
    return rows, visuals


def render_sheets(
    rows: list[SlotAppearance],
    visuals: dict[tuple[str, str], Visual],
    output: Path,
) -> list[dict[str, Any]]:
    """Write FN, matched-TP, within-frame pair and R013 sheets."""
    by_key = {(row.sample_id, row.slot): row for row in rows}

    def rendered(row: SlotAppearance) -> np.ndarray:
        roi, hit, _context = visuals[(row.sample_id, row.slot)]
        return render_row(roi, hit, row)

    def context(row: SlotAppearance) -> np.ndarray:
        return visuals[(row.sample_id, row.slot)][2]

    legend = "tiles: ROI | template@minLoc | mask support | gate classes | neutral-gray X"
    fns = [row for row in rows if row.group == "fn"]
    controls = [matched_control(fn, rows) for fn in fns]
    write_sheet(
        [rendered(r) for r in fns],
        output / "sheets" / "fn_live.png",
        f"live false-negative X ({len(fns)}) - {legend}",
    )
    write_sheet(
        [rendered(r) for r in controls if r],
        output / "sheets" / "tp_matched.png",
        f"matched correctly detected X, same order as fn_live - {legend}",
    )
    interleaved = []
    for fn, tp in zip(fns, controls, strict=True):
        interleaved.append(rendered(fn))
        if tp:
            interleaved.append(rendered(tp))
    write_sheet(
        interleaved,
        output / "sheets" / "fn_vs_tp_interleaved.png",
        "each FN followed by its matched TP control",
    )
    pairs = pair_rows(rows)
    pair_images = []
    for pair in pairs:
        pair_images.append(rendered(by_key[(pair["sample_id"], pair["fn_slot"])]))
        pair_images.append(rendered(by_key[(pair["sample_id"], pair["tp_slot"])]))
    write_sheet(
        pair_images,
        output / "sheets" / "within_frame_pairs.png",
        "within-frame pairs: FN row then TP row (same side first)",
    )
    primary = [
        pair for pair in pairs if pair["sample_id"] == "R013" and pair["same_side"]
    ]
    write_sheet(
        [
            img
            for pair in primary
            for img in (
                rendered(by_key[("R013", pair["fn_slot"])]),
                rendered(by_key[("R013", pair["tp_slot"])]),
            )
        ],
        output / "sheets" / "R013_primary.png",
        "R013 primary case: A1 (FN) vs A3 (TP)",
    )
    context_rows = []
    for fn, tp in zip(fns, controls, strict=True):
        tiles = [context(fn)] + ([context(tp)] if tp else [])
        height = max(tile.shape[0] for tile in tiles)
        context_rows.append(
            np.hstack(
                [
                    cv2.copyMakeBorder(
                        t, 0, height - t.shape[0], 0, 8, cv2.BORDER_CONSTANT
                    )
                    for t in tiles
                ]
            )
        )
    write_sheet(
        context_rows,
        output / "sheets" / "context_probe.png",
        f"ROI (green) and best template with {CONTEXT_MARGIN_PX}px margin "
        "(magenta): FN left, matched TP right",
    )
    tps = sorted(
        (row for row in rows if row.group == "tp"), key=lambda row: row.best_score
    )
    write_sheet(
        [rendered(r) for r in tps[-12:]],
        output / "sheets" / "tp_weakest12.png",
        "12 weakest-scoring correctly detected X (closest to 0.03)",
    )
    return pairs


def _write_csv(rows: list[SlotAppearance], path: Path) -> None:
    """Flat per-slot table (template_scores and overhang JSON-encoded)."""
    records = [row.model_dump(mode="json") for row in rows]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]), lineterminator="\n")
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    key: json.dumps(value) if isinstance(value, dict | list) else value
                    for key, value in record.items()
                }
            )


def run(
    baseline: Path, video_dir: Path, output: Path, config_path: Path
) -> dict[str, Any]:
    """Measure, render and persist the X appearance study."""
    config = load_config(config_path)
    templates = _load_masked_templates(config.vision.player_count.template_dir)
    rows, visuals = measure_all(baseline, video_dir, config, templates)
    output.mkdir(parents=True, exist_ok=True)
    pairs = render_sheets(rows, visuals, output)
    template_gallery(templates, output / "sheets" / "templates.png")
    controls = {
        f"{fn.sample_id}:{fn.slot}": (
            f"{tp.sample_id}:{tp.slot}" if (tp := matched_control(fn, rows)) else None
        )
        for fn in rows
        if fn.group == "fn"
    }
    payload = {
        "schema_version": 1,
        "development_only": True,
        "threshold": THRESHOLD,
        "frames_verified": all(row.frame_verified for row in rows),
        "counts": {
            "fn": sum(row.group == "fn" for row in rows),
            "tp": sum(row.group == "tp" for row in rows),
        },
        "matched_controls": controls,
        "pairs": [
            {k: pair[k] for k in ("sample_id", "fn_slot", "tp_slot", "same_side")}
            for pair in pairs
        ],
        "summary": summarize(rows),
        "rows": [row.model_dump(mode="json") for row in rows],
    }
    (output / "x_appearance.json").write_text(
        json.dumps(payload, indent=2) + "\n", "utf-8"
    )
    _write_csv(rows, output / "x_appearance.csv")
    return payload


def main() -> int:
    """Run the read-only X appearance study."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--video-dir", type=Path, default=Path.home() / "Movies")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=default_config_path())
    args = parser.parse_args()
    run(args.baseline, args.video_dir, args.output, args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
