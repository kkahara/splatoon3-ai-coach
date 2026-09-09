"""Masked X-only matching experiment (diagnostic only; no production changes).

Derives X-structure masks from real player-x templates and scores slots with
``TM_SQDIFF_NORMED`` + mask. Writes a new diagnostic directory; does not
overwrite the prior real-template CCOEFF results.
"""

from __future__ import annotations

import csv
from pathlib import Path

import cv2
import numpy as np
import yaml

VIDEO = Path("/Users/kenjikahara/Movies/2026-09-06 22-35-09.mov")
CONFIG = Path("/Users/kenjikahara/splatoon3-ai-coach/configs/default.yaml")
TEMPLATES = Path(
    "/Users/kenjikahara/splatoon3-ai-coach/calibration/templates/players"
)
OUT = Path(
    "/Users/kenjikahara/splatoon3-ai-coach/analysis/"
    "2026-09-06 22-35-09-player_count/"
    "player_count_crop_diagnostic_masked_sqdiff"
)

FRAME_TIMES = [59.5, 60.0, 89.0, 171.0, 190.0, 269.5, 326.5]

# Visual ground truth from prior crop inspection (refined for this experiment).
# framing: clear | shifted | bleed | none
LABELS: dict[float, dict[str, tuple[str, str]]] = {
    # slot -> (visual: X|no-X|uncertain, framing: clear|shifted|bleed|none)
    59.5: {
        "ally-1": ("X", "clear"),
        "ally-2": ("no-X", "none"),
        "ally-3": ("X", "shifted"),
        "ally-4": ("no-X", "none"),
        "opponent-1": ("X", "shifted"),
        "opponent-2": ("no-X", "none"),
        "opponent-3": ("no-X", "none"),
        "opponent-4": ("no-X", "none"),
    },
    60.0: {
        "ally-1": ("X", "clear"),
        "ally-2": ("no-X", "none"),
        "ally-3": ("X", "shifted"),
        "ally-4": ("no-X", "none"),
        "opponent-1": ("X", "shifted"),
        "opponent-2": ("no-X", "none"),
        "opponent-3": ("no-X", "none"),
        "opponent-4": ("no-X", "none"),
    },
    89.0: {
        "ally-1": ("X", "clear"),
        "ally-2": ("no-X", "bleed"),
        "ally-3": ("no-X", "bleed"),
        "ally-4": ("no-X", "none"),
        "opponent-1": ("X", "clear"),
        "opponent-2": ("X", "clear"),
        "opponent-3": ("no-X", "bleed"),
        "opponent-4": ("no-X", "none"),
    },
    171.0: {
        "ally-1": ("X", "clear"),
        "ally-2": ("no-X", "bleed"),
        "ally-3": ("X", "shifted"),
        "ally-4": ("X", "clear"),
        "opponent-1": ("no-X", "none"),
        "opponent-2": ("X", "clear"),
        "opponent-3": ("no-X", "none"),
        "opponent-4": ("no-X", "none"),
    },
    190.0: {
        "ally-1": ("X", "clear"),
        "ally-2": ("X", "bleed"),  # two Xs / wide ROI
        "ally-3": ("X", "shifted"),
        "ally-4": ("no-X", "none"),
        "opponent-1": ("no-X", "none"),
        "opponent-2": ("no-X", "none"),
        "opponent-3": ("no-X", "none"),
        "opponent-4": ("no-X", "none"),
    },
    269.5: {
        "ally-1": ("X", "clear"),
        "ally-2": ("X", "clear"),
        "ally-3": ("no-X", "none"),
        "ally-4": ("no-X", "none"),
        "opponent-1": ("X", "clear"),
        "opponent-2": ("no-X", "none"),
        "opponent-3": ("no-X", "none"),
        "opponent-4": ("no-X", "none"),
    },
    326.5: {
        "ally-1": ("X", "clear"),
        "ally-2": ("no-X", "none"),
        "ally-3": ("X", "shifted"),
        "ally-4": ("X", "clear"),
        "opponent-1": ("X", "clear"),
        "opponent-2": ("uncertain", "none"),
        "opponent-3": ("no-X", "none"),
        "opponent-4": ("no-X", "none"),
    },
}

