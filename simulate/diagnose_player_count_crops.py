"""Crop diagnostic for player-count X templates (production match path).

Loads whatever image templates exist under ``calibration/templates/players``
the same way ``PlayerCountDetector`` does (``IMREAD_GRAYSCALE``, CLAHE on the
ROI only, ``TM_CCOEFF_NORMED``, shrink-to-fit only). Does not change production
code, ROIs, or thresholds.
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
    "2026-09-06 22-35-09-player_count/player_count_crop_diagnostic_real_templates"
)

FRAME_TIMES = [
    59.5,
    60.0,
    89.0,
    171.0,
    190.0,
    269.5,
    326.5,
]

# Visual labels from prior diagnostic inspection (re-verified after dump when needed).
# Used only for separation stats — scores are computed for every slot.
VISUAL_DEAD_X: dict[float, set[str]] = {
    59.5: {"ally-1", "ally-3", "ally-4"},  # refined after image inspect
    60.0: {"ally-1", "ally-3", "ally-4"},
    89.0: set(),  # filled after inspect if needed
    171.0: {"ally-1", "ally-2", "ally-3", "ally-4", "opponent-2"},
    190.0: set(),
    269.5: set(),
    326.5: set(),
}


def _load_slots() -> tuple[
    list[tuple[float, float, float, float]],
    list[tuple[float, float, float, float]],
]:
    raw = yaml.safe_load(CONFIG.read_text())
    pc = raw["vision"]["player_count"]
    ally = [tuple(map(float, box)) for box in pc["ally_slots"]]
    opp = [tuple(map(float, box)) for box in pc["opponent_slots"]]
    return ally, opp


def _discover_templates() -> list[tuple[str, np.ndarray, Path]]:
    """Same discovery rules as ``_load_flat_templates`` plus keep filenames."""
    out: list[tuple[str, np.ndarray, Path]] = []
    for path in sorted(TEMPLATES.glob("*")):
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            continue
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None or image.size == 0:
            continue
        out.append((path.stem, image, path))
    return out


def _crop(image: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
    h, w = image.shape[:2]
    x1, y1, x2, y2 = box
    left, top = int(x1 * w), int(y1 * h)
    right, bottom = int(x2 * w), int(y2 * h)
    return image[top:bottom, left:right].copy()


def _box_pixels(
    image: np.ndarray, box: tuple[float, float, float, float]
) -> tuple[int, int, int, int]:
    h, w = image.shape[:2]
    x1, y1, x2, y2 = box
    return int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h)


def _to_gray_clahe(roi: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    return clahe.apply(gray)


def _to_gray_raw(roi: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)


def _fit_template(image: np.ndarray, template: np.ndarray) -> np.ndarray | None:
    th, tw = template.shape[:2]
    ih, iw = image.shape[:2]
    if th < 4 or tw < 4 or ih < 4 or iw < 4:
        return None
    scale = min(1.0, ih / th, iw / tw)
    if scale < 1.0:
        new_w = max(4, int(tw * scale))
        new_h = max(4, int(th * scale))
        template = cv2.resize(template, (new_w, new_h), interpolation=cv2.INTER_AREA)
    if template.shape[0] > ih or template.shape[1] > iw:
        return None
    return template


def _score(
    gray_roi: np.ndarray, template: np.ndarray
) -> tuple[float, tuple[int, int] | None]:
    scaled = _fit_template(gray_roi, template)
    if scaled is None:
        return 0.0, None
    result = cv2.matchTemplate(gray_roi, scaled, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(result)
    return float(max_val), (int(scaled.shape[1]), int(scaled.shape[0]))


def _multi_scale_best(gray: np.ndarray, template: np.ndarray) -> tuple[float, float]:
    """Diagnostic-only scale sweep 0.3..1.0; production does not do this."""
    best = 0.0
    best_scale = 1.0
    for i in range(6, 21):
        scale = i / 20.0
        tw = max(4, int(template.shape[1] * scale))
        th = max(4, int(template.shape[0] * scale))
        if th > gray.shape[0] or tw > gray.shape[1]:
            continue
        scaled = cv2.resize(template, (tw, th), interpolation=cv2.INTER_AREA)
        score = float(cv2.matchTemplate(gray, scaled, cv2.TM_CCOEFF_NORMED).max())
        if score > best:
            best, best_scale = score, scale
    return best, best_scale


def _pad_to(img: np.ndarray, size: tuple[int, int], is_gray: bool = False) -> np.ndarray:
    th, tw = size
    canvas = (
        np.zeros((th, tw), dtype=np.uint8)
        if is_gray
        else np.zeros((th, tw, 3), dtype=np.uint8)
    )
    h, w = img.shape[:2]
    y0 = max(0, (th - h) // 2)
    x0 = max(0, (tw - w) // 2)
    canvas[y0 : y0 + h, x0 : x0 + w] = img
    if is_gray:
        return cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)
    return canvas


def _label_strip(width: int, text: str, height: int = 28) -> np.ndarray:
    strip = np.full((height, width, 3), 32, dtype=np.uint8)
    cv2.putText(
        strip,
        text[:48],
        (6, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (240, 240, 240),
        1,
        cv2.LINE_AA,
    )
    return strip


def _compare_row(
    crop_bgr: np.ndarray,
    templates: list[tuple[str, np.ndarray]],
    title: str,
) -> np.ndarray:
    """SOURCE | winning template | next template (raw sizes, padded)."""
    panels: list[np.ndarray] = [crop_bgr]
    labels = [title]
    for name, tmpl in templates[:2]:
        panels.append(cv2.cvtColor(tmpl, cv2.COLOR_GRAY2BGR))
        labels.append(f"{name} {tmpl.shape[1]}x{tmpl.shape[0]}")
    max_h = max(p.shape[0] for p in panels)
    max_w = max(p.shape[1] for p in panels)
    padded = [_pad_to(p, (max_h, max_w)) for p in panels]
    labeled = [
        np.vstack([_label_strip(p.shape[1], lab), p])
        for p, lab in zip(padded, labels, strict=True)
    ]
    gap = np.full((labeled[0].shape[0], 8, 3), 16, dtype=np.uint8)
    out = labeled[0]
    for panel in labeled[1:]:
        out = np.hstack([out, gap, panel])
    return out


def _draw_rois(
    frame: np.ndarray,
    ally: list[tuple[float, float, float, float]],
    opp: list[tuple[float, float, float, float]],
) -> np.ndarray:
    out = frame.copy()
    for i, box in enumerate(ally, start=1):
        x1, y1, x2, y2 = _box_pixels(out, box)
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(
            out,
            f"A{i}",
            (x1, max(y1 - 4, 12)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )
    for i, box in enumerate(opp, start=1):
        x1, y1, x2, y2 = _box_pixels(out, box)
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 165, 255), 2)
        cv2.putText(
            out,
            f"O{i}",
            (x1, max(y1 - 4, 12)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 165, 255),
            1,
            cv2.LINE_AA,
        )
    return out


def _seek_frame(cap: cv2.VideoCapture, t: float) -> np.ndarray:
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
    ok, frame = cap.read()
    if not ok or frame is None:
        raise RuntimeError(f"Failed to read frame at t={t}")
    return frame


def main() -> None:
    templates = _discover_templates()
    if not templates:
        raise RuntimeError(f"No templates found under {TEMPLATES}")

    print("Loaded templates (production path: IMREAD_GRAYSCALE, no CLAHE on template):")
    for name, img, path in templates:
        print(f"  {path.name}: {img.shape[1]}x{img.shape[0]} mean={img.mean():.1f}")
    print(f"count={len(templates)}")
    assert not any(name.startswith("x-mark") for name, _, _ in templates)

    ally_slots, opp_slots = _load_slots()
    slot_specs = [(f"ally-{i}", box) for i, box in enumerate(ally_slots, start=1)]
    slot_specs += [(f"opponent-{i}", box) for i, box in enumerate(opp_slots, start=1)]

    OUT.mkdir(parents=True, exist_ok=True)
    templates_out = OUT / "templates"
    templates_out.mkdir(exist_ok=True)
    for name, img, path in templates:
        cv2.imwrite(str(templates_out / f"{name}_as_loaded_gray.png"), img)
        (templates_out / path.name).write_bytes(path.read_bytes())

    load_report = OUT / "template_load_report.txt"
    load_report.write_text(
        "\n".join(
            [
                "Production loading rules mirrored:",
                "- Discover *.png/*.jpg/*.jpeg under template_dir",
                "- cv2.IMREAD_GRAYSCALE (no alpha path; templates are opaque BGR)",
                "- CLAHE applied only to ROI crop at match time, NOT to templates",
                "- Matcher: TM_CCOEFF_NORMED; _fit_template shrink-to-fit only",
                f"- match_threshold unchanged (config): 0.70",
                "",
                f"templates_loaded={len(templates)}",
                *[
                    f"{path.name}\t{img.shape[1]}x{img.shape[0]}\tmean={img.mean():.1f}"
                    for _, img, path in templates
                ],
                "",
                "old x-mark-* templates present: False",
            ]
        )
        + "\n"
    )

    cap = cv2.VideoCapture(str(VIDEO))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open {VIDEO}")

    score_rows: list[dict[str, object]] = []
    best_overall: tuple[float, float, str, str] | None = None

    for t in FRAME_TIMES:
        frame = _seek_frame(cap, t)
        death_dir = OUT / f"death_{t:.1f}"
        death_dir.mkdir(exist_ok=True)
        cv2.imwrite(
            str(death_dir / "frame_with_rois.png"),
            _draw_rois(frame, ally_slots, opp_slots),
        )

        frame_best = 0.0
        frame_best_slot = ""
        frame_best_tmpl = ""
        crops: dict[str, np.ndarray] = {}
        slot_best: dict[str, tuple[float, str, float, float]] = {}

        for name, box in slot_specs:
            crop = _crop(frame, box)
            crops[name] = crop
            cv2.imwrite(str(death_dir / f"{name}.png"), crop)
            ch, cw = crop.shape[:2]
            gray_clahe = _to_gray_clahe(crop)
            gray_raw = _to_gray_raw(crop)

            best_c = 0.0
            best_r = 0.0
            best_tmpl = ""
            best_ms = 0.0
            for tmpl_name, tmpl, _path in templates:
                s_clahe, fitted = _score(gray_clahe, tmpl)
                s_raw, _ = _score(gray_raw, tmpl)
                ms, ms_scale = _multi_scale_best(gray_clahe, tmpl)
                score_rows.append(
                    {
                        "frame_time": t,
                        "slot": name,
                        "template": tmpl_name,
                        "score_clahe": round(s_clahe, 4),
                        "score_raw_gray": round(s_raw, 4),
                        "multiscale_best_clahe": round(ms, 4),
                        "multiscale_best_scale": ms_scale,
                        "crop_w": cw,
                        "crop_h": ch,
                        "template_w": tmpl.shape[1],
                        "template_h": tmpl.shape[0],
                        "fitted_w": fitted[0] if fitted else "",
                        "fitted_h": fitted[1] if fitted else "",
                        "shrink_applied": bool(
                            fitted
                            and (
                                fitted[0] < tmpl.shape[1]
                                or fitted[1] < tmpl.shape[0]
                            )
                        ),
                    }
                )
                if s_clahe >= best_c:
                    best_c, best_tmpl = s_clahe, tmpl_name
                best_r = max(best_r, s_raw)
                best_ms = max(best_ms, ms)

            slot_best[name] = (best_c, best_tmpl, best_r, best_ms)
            if best_c > frame_best:
                frame_best = best_c
                frame_best_slot = name
                frame_best_tmpl = best_tmpl

        # Rank templates by score on best slot for comparison image.
        ranked = sorted(
            (
                (
                    float(
                        next(
                            r["score_clahe"]
                            for r in score_rows
                            if r["frame_time"] == t
                            and r["slot"] == frame_best_slot
                            and r["template"] == n
                        )
                    ),
                    n,
                    img,
                )
                for n, img, _ in templates
            ),
            reverse=True,
        )
        cmp = _compare_row(
            crops[frame_best_slot],
            [(n, img) for _, n, img in ranked[:2]],
            f"SOURCE {frame_best_slot} {crops[frame_best_slot].shape[1]}x"
            f"{crops[frame_best_slot].shape[0]} best={frame_best:.3f}",
        )
        cv2.imwrite(str(death_dir / "compare_best_slot_raw.png"), cmp)

        # Clear-X comparisons for known t=171 problem slots.
        if abs(t - 171.0) < 1e-6:
            for slot in ("ally-1", "ally-3", "ally-4", "opponent-2"):
                sc, tn, raw, ms = slot_best[slot]
                tmpl_img = next(img for n, img, _ in templates if n == tn)
                # second-best
                others = sorted(
                    (
                        float(
                            next(
                                r["score_clahe"]
                                for r in score_rows
                                if r["frame_time"] == t
                                and r["slot"] == slot
                                and r["template"] == n
                            )
                        ),
                        n,
                        img,
                    )
                    for n, img, _ in templates
                )
                others.sort(reverse=True)
                row = _compare_row(
                    crops[slot],
                    [(others[0][1], others[0][2]), (others[1][1], others[1][2])],
                    f"SOURCE {slot} clahe={sc:.3f} raw={raw:.3f} ms={ms:.3f}",
                )
                cv2.imwrite(str(death_dir / f"compare_{slot}.png"), row)

        # All-slots strip
        panels = []
        for name, _ in slot_specs:
            crop = crops[name]
            sc, tn, _, _ = slot_best[name]
            panel = _pad_to(crop, (140, 160))
            panel = np.vstack(
                [_label_strip(panel.shape[1], f"{name} {sc:.2f}"), panel]
            )
            panels.append(panel)
        row1 = np.hstack(panels[:4])
        row2 = np.hstack(panels[4:])
        w = max(row1.shape[1], row2.shape[1])

        def _pad_w(img: np.ndarray, width: int) -> np.ndarray:
            if img.shape[1] >= width:
                return img
            pad = np.zeros((img.shape[0], width - img.shape[1], 3), dtype=np.uint8)
            return np.hstack([img, pad])

        cv2.imwrite(
            str(death_dir / "all_slots.png"),
            np.vstack([_pad_w(row1, w), _pad_w(row2, w)]),
        )

        if best_overall is None or frame_best > best_overall[0]:
            best_overall = (frame_best, t, frame_best_slot, frame_best_tmpl)

    cap.release()

    fieldnames = [
        "frame_time",
        "slot",
        "template",
        "score_clahe",
        "score_raw_gray",
        "multiscale_best_clahe",
        "multiscale_best_scale",
        "crop_w",
        "crop_h",
        "template_w",
        "template_h",
        "fitted_w",
        "fitted_h",
        "shrink_applied",
    ]
    with (OUT / "scores.tsv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(score_rows)

    # Per slot best across all templates
    with (OUT / "scores_summary.tsv").open("w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(
            [
                "frame_time",
                "slot",
                "best_template",
                "best_clahe",
                "best_raw",
                "best_multiscale_clahe",
                "crop_wxh",
            ]
        )
        by_key: dict[tuple[float, str], list[dict]] = {}
        for r in score_rows:
            by_key.setdefault(
                (float(r["frame_time"]), str(r["slot"])), []  # type: ignore[arg-type]
            ).append(r)
        for (ft, slot), rows in sorted(by_key.items()):
            best = max(rows, key=lambda r: float(r["score_clahe"]))  # type: ignore[arg-type]
            best_raw = max(float(r["score_raw_gray"]) for r in rows)  # type: ignore[arg-type]
            best_ms = max(float(r["multiscale_best_clahe"]) for r in rows)  # type: ignore[arg-type]
            w.writerow(
                [
                    ft,
                    slot,
                    best["template"],
                    best["score_clahe"],
                    round(best_raw, 4),
                    round(best_ms, 4),
                    f"{best['crop_w']}x{best['crop_h']}",
                ]
            )

    assert best_overall is not None
    print(f"Wrote {OUT}")
    print(f"Strongest: score={best_overall[0]:.4f} t={best_overall[1]} "
          f"slot={best_overall[2]} tmpl={best_overall[3]}")


if __name__ == "__main__":
    main()
