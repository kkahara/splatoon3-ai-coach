#!/usr/bin/env python3
"""Offline map-union color audit (instrument only — no team-color calibration).

Reports dominant saturated H clusters inside the stage-map union and contrasts
them with the current fixed ``MapInkClassifier`` labels. Does not change
production HSV ranges or wire ``TeamColorCalibration``.

Examples::

    python tools/map_ink_color_audit.py frame.jpg --stage inkblot_art_academy
    python tools/map_ink_color_audit.py \\
      "analysis/2026-09-09 23-29-20/debug_snapshots/00006360_000106.000.jpg" \\
      --stage inkblot_art_academy --label "106.0s"
    python tools/map_ink_color_audit.py frame.jpg --stage inkblot_art_academy -o /tmp/color_audit.jpg
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from roi_calibrate import load_frame, pick_input_path  # noqa: E402

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.vision.map_ink import MapInkClassifier, _union_region_mask
from splatoon3_ai_coach.vision.stage_maps import (
    StageMapGeometry,
    load_stage_map_geometry,
    resolve_stage_map_geometry,
)

REPO = Path(__file__).resolve().parents[1]
H_MAX = 180  # OpenCV hue range
_CLUSTER_COLORS_BGR: tuple[tuple[int, int, int], ...] = (
    (0, 255, 255),  # yellow
    (255, 0, 255),  # magenta
)
_UNION_COLOR = (255, 220, 0)


@dataclass(frozen=True)
class HueCluster:
    """One H-histogram bin among saturated candidates in the union."""

    h_lo: int
    h_hi: int  # exclusive end, or 180 for last bin
    count: int
    share_of_union: float
    mean_s: float
    std_s: float
    mean_v: float
    std_v: float
    ally_pixels: int
    opponent_pixels: int
    other_pixels: int

    @property
    def current_label(self) -> str:
        """Majority current-classifier label among pixels in this bin."""
        scores = {
            "ally": self.ally_pixels,
            "opponent": self.opponent_pixels,
            "other": self.other_pixels,
        }
        best = max(scores, key=scores.get)
        if scores[best] == 0:
            return "none"
        return best


@dataclass(frozen=True)
class ColorAuditResult:
    """Full audit summary for one frame + geometry."""

    label: str
    stage_id: str
    union_pixels: int
    candidate_pixels: int
    low_sat_pixels: int
    candidate_fraction: float
    clusters: tuple[HueCluster, ...]
    classifier_ally: int
    classifier_opponent: int
    classifier_other: int
    s_min: int
    v_min: int
    bin_width: int


def audit_map_union_colors(
    image: np.ndarray,
    geometry: StageMapGeometry,
    classifier: MapInkClassifier,
    *,
    s_min: int = 70,
    v_min: int = 40,
    bin_width: int = 10,
    top_n: int = 5,
    label: str = "",
) -> ColorAuditResult:
    """Cluster saturated hues inside the stage-map union; contrast fixed classifier."""
    if image.size == 0:
        raise ValueError("empty image")
    if bin_width < 1 or bin_width > H_MAX:
        raise ValueError(f"bin_width must be in 1..{H_MAX}, got {bin_width}")
    if top_n < 1:
        raise ValueError(f"top_n must be >= 1, got {top_n}")

    height, width = image.shape[:2]
    union = _union_region_mask(height, width, geometry.regions)
    union_pixels = int(np.count_nonzero(union))
    if union_pixels == 0:
        raise ValueError("union mask is empty — check stage geometry ROIs")

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    h_ch = hsv[:, :, 0]
    s_ch = hsv[:, :, 1]
    v_ch = hsv[:, :, 2]

    in_union = union
    dark = v_ch < v_min
    # Candidates: in union, saturated enough, not extremely dark.
    candidates = in_union & (s_ch >= s_min) & ~dark
    # In-union pixels failing the S identity gate (includes dark low-S).
    low_sat_all = in_union & (s_ch < s_min)
    candidate_pixels = int(np.count_nonzero(candidates))
    low_sat_pixels = int(np.count_nonzero(low_sat_all))
    candidate_fraction = candidate_pixels / float(union_pixels)

    ally_m, opp_m, other_m = classifier.classify_bgr(image)
    clf_ally = int(np.count_nonzero(ally_m & in_union))
    clf_opp = int(np.count_nonzero(opp_m & in_union))
    clf_other = int(np.count_nonzero(other_m & in_union))

    n_bins = (H_MAX + bin_width - 1) // bin_width
    bin_counts = np.zeros(n_bins, dtype=np.int64)
    if candidate_pixels > 0:
        h_vals = h_ch[candidates].astype(np.int32)
        bin_idx = np.minimum(h_vals // bin_width, n_bins - 1)
        bin_counts = np.bincount(bin_idx, minlength=n_bins).astype(np.int64)

    ranked = np.argsort(-bin_counts)
    clusters: list[HueCluster] = []
    for bi in ranked[:top_n]:
        count = int(bin_counts[bi])
        if count == 0:
            break
        h_lo = int(bi * bin_width)
        h_hi = min(H_MAX, h_lo + bin_width)
        in_bin = candidates & (h_ch >= h_lo) & (h_ch < h_hi if h_hi < H_MAX else h_ch <= 179)
        # Last bin: include H==179
        if h_hi >= H_MAX:
            in_bin = candidates & (h_ch >= h_lo)
        s_vals = s_ch[in_bin].astype(np.float64)
        v_vals = v_ch[in_bin].astype(np.float64)
        clusters.append(
            HueCluster(
                h_lo=h_lo,
                h_hi=h_hi,
                count=count,
                share_of_union=count / float(union_pixels),
                mean_s=float(s_vals.mean()) if count else 0.0,
                std_s=float(s_vals.std()) if count else 0.0,
                mean_v=float(v_vals.mean()) if count else 0.0,
                std_v=float(v_vals.std()) if count else 0.0,
                ally_pixels=int(np.count_nonzero(ally_m & in_bin)),
                opponent_pixels=int(np.count_nonzero(opp_m & in_bin)),
                other_pixels=int(np.count_nonzero(other_m & in_bin)),
            )
        )

    return ColorAuditResult(
        label=label,
        stage_id=geometry.stage_id,
        union_pixels=union_pixels,
        candidate_pixels=candidate_pixels,
        low_sat_pixels=low_sat_pixels,
        candidate_fraction=candidate_fraction,
        clusters=tuple(clusters),
        classifier_ally=clf_ally,
        classifier_opponent=clf_opp,
        classifier_other=clf_other,
        s_min=s_min,
        v_min=v_min,
        bin_width=bin_width,
    )


def format_audit_report(result: ColorAuditResult) -> str:
    """Human-readable audit report."""
    title = f"MAP INK COLOR AUDIT — {result.stage_id}"
    if result.label:
        title += f" @ {result.label}"
    lines = [
        title,
        f"Gates: S>={result.s_min} (identity), V>={result.v_min} (soft dark reject), "
        f"H bin_width={result.bin_width}",
        f"union_pixels:       {result.union_pixels}",
        f"candidate_pixels:   {result.candidate_pixels}",
        f"low_sat_pixels:     {result.low_sat_pixels}",
        f"candidate_fraction: {result.candidate_fraction:.3f}",
        "",
        "Candidate H clusters (histogram over candidates; share_of_union / full union):",
    ]
    if not result.clusters:
        lines.append("  (none)")
    for c in result.clusters:
        lines.extend(
            [
                f"  H[{c.h_lo}-{c.h_hi}):",
                f"    count:            {c.count}",
                f"    share_of_union:   {c.share_of_union:.3f}",
                f"    mean_S / std_S:   {c.mean_s:.1f} / {c.std_s:.1f}",
                f"    mean_V / std_V:   {c.mean_v:.1f} / {c.std_v:.1f}",
                f"    current_label:    {c.current_label} "
                f"(ally={c.ally_pixels} opp={c.opponent_pixels} other={c.other_pixels})",
            ]
        )
    lines.extend(
        [
            "",
            "Current fixed classifier (inside union):",
            f"  ally:       {result.classifier_ally}",
            f"  opponent:   {result.classifier_opponent}",
            f"  other:      {result.classifier_other}",
            "",
            "Note: current_label uses fixed YAML HSV palette — not team binding.",
        ]
    )
    return "\n".join(lines)


def render_audit_overlay(
    image: np.ndarray,
    geometry: StageMapGeometry,
    result: ColorAuditResult,
    *,
    s_min: int | None = None,
    v_min: int | None = None,
) -> np.ndarray:
    """Dim frame; paint top-2 H clusters; cyan union outline."""
    s_min = result.s_min if s_min is None else s_min
    v_min = result.v_min if v_min is None else v_min
    height, width = image.shape[:2]
    union = _union_region_mask(height, width, geometry.regions)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    candidates = (
        union
        & (hsv[:, :, 1] >= s_min)
        & (hsv[:, :, 2] >= v_min)
    )
    canvas = (image.astype(np.float32) * 0.35).astype(np.uint8)
    h_ch = hsv[:, :, 0]
    for index, cluster in enumerate(result.clusters[:2]):
        color = _CLUSTER_COLORS_BGR[index % len(_CLUSTER_COLORS_BGR)]
        if cluster.h_hi >= H_MAX:
            mask = candidates & (h_ch >= cluster.h_lo)
        else:
            mask = candidates & (h_ch >= cluster.h_lo) & (h_ch < cluster.h_hi)
        canvas[mask] = (
            canvas[mask].astype(np.float32) * 0.25 + np.array(color, dtype=np.float32) * 0.75
        ).astype(np.uint8)
    union_u8 = (union.astype(np.uint8) * 255)
    contours, _ = cv2.findContours(union_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(canvas, contours, -1, _UNION_COLOR, 2)
    return canvas


def build_parser() -> argparse.ArgumentParser:
    """CLI for map-ink color audit."""
    parser = argparse.ArgumentParser(
        description=(
            "Audit saturated H clusters inside the stage-map union and contrast "
            "the current fixed map-ink classifier. Instrument only — no team-color "
            "calibration."
        )
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=None,
        help="Still image or video (omit to open a file picker)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--stage", help="Stage id under configs/stage_maps/")
    group.add_argument("--geometry", type=Path, help="Explicit stage geometry YAML")
    parser.add_argument("--mode", default=None, help="Optional battle_mode_id for --stage")
    parser.add_argument("--label", default="", help="Label for the report (e.g. 106.0s)")
    parser.add_argument("--time", type=float, default=None, help="Video seek seconds")
    parser.add_argument("--s-min", type=int, default=70, help="Min saturation for candidates")
    parser.add_argument(
        "--v-min",
        type=int,
        default=40,
        help="Soft min value (darkness reject only)",
    )
    parser.add_argument("--bin-width", type=int, default=10, help="Hue histogram bin width")
    parser.add_argument("--top-n", type=int, default=5, help="Number of top H clusters")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Optional overlay image path",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="App config YAML (default: configs/default.yaml)",
    )
    return parser


def _load_geometry(
    *,
    stage: str | None,
    geometry_path: Path | None,
    mode: str | None,
    config_path: Path | None,
) -> StageMapGeometry:
    if geometry_path is not None:
        return load_stage_map_geometry(geometry_path)
    assert stage is not None
    config = load_config(config_path or default_config_path())
    geometry = resolve_stage_map_geometry(
        config.vision.map_ink.geometry_dir,
        stage_id=stage,
        battle_mode_id=mode,
    )
    if geometry is None:
        raise ValueError(
            f"no geometry pack for stage_id={stage!r} "
            f"(looked under {config.vision.map_ink.geometry_dir})"
        )
    return geometry


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    try:
        path = args.input if args.input is not None else pick_input_path()
        if not path.is_file():
            print(f"input not found: {path}", file=sys.stderr)
            return 1
        config = load_config(args.config or default_config_path())
        geometry = _load_geometry(
            stage=args.stage,
            geometry_path=args.geometry,
            mode=args.mode,
            config_path=args.config,
        )
        image = load_frame(path, args.time)
        classifier = MapInkClassifier(config.vision.map_ink)
        result = audit_map_union_colors(
            image,
            geometry,
            classifier,
            s_min=args.s_min,
            v_min=args.v_min,
            bin_width=args.bin_width,
            top_n=args.top_n,
            label=args.label,
        )
        print(format_audit_report(result))
        if args.output is not None:
            overlay = render_audit_overlay(image, geometry, result)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(args.output), overlay):
                raise ValueError(f"could not write overlay: {args.output}")
            print(f"\nWrote overlay: {args.output}")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