# Mid-gray band characteristic of the translucent X (not white weapon, not black bg).
_X_GRAY_LO = 70
_X_GRAY_HI = 140
_DIAG_THICKNESS = 0.11  # fraction of width/height for diagonal arm half-width


def _load_slots() -> tuple[
    list[tuple[float, float, float, float]],
    list[tuple[float, float, float, float]],
]:
    raw = yaml.safe_load(CONFIG.read_text())
    pc = raw["vision"]["player_count"]
    ally = [tuple(map(float, box)) for box in pc["ally_slots"]]
    opp = [tuple(map(float, box)) for box in pc["opponent_slots"]]
    return ally, opp


def _crop(image: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
    h, w = image.shape[:2]
    x1, y1, x2, y2 = box
    left, top = int(x1 * w), int(y1 * h)
    right, bottom = int(x2 * w), int(y2 * h)
    return image[top:bottom, left:right].copy()


def _discover_templates() -> list[tuple[str, np.ndarray, Path]]:
    out: list[tuple[str, np.ndarray, Path]] = []
    for path in sorted(TEMPLATES.glob("player-x-*")):
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            continue
        gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if gray is None or gray.size == 0:
            continue
        out.append((path.stem, gray, path))
    return out


def _diagonal_band(h: int, w: int, thickness: float = _DIAG_THICKNESS) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    dx = (xx - cx) / max(w, 1)
    dy = (yy - cy) / max(h, 1)
    return (np.abs(dx - dy) < thickness) | (np.abs(dx + dy) < thickness)


def derive_x_mask(gray: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
    """Derive an X-only mask from a real template.

    Method (documented, diagnostic):
    1. Geometric prior: pixels on the two diagonal arms of an X.
    2. Intensity gate: mid-gray ``[_X_GRAY_LO, _X_GRAY_HI]`` so bright white
       weapon cores and near-black background drop out.
    3. Morphological open/close to remove speckles and fill small gaps.
    4. Keep the largest connected component if multiple survive.

    Returns uint8 mask {0,255} and coverage stats.
    """
    h, w = gray.shape
    diag = _diagonal_band(h, w)
    mid = (gray >= _X_GRAY_LO) & (gray <= _X_GRAY_HI)
    raw = (diag & mid).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    cleaned = cv2.morphologyEx(raw, cv2.MORPH_OPEN, kernel, iterations=1)
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, kernel, iterations=1)

    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(cleaned)
    if n_labels <= 1:
        mask = cleaned
    else:
        # skip background label 0
        areas = stats[1:, cv2.CC_STAT_AREA]
        keep = 1 + int(np.argmax(areas))
        mask = np.where(labels == keep, 255, 0).astype(np.uint8)

    stats_out = {
        "diag_frac": float(diag.mean()),
        "mid_frac": float(mid.mean()),
        "raw_mask_frac": float((raw > 0).mean()),
        "final_mask_frac": float((mask > 0).mean()),
        "masked_mean_gray": float(gray[mask > 0].mean()) if np.any(mask) else 0.0,
        "unmasked_mean_gray": float(gray[mask == 0].mean()) if np.any(mask == 0) else 0.0,
    }
    return mask, stats_out


def _fit_pair(
    image: np.ndarray, template: np.ndarray, mask: np.ndarray
) -> tuple[np.ndarray, np.ndarray] | None:
    """Shrink template+mask together so they fit inside ``image``."""
    th, tw = template.shape[:2]
    ih, iw = image.shape[:2]
    if th < 4 or tw < 4 or ih < 4 or iw < 4:
        return None
    scale = min(1.0, ih / th, iw / tw)
    if scale < 1.0:
        new_w = max(4, int(tw * scale))
        new_h = max(4, int(th * scale))
        template = cv2.resize(template, (new_w, new_h), interpolation=cv2.INTER_AREA)
        mask = cv2.resize(mask, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
    if template.shape[0] > ih or template.shape[1] > iw:
        return None
    if not np.any(mask):
        return None
    return template, mask


def score_masked_sqdiff(
    gray_roi: np.ndarray, template: np.ndarray, mask: np.ndarray
) -> tuple[float, tuple[int, int] | None, str]:
    """Return (best_score, loc, note). Lower is better for SQDIFF_NORMED."""
    fitted = _fit_pair(gray_roi, template, mask)
    if fitted is None:
        return 1.0, None, "fit_failed"
    tmpl, msk = fitted
    try:
        result = cv2.matchTemplate(
            gray_roi, tmpl, cv2.TM_SQDIFF_NORMED, mask=msk
        )
    except cv2.error as exc:
        return 1.0, None, f"opencv_error:{exc}"
    min_val, _max_val, min_loc, _max_loc = cv2.minMaxLoc(result)
    return float(min_val), (int(min_loc[0]), int(min_loc[1])), "ok"


def score_unmasked_ccoeff(gray_roi: np.ndarray, template: np.ndarray) -> float:
    """Production-style unmasked CCOEFF for before/after (higher better)."""
    th, tw = template.shape[:2]
    ih, iw = gray_roi.shape[:2]
    scale = min(1.0, ih / th, iw / tw)
    tmpl = template
    if scale < 1.0:
        tmpl = cv2.resize(
            template,
            (max(4, int(tw * scale)), max(4, int(th * scale))),
            interpolation=cv2.INTER_AREA,
        )
    if tmpl.shape[0] > ih or tmpl.shape[1] > iw:
        return 0.0
    return float(cv2.matchTemplate(gray_roi, tmpl, cv2.TM_CCOEFF_NORMED).max())


def _to_gray_clahe(roi: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4)).apply(gray)


def _to_gray_raw(roi: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)


def _pad(img: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    th, tw = size
    if img.ndim == 2:
        canvas = np.zeros((th, tw), dtype=np.uint8)
    else:
        canvas = np.zeros((th, tw, 3), dtype=np.uint8)
    h, w = img.shape[:2]
    y0, x0 = max(0, (th - h) // 2), max(0, (tw - w) // 2)
    canvas[y0 : y0 + h, x0 : x0 + w] = img
    return canvas


def _label(width: int, text: str, height: int = 28) -> np.ndarray:
    strip = np.full((height, width, 3), 32, dtype=np.uint8)
    cv2.putText(
        strip,
        text[:52],
        (4, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (240, 240, 240),
        1,
        cv2.LINE_AA,
    )
    return strip


def _panel(img: np.ndarray, text: str, size: tuple[int, int]) -> np.ndarray:
    if img.ndim == 2:
        bgr = cv2.cvtColor(_pad(img, size), cv2.COLOR_GRAY2BGR)
    else:
        bgr = _pad(img, size)
    return np.vstack([_label(bgr.shape[1], text), bgr])


def make_compare(
    crop_bgr: np.ndarray,
    template: np.ndarray,
    mask: np.ndarray,
    score: float,
    loc: tuple[int, int] | None,
    title: str,
) -> np.ndarray:
    """ROI | template | mask | overlay of match location."""
    h = max(crop_bgr.shape[0], template.shape[0], 120)
    w = max(crop_bgr.shape[1], template.shape[1], 120)
    size = (h, w)

    overlay = crop_bgr.copy()
    if loc is not None:
        th, tw = template.shape[:2]
        # account for possible shrink
        scale = min(1.0, crop_bgr.shape[0] / template.shape[0], crop_bgr.shape[1] / template.shape[1])
        tw2 = max(4, int(template.shape[1] * min(scale, 1.0)))
        th2 = max(4, int(template.shape[0] * min(scale, 1.0)))
        x, y = loc
        cv2.rectangle(overlay, (x, y), (x + tw2, y + th2), (0, 255, 255), 1)
        # draw masked template outline at location
        msk = cv2.resize(mask, (tw2, th2), interpolation=cv2.INTER_NEAREST)
        ys, xs = np.where(msk > 0)
        if len(xs):
            for yy, xx in zip(ys[:: max(1, len(ys)//80)], xs[:: max(1, len(xs)//80)], strict=False):
                py, px = y + int(yy), x + int(xx)
                if 0 <= py < overlay.shape[0] and 0 <= px < overlay.shape[1]:
                    overlay[py, px] = (0, 0, 255)

    # masked template preview: template where mask, else dark
    masked_view = np.where(mask > 0, template, 0).astype(np.uint8)

    panels = [
        _panel(crop_bgr, f"ROI {title}", size),
        _panel(template, f"TMPL {template.shape[1]}x{template.shape[0]}", size),
        _panel(mask, f"MASK cov={(mask>0).mean():.2f}", size),
        _panel(masked_view, "TMPL*MASK", size),
        _panel(overlay, f"MATCH sqdiff={score:.3f}", size),
    ]
    gap = np.full((panels[0].shape[0], 6, 3), 16, dtype=np.uint8)
    out = panels[0]
    for p in panels[1:]:
        out = np.hstack([out, gap, p])
    return out


def _seek(cap: cv2.VideoCapture, t: float) -> np.ndarray:
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
    ok, frame = cap.read()
    if not ok or frame is None:
        raise RuntimeError(f"Failed to read t={t}")
    return frame


def main() -> None:
    # Sanity: confirm OpenCV masked SQDIFF works
    probe = np.zeros((100, 100), np.uint8)
    tmpl = np.full((40, 40), 105, np.uint8)
    mask = np.zeros((40, 40), np.uint8)
    cv2.line(mask, (0, 0), (39, 39), 255, 5)
    cv2.line(mask, (39, 0), (0, 39), 255, 5)
    probe[30:70, 30:70] = tmpl
    try:
        res = cv2.matchTemplate(probe, tmpl, cv2.TM_SQDIFF_NORMED, mask=mask)
        probe_ok = True
        probe_score = float(res.min())
    except cv2.error as exc:
        probe_ok = False
        probe_score = -1.0
        probe_err = str(exc)

    templates = _discover_templates()
    if not templates:
        raise RuntimeError("No player-x templates found")

    OUT.mkdir(parents=True, exist_ok=True)
    mask_dir = OUT / "masks"
    mask_dir.mkdir(exist_ok=True)
    tmpl_dir = OUT / "templates"
    tmpl_dir.mkdir(exist_ok=True)

    prepared: list[tuple[str, np.ndarray, np.ndarray, dict]] = []
    mask_report_lines = [
        "Mask construction method:",
        "  1) Geometric X diagonal-band prior (thickness=0.11 of frame).",
        f"  2) Mid-gray intensity gate [{_X_GRAY_LO},{_X_GRAY_HI}] to drop white weapon / black bg.",
        "  3) Morphological open+close (3x3 ellipse).",
        "  4) Keep largest connected component.",
        "Template pixels outside the mask are ignored by OpenCV matchTemplate(mask=...).",
        f"OpenCV masked TM_SQDIFF_NORMED probe: ok={probe_ok} self_score={probe_score:.4f}",
        "",
    ]
    if not probe_ok:
        mask_report_lines.append(f"PROBE ERROR: {probe_err}")  # noqa: F821

    for name, gray, path in templates:
        mask, stats = derive_x_mask(gray)
        prepared.append((name, gray, mask, stats))
        cv2.imwrite(str(mask_dir / f"{name}_mask.png"), mask)
        cv2.imwrite(str(tmpl_dir / f"{name}_gray.png"), gray)
        (tmpl_dir / path.name).write_bytes(path.read_bytes())
        # side-by-side mask diagnostic
        view = np.hstack(
            [
                cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR),
                cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR),
                cv2.cvtColor(np.where(mask > 0, gray, 0).astype(np.uint8), cv2.COLOR_GRAY2BGR),
            ]
        )
        cv2.imwrite(str(mask_dir / f"{name}_gray_mask_masked.png"), view)
        mask_report_lines.append(
            f"{name}\t{gray.shape[1]}x{gray.shape[0]}\t"
            f"mask_frac={stats['final_mask_frac']:.3f}\t"
            f"masked_mean={stats['masked_mean_gray']:.1f}\t"
            f"unmasked_mean={stats['unmasked_mean_gray']:.1f}"
        )

    (OUT / "mask_construction.txt").write_text("\n".join(mask_report_lines) + "\n")

    ally_slots, opp_slots = _load_slots()
    slot_specs = [(f"ally-{i}", box) for i, box in enumerate(ally_slots, start=1)]
    slot_specs += [(f"opponent-{i}", box) for i, box in enumerate(opp_slots, start=1)]

    cap = cv2.VideoCapture(str(VIDEO))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open {VIDEO}")

    rows: list[dict[str, object]] = []
    # representative dumps
    specials = {
        (171.0, "ally-1"): "matching_weapon_X",
        (171.0, "ally-4"): "different_weapon_X",
        (171.0, "opponent-2"): "cross_weapon_opp_X",
        (171.0, "opponent-1"): "alive_clear",
        (171.0, "ally-3"): "shifted_X",
        (269.5, "opponent-1"): "opp_dualie_X",
        (59.5, "ally-1"): "easy_centered_X",
    }

    for t in FRAME_TIMES:
        frame = _seek(cap, t)
        death_dir = OUT / f"death_{t:.1f}"
        death_dir.mkdir(exist_ok=True)
        crops: dict[str, np.ndarray] = {}

        for name, box in slot_specs:
            crop = _crop(frame, box)
            crops[name] = crop
            cv2.imwrite(str(death_dir / f"{name}.png"), crop)
            gray_raw = _to_gray_raw(crop)
            gray_clahe = _to_gray_clahe(crop)
            visual, framing = LABELS[t][name]

            best_sq = 1.0
            best_tmpl = ""
            best_loc: tuple[int, int] | None = None
            best_note = ""
            best_mask: np.ndarray | None = None
            best_tmpl_img: np.ndarray | None = None
            best_ccoeff = 0.0

            for tmpl_name, tmpl, mask, _stats in prepared:
                # Primary experiment: raw gray + masked SQDIFF (no CLAHE on ROI
                # for the distance metric; also score CLAHE separately).
                sq_raw, loc, note = score_masked_sqdiff(gray_raw, tmpl, mask)
                sq_clahe, _, _ = score_masked_sqdiff(gray_clahe, tmpl, mask)
                cc = score_unmasked_ccoeff(gray_clahe, tmpl)
                rows.append(
                    {
                        "frame_time": t,
                        "slot": name,
                        "visual": visual,
                        "framing": framing,
                        "template": tmpl_name,
                        "masked_sqdiff_raw": round(sq_raw, 4),
                        "masked_sqdiff_clahe": round(sq_clahe, 4),
                        "unmasked_ccoeff_clahe": round(cc, 4),
                        "note": note,
                        "crop_w": crop.shape[1],
                        "crop_h": crop.shape[0],
                        "template_w": tmpl.shape[1],
                        "template_h": tmpl.shape[0],
                        "mask_frac": round(float((mask > 0).mean()), 4),
                    }
                )
                if note == "ok" and sq_raw < best_sq:
                    best_sq = sq_raw
                    best_tmpl = tmpl_name
                    best_loc = loc
                    best_note = note
                    best_mask = mask
                    best_tmpl_img = tmpl
                best_ccoeff = max(best_ccoeff, cc)

            # per-slot summary row already in scores; also write compare for specials
            key = (t, name)
            if key in specials and best_tmpl_img is not None and best_mask is not None:
                cmp = make_compare(
                    crop,
                    best_tmpl_img,
                    best_mask,
                    best_sq,
                    best_loc,
                    f"{specials[key]} {name}@{t}",
                )
                cv2.imwrite(str(OUT / f"compare_{specials[key]}_{name}_t{t}.png"), cmp)

        # all-slots strip with best sqdiff
        panels = []
        for name, _ in slot_specs:
            crop = crops[name]
            # find best raw sqdiff for slot from rows just appended
            slot_rows = [
                r
                for r in rows
                if float(r["frame_time"]) == t and r["slot"] == name  # type: ignore[arg-type]
            ]
            best = min(slot_rows, key=lambda r: float(r["masked_sqdiff_raw"]))  # type: ignore[arg-type]
            visual = best["visual"]
            panel = _pad(crop, (130, 150))
            if panel.ndim == 2:
                panel = cv2.cvtColor(panel, cv2.COLOR_GRAY2BGR)
            label = f"{name} {visual} {float(best['masked_sqdiff_raw']):.2f}"  # type: ignore[arg-type]
            panels.append(np.vstack([_label(panel.shape[1], label), panel]))
        row1, row2 = np.hstack(panels[:4]), np.hstack(panels[4:])
        w = max(row1.shape[1], row2.shape[1])

        def _pw(img: np.ndarray, width: int) -> np.ndarray:
            if img.shape[1] >= width:
                return img
            return np.hstack(
                [img, np.zeros((img.shape[0], width - img.shape[1], 3), np.uint8)]
            )

        cv2.imwrite(
            str(death_dir / "all_slots.png"),
            np.vstack([_pw(row1, w), _pw(row2, w)]),
        )

    cap.release()

    with (OUT / "scores_all_templates.tsv").open("w", newline="") as f:
        fields = list(rows[0].keys())
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        w.writeheader()
        w.writerows(rows)

    # Best-per-slot summary
    by_slot: dict[tuple[float, str], list[dict]] = {}
    for r in rows:
        by_slot.setdefault((float(r["frame_time"]), str(r["slot"])), []).append(r)

    summary_rows = []
    with (OUT / "scores_summary.tsv").open("w", newline="") as f:
        fields = [
            "frame_time",
            "slot",
            "visual",
            "framing",
            "best_template",
            "masked_sqdiff_raw",
            "masked_sqdiff_clahe",
            "unmasked_ccoeff_clahe",
        ]
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        w.writeheader()
        for (ft, slot), rs in sorted(by_slot.items()):
            best = min(rs, key=lambda r: float(r["masked_sqdiff_raw"]))  # type: ignore[arg-type]
            best_cc = max(float(r["unmasked_ccoeff_clahe"]) for r in rs)  # type: ignore[arg-type]
            out_row = {
                "frame_time": ft,
                "slot": slot,
                "visual": best["visual"],
                "framing": best["framing"],
                "best_template": best["template"],
                "masked_sqdiff_raw": best["masked_sqdiff_raw"],
                "masked_sqdiff_clahe": best["masked_sqdiff_clahe"],
                "unmasked_ccoeff_clahe": round(best_cc, 4),
            }
            w.writerow(out_row)
            summary_rows.append(out_row)

    # Separation stats
    def collect(visuals: set[str], framing: set[str] | None = None) -> list[float]:
        vals = []
        for r in summary_rows:
            if r["visual"] not in visuals:
                continue
            if framing is not None and r["framing"] not in framing:
                continue
            vals.append(float(r["masked_sqdiff_raw"]))
        return vals

    clear_x = collect({"X"}, {"clear"})
    all_x = collect({"X"})
    alive = collect({"no-X"})
    # also unmasked ccoeff for same sets
    def collect_cc(visuals: set[str], framing: set[str] | None = None) -> list[float]:
        vals = []
        for r in summary_rows:
            if r["visual"] not in visuals:
                continue
            if framing is not None and r["framing"] not in framing:
                continue
            vals.append(float(r["unmasked_ccoeff_clahe"]))
        return vals

    from statistics import median

    def fmt(vals: list[float], lower_better: bool) -> str:
        if not vals:
            return "n/a"
        return (
            f"n={len(vals)} best={min(vals):.3f} median={median(vals):.3f} "
            f"worst={max(vals):.3f}"
            if lower_better
            else f"n={len(vals)} best={max(vals):.3f} median={median(vals):.3f} "
            f"worst={min(vals):.3f}"
        )

    report = []
    report.append("# Masked SQDIFF experiment")
    report.append("")
    report.append(f"OpenCV masked SQDIFF probe ok={probe_ok} self={probe_score:.4f}")
    report.append(f"Templates: {len(prepared)}")
    for name, gray, mask, st in prepared:
        report.append(
            f"  {name}: {gray.shape[1]}x{gray.shape[0]} mask_frac={st['final_mask_frac']:.3f}"
        )
    report.append("")
    report.append("## Masked SQDIFF (raw gray ROI; lower=better)")
    report.append(f"Clear X: {fmt(clear_x, True)}")
    report.append(f"All X:   {fmt(all_x, True)}")
    report.append(f"Alive:   {fmt(alive, True)}")
    if clear_x and alive:
        report.append(
            f"Separation dead_max - alive_min = {max(clear_x) - min(alive):+.3f} "
            f"(want negative; clear worst below alive best)"
        )
        report.append(
            f"Clear X worst={max(clear_x):.3f} vs Alive best(lowest)={min(alive):.3f}"
        )
        report.append(
            f"Overlap: clear_X scores >= alive_min : "
            f"{sum(1 for v in clear_x if v >= min(alive))}/{len(clear_x)}"
        )
    report.append("")
    report.append("## Unmasked CCOEFF same slots (higher=better; for comparison)")
    cc_clear = collect_cc({"X"}, {"clear"})
    cc_alive = collect_cc({"no-X"})
    report.append(f"Clear X: {fmt(cc_clear, False)}")
    report.append(f"Alive:   {fmt(cc_alive, False)}")
    (OUT / "separation_report.txt").write_text("\n".join(report) + "\n")
    print("\n".join(report))
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
