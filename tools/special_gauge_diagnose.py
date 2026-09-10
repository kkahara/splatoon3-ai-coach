#!/usr/bin/env python3
"""Offline Special-gauge diagnostics for survey representative frames."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
from loguru import logger

from splatoon3_ai_coach.config.models import SpecialGaugeDetectorConfig
from splatoon3_ai_coach.vision.special_gauge import (
    SpecialGaugeDetector,
    write_special_gauge_diagnostic,
)

REPO = Path(__file__).resolve().parents[1]
REP = REPO / "analysis" / "special_gauge_survey" / "representatives"
OUT = REPO / "analysis" / "special_gauge_survey" / "diagnostics"


def main() -> None:
    """Run the detector on representative crops and write overlays."""
    OUT.mkdir(parents=True, exist_ok=True)
    crop_cfg = SpecialGaugeDetectorConfig(
        roi=(0.0, 0.0, 1.0, 1.0),
        prompt_roi=(0.72, 0.0, 1.0, 0.55),
    )
    detector = SpecialGaugeDetector(crop_cfg)
    rows: list[dict[str, object]] = []
    for path in sorted(REP.glob("*_crop.jpg")):
        image = cv2.imread(str(path))
        if image is None:
            logger.warning("skip unreadable {}", path)
            continue
        reading, confidence = detector.detect(image)
        assert reading is not None
        out_path = OUT / f"{path.stem}_diag.jpg"
        write_special_gauge_diagnostic(
            image,
            reading,
            detector.last_debug,
            out_path,
            roi=crop_cfg.roi,
        )
        rows.append(
            {
                "file": path.name,
                "visible": reading.visible,
                "fill_fraction": reading.fill_fraction,
                "ready": reading.ready,
                "dial_score": reading.dial_score,
                "ready_prompt_score": reading.ready_prompt_score,
                "confidence": confidence,
                "diagnostic": str(out_path.relative_to(REPO)),
            }
        )
        logger.info(
            "{} vis={} fill={} ready={} conf={:.2f}",
            path.name,
            reading.visible,
            reading.fill_fraction,
            reading.ready,
            confidence,
        )

    metrics_path = OUT / "metrics.json"
    metrics_path.write_text(json.dumps({"crops": rows}, indent=2), encoding="utf-8")
    logger.info("wrote {} ({} crops)", metrics_path, len(rows))


if __name__ == "__main__":
    main()
