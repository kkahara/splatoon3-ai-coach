#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$PROJECT_ROOT/.venv/bin/activate"

export FRAMES_DIR="${1:-$PROJECT_ROOT/analysis/2026-07-07 23-49-06/frames}"
export OUT_DIR="${2:-$PROJECT_ROOT/analysis/timer_match_debug}"

python - <<'PY'
from pathlib import Path
import os

import cv2
import numpy as np

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.vision.glyphs import normalize_glyph
from splatoon3_ai_coach.vision.templates import load_templates
from splatoon3_ai_coach.vision.roi import crop_roi
from splatoon3_ai_coach.vision.timer import (
    diagnose_glyph_match,
    segment_timer_roi,
    write_match_debug,
)

config = load_config(default_config_path())
frames_dir = Path(os.environ["FRAMES_DIR"])
out_dir = Path(os.environ["OUT_DIR"])
out_dir.mkdir(parents=True, exist_ok=True)

selected_names = [
    "000103.750_motion_0110.jpg",
    "000127.983_motion_0137.jpg",
    "000149.400_motion_0161.jpg",
    "000170.833_motion_0185.jpg",
    "000192.267_motion_0209.jpg",
    "000030.533_motion_0031.jpg",
]
selected = [frames_dir / name for name in selected_names if (frames_dir / name).exists()]

templates = load_templates(config.vision.timer.template_dir)
threshold = config.vision.timer.match_threshold

print("=== Normalization path check ===")
print("Calibration tiles are saved via normalize_glyph().")
print("load_templates() also calls normalize_glyph() on each tile.")
sample_template_path = next((config.vision.timer.template_dir / "2").glob("*.png"))
raw_template = cv2.imread(str(sample_template_path), cv2.IMREAD_GRAYSCALE)
once = normalize_glyph(raw_template)
twice = normalize_glyph(once)
print(f"sample template file: {sample_template_path.name} shape={raw_template.shape}")
print(f"normalize once shape={once.shape} twice shape={twice.shape}")
print(f"once==twice identical pixels: {np.array_equal(once, twice)}")
print(
    "max abs diff once vs twice:",
    int(np.max(np.abs(once.astype(int) - twice.astype(int)))),
)
print(f"loaded template[0] shape for '2': {templates['2'][0].shape}")
print(f"match_threshold (unchanged): {threshold}")
print()

for path in selected:
    image = cv2.imread(str(path))
    if image is None:
        print(f"SKIP {path.name}")
        continue
    roi = crop_roi(image, config.vision.timer.roi)
    segmentation = segment_timer_roi(roi)
    print(f"=== {path.name} final={len(segmentation.final_boxes)} ===")
    for index, (box, glyph) in enumerate(
        zip(segmentation.final_boxes, segmentation.glyphs, strict=True)
    ):
        diag = diagnose_glyph_match(glyph, templates, threshold)
        ranked = sorted(
            diag.scores_by_symbol.items(), key=lambda item: item[1], reverse=True
        )
        print(
            f"  [{index}] label={box.label} bbox={box.w}x{box.h} "
            f"norm={diag.normalized_shape[0]}x{diag.normalized_shape[1]} "
            f"best={diag.best_symbol}:{diag.best_score:.3f} "
            f"accepted={diag.accepted} "
            f"compared={diag.templates_compared} "
            f"shape_skip={diag.shape_mismatches}"
        )
        print("       top scores:", ", ".join(f"{s}:{v:.3f}" for s, v in ranked[:6]))
        out = out_dir / f"{path.stem}_g{index}_{box.label}.png"
        write_match_debug(diag, out, expected_label=box.label)
        print(f"       wrote {out.name}")
    print()
PY
