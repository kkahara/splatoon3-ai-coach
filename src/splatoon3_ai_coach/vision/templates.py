"""Load calibrated timer glyph templates."""

from pathlib import Path

import cv2
import numpy as np

from splatoon3_ai_coach.exceptions import VisionError
from splatoon3_ai_coach.vision.glyphs import normalize_glyph

SYMBOLS = tuple(str(d) for d in range(10)) + ("colon",)


def load_templates(template_dir: Path) -> dict[str, list[np.ndarray]]:
    """Load normalized templates grouped by symbol name."""
    if not template_dir.exists():
        raise VisionError(f"Template directory not found: {template_dir}")

    templates: dict[str, list[np.ndarray]] = {symbol: [] for symbol in SYMBOLS}
    for symbol in SYMBOLS:
        symbol_dir = template_dir / symbol
        if not symbol_dir.exists():
            continue
        for path in sorted(symbol_dir.glob("*")):
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                continue
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                continue
            templates[symbol].append(normalize_glyph(image))

    if not any(templates.values()):
        raise VisionError(f"No templates found under {template_dir}")
    return templates
