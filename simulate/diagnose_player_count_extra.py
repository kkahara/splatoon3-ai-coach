"""Extra measurements for player-count crop diagnostic (not production)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

BASE = Path(
    "analysis/2026-09-06 22-35-09-player_count/player_count_crop_diagnostic"
)
T1 = cv2.imread("calibration/templates/players/x-mark-1.png", cv2.IMREAD_UNCHANGED)
T2 = cv2.imread("calibration/templates/players/x-mark-2.png", cv2.IMREAD_UNCHANGED)
T1G = cv2.imread("calibration/templates/players/x-mark-1.png", cv2.IMREAD_GRAYSCALE)
T2G = cv2.imread("calibration/templates/players/x-mark-2.png", cv2.IMREAD_GRAYSCALE)


def x_stats(gray: np.ndarray, name: str) -> None:
    mask = gray > 40
    ys, xs = np.where(mask)
    x1, x2, y1, y2 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    print(
        f"{name}: canvas={gray.shape[1]}x{gray.shape[0]} "
        f"X_bbox={x2 - x1 + 1}x{y2 - y1 + 1} origin=({x1},{y1}) "
        f"mean_on={gray[mask].mean():.1f} mean_off={gray[~mask].mean():.1f} "
        f"coverage={mask.mean():.3f}"
    )


def multi_scale(gray: np.ndarray, tmpl: np.ndarray) -> tuple[float, float, int, int]:
    best = (-1.0, 1.0, 0, 0)
    for scale_i in range(6, 21):
        scale = scale_i / 20.0
        tw = max(4, int(tmpl.shape[1] * scale))
        th = max(4, int(tmpl.shape[0] * scale))
        if th > gray.shape[0] or tw > gray.shape[1]:
            continue
        scaled = cv2.resize(tmpl, (tw, th), interpolation=cv2.INTER_AREA)
        score = float(cv2.matchTemplate(gray, scaled, cv2.TM_CCOEFF_NORMED).max())
        if score > best[0]:
            best = (score, scale, tw, th)
    return best  # type: ignore[return-value]


def main() -> None:
    print("=== Templates (IMREAD_GRAYSCALE) ===")
    x_stats(T1G, "x-mark-1")
    x_stats(T2G, "x-mark-2")
    print(
        "RGBA alpha coverage:",
        float((T1[:, :, 3] > 0).mean()),
        float((T2[:, :, 3] > 0).mean()),
    )

    print("\n=== Multi-scale CCOEFF (CLAHE) @ death_171.0 ===")
    for slot in ("ally-1", "ally-4", "ally-3", "opponent-2"):
        crop = cv2.imread(str(BASE / "death_171.0" / f"{slot}.png"))
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4)).apply(gray)
        print(f"  {slot} crop={crop.shape[1]}x{crop.shape[0]}")
        for name, tmpl in (("t1", T1G), ("t2", T2G)):
            score, scale, tw, th = multi_scale(clahe, tmpl)
            print(f"    {name}: best={score:.4f} scale={scale} fitted={tw}x{th}")

    print("\n=== Masked match @ death_171.0 ===")
    for slot in ("ally-1", "ally-4", "ally-3", "opponent-2"):
        crop = cv2.imread(str(BASE / "death_171.0" / f"{slot}.png"))
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        for name, rgba in (("t1", T1), ("t2", T2)):
            tmpl = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_BGR2GRAY)
            mask = rgba[:, :, 3]
            th, tw = tmpl.shape
            ih, iw = gray.shape
            scale = min(1.0, ih / th, iw / tw)
            if scale < 1.0:
                tw2, th2 = max(4, int(tw * scale)), max(4, int(th * scale))
                tmpl = cv2.resize(tmpl, (tw2, th2), interpolation=cv2.INTER_AREA)
                mask = cv2.resize(mask, (tw2, th2), interpolation=cv2.INTER_NEAREST)
            for method_name, method in (
                ("CCORR_NORMED+mask", cv2.TM_CCORR_NORMED),
                ("CCOEFF_NORMED+mask", cv2.TM_CCOEFF_NORMED),
                ("SQDIFF_NORMED+mask", cv2.TM_SQDIFF_NORMED),
            ):
                try:
                    res = cv2.matchTemplate(gray, tmpl, method, mask=mask)
                    val = float(res.min() if method == cv2.TM_SQDIFF_NORMED else res.max())
                    print(f"  {slot} {name} {method_name}: {val:.4f}")
                except cv2.error as exc:
                    print(f"  {slot} {name} {method_name}: ERR {exc}")

    # Synthetic: paint template X onto black crop of same size as ally-4 → should ~1.0
    print("\n=== Sanity: template self-match in black canvas ===")
    for name, tmpl in (("t1", T1G), ("t2", T2G)):
        canvas = np.zeros((111, 111), dtype=np.uint8)
        y0 = (111 - tmpl.shape[0]) // 2
        x0 = (111 - tmpl.shape[1]) // 2
        canvas[y0 : y0 + tmpl.shape[0], x0 : x0 + tmpl.shape[1]] = tmpl
        score = float(cv2.matchTemplate(canvas, tmpl, cv2.TM_CCOEFF_NORMED).max())
        print(f"  {name} self on black: {score:.4f}")

    # Composite: paste opaque template X onto real alive-looking background?
    # Compare correlation of real dead crop vs same crop with estimated X removed.
    print("\n=== Production scores for visually-clear X slots @171 ===")
    import csv

    rows = list(
        csv.DictReader((BASE / "scores_summary.tsv").open(), delimiter="\t")
    )
    for r in rows:
        if float(r["frame_time"]) == 171.0 and r["slot"] in {
            "ally-1",
            "ally-2",
            "ally-3",
            "ally-4",
            "opponent-2",
        }:
            print(
                f"  {r['slot']}: best_clahe={r['best_clahe']} best_raw={r['best_raw']}"
            )


if __name__ == "__main__":
    main()
