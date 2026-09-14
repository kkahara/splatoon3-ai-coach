"""Playable-stage geometry mask for map-ink sampling.

Answers only: is this pixel part of the stage? No ink / team-color logic.
When ``stage_mask.yaml`` is present for a stage, map ink measures over the
filled polygon (optional erosion). Otherwise callers keep ROI-union sampling.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import yaml
from loguru import logger
from pydantic import BaseModel, Field, field_validator

STAGE_MASK_FILENAME = "stage_mask.yaml"


class StageMaskConfig(BaseModel):
    """Normalized playable-stage polygon for one ``stage_id``."""

    stage_id: str
    polygon: list[tuple[float, float]] = Field(min_length=3)
    erosion_pixels: int = Field(default=0, ge=0)

    @field_validator("polygon")
    @classmethod
    def _check_polygon(
        cls, points: list[tuple[float, float]]
    ) -> list[tuple[float, float]]:
        cleaned: list[tuple[float, float]] = []
        for point in points:
            if len(point) != 2:
                raise ValueError(f"polygon vertex must be [x, y], got {point!r}")
            x, y = float(point[0]), float(point[1])
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError(f"polygon vertex must be in [0, 1]: {(x, y)}")
            cleaned.append((x, y))
        if len(cleaned) < 3:
            raise ValueError("polygon requires at least 3 vertices")
        return cleaned


def load_stage_mask(path: Path) -> StageMaskConfig:
    """Load one ``stage_mask.yaml`` file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return StageMaskConfig.model_validate(raw)


def resolve_stage_mask(
    geometry_dir: Path | None,
    *,
    stage_id: str,
) -> StageMaskConfig | None:
    """Return ``geometry_dir/<stage_id>/stage_mask.yaml`` when present."""
    if geometry_dir is None or not geometry_dir.is_dir():
        return None
    path = geometry_dir / stage_id / STAGE_MASK_FILENAME
    if not path.is_file():
        return None
    try:
        config = load_stage_mask(path)
    except Exception as exc:  # noqa: BLE001 — skip bad mask; keep ROI fallback
        logger.warning("Invalid stage mask at {}: {}", path, exc)
        return None
    if config.stage_id != stage_id:
        logger.warning(
            "stage_mask stage_id={!r} does not match directory {!r}; using file",
            config.stage_id,
            stage_id,
        )
    return config


def stage_mask_to_bool(
    config: StageMaskConfig,
    height: int,
    width: int,
) -> np.ndarray:
    """Rasterize the normalized polygon to a boolean HxW mask."""
    if height <= 0 or width <= 0:
        raise ValueError(f"frame size must be positive, got {width}x{height}")
    mask_u8 = np.zeros((height, width), dtype=np.uint8)
    pts = np.array(
        [[int(x * width), int(y * height)] for x, y in config.polygon],
        dtype=np.int32,
    )
    cv2.fillPoly(mask_u8, [pts], 255)
    if config.erosion_pixels > 0:
        k = int(config.erosion_pixels) * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        mask_u8 = cv2.erode(mask_u8, kernel)
    return mask_u8 > 0


def write_stage_mask(path: Path, config: StageMaskConfig) -> None:
    """Persist a stage mask YAML (normalized vertices)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "stage_id": config.stage_id,
        "polygon": [[float(x), float(y)] for x, y in config.polygon],
        "erosion_pixels": int(config.erosion_pixels),
    }
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, default_flow_style=None),
        encoding="utf-8",
    )


def render_stage_mask_overlay(
    image: np.ndarray,
    config: StageMaskConfig,
    *,
    alpha: float = 0.45,
) -> tuple[np.ndarray, int, int]:
    """Semi-transparent mask overlay plus inside/outside pixel counts.

    Returns ``(overlay_bgr, inside_pixels, outside_pixels)``.
    """
    height, width = image.shape[:2]
    mask = stage_mask_to_bool(config, height, width)
    inside = int(np.count_nonzero(mask))
    outside = int(height * width - inside)
    canvas = image.copy()
    tint = np.zeros_like(canvas)
    tint[:, :] = (40, 200, 40)
    canvas[mask] = (
        canvas[mask].astype(np.float32) * (1.0 - alpha)
        + tint[mask].astype(np.float32) * alpha
    ).astype(np.uint8)
    pts = np.array(
        [[int(x * width), int(y * height)] for x, y in config.polygon],
        dtype=np.int32,
    )
    cv2.polylines(canvas, [pts], isClosed=True, color=(0, 255, 255), thickness=2)
    for index, (x, y) in enumerate(pts):
        cv2.circle(canvas, (int(x), int(y)), 4, (0, 165, 255), -1)
        cv2.putText(
            canvas,
            str(index),
            (int(x) + 6, int(y) - 6),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    lines = [
        f"stage_id={config.stage_id}",
        f"vertices={len(config.polygon)}",
        f"erosion_pixels={config.erosion_pixels}",
        f"inside={inside}",
        f"outside={outside}",
    ]
    for i, line in enumerate(lines):
        y = 28 + i * 22
        cv2.putText(
            canvas, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA
        )
        cv2.putText(
            canvas,
            line,
            (12, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    return canvas, inside, outside
