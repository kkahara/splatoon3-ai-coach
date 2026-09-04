#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$PROJECT_ROOT/.venv/bin/activate"

FRAMES_DIR="${1:-$PROJECT_ROOT/analysis/2026-07-07 23-49-06/frames}"
OUT_DIR="${2:-$PROJECT_ROOT/analysis/timer_debug}"

python - <<PY
from pathlib import Path

import cv2

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.vision.templates import load_templates
from splatoon3_ai_coach.vision.roi import crop_roi
from splatoon3_ai_coach.vision.timer import (
    extract_timer_display,
    match_glyph,
    segment_timer_roi,
    write_segmentation_debug,
)

config = load_config(default_config_path())
frames_dir = Path(r"""$FRAMES_DIR""")
out_dir = Path(r"""$OUT_DIR""")
out_dir.mkdir(parents=True, exist_ok=True)

candidates = sorted(frames_dir.glob("*_motion_*.jpg"))
step = max(len(candidates) // 10, 1)
selected = candidates[::step][:10]
templates = load_templates(config.vision.timer.template_dir)

print(f"ROI={config.vision.timer.roi}")
print(f"Writing diagnostics to {out_dir}")
print(f"Selected {len(selected)} frames")

for path in selected:
    image = cv2.imread(str(path))
    if image is None:
        print(f"SKIP load failed: {path.name}")
        continue
    roi = crop_roi(image, config.vision.timer.roi)
    result = segment_timer_roi(roi)
    debug_path = write_segmentation_debug(
        roi, result, out_dir / f"{path.stem}_seg.png"
    )

    symbols = []
    for glyph in result.glyphs:
        symbol, _score = match_glyph(
            glyph, templates, config.vision.timer.match_threshold
        )
        symbols.append(":" if symbol == "colon" else (symbol or "?"))
    extracted = extract_timer_display("".join(s for s in symbols if s != "?"))
    print(
        f"{path.name}: raw={len(result.raw_boxes)} "
        f"filtered={len(result.filtered_boxes)} "
        f"rejected={len(result.rejected_boxes)} "
        f"final={len(result.final_boxes)} "
        f"labels={[b.label for b in result.final_boxes]} "
        f"match={symbols} extracted={extracted} -> {debug_path.name}"
    )
PY
