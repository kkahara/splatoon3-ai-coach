"""Registered gameplay polygon for map-ink sampling.

Answers only whether a pixel is inside the operator's outline. The interior
is not restricted to paintable terrain. Production never falls back to rectangles.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np
import yaml
from loguru import logger
from pydantic import BaseModel, Field, field_validator

STAGE_MASK_FILENAME = "stage_mask.yaml"


class OverlayMapPolygonConfig(BaseModel):
    """Registered gameplay polygon; not a segmentation of paintable terrain."""

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


def load_overlay_map_polygon(path: Path) -> OverlayMapPolygonConfig:
    """Load one ``stage_mask.yaml`` file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return OverlayMapPolygonConfig.model_validate(raw)


def overlay_polygon_sha256(config: OverlayMapPolygonConfig) -> str:
    """Identify the normalized polygon and erosion settings used for a sample."""
    return hashlib.sha256(config.model_dump_json().encode("utf-8")).hexdigest()


def resolve_overlay_map_polygon(
    geometry_dir: Path | None,
    *,
    stage_id: str,
) -> OverlayMapPolygonConfig | None:
    """Return ``geometry_dir/<stage_id>/stage_mask.yaml`` when present."""
    if geometry_dir is None or not geometry_dir.is_dir():
        return None
    path = geometry_dir / stage_id / STAGE_MASK_FILENAME
    if not path.is_file():
        return None
    try:
        config = load_overlay_map_polygon(path)
    except Exception as exc:  # noqa: BLE001 — invalid registration is unavailable
        logger.warning("Invalid stage mask at {}: {}", path, exc)
        return None
    if config.stage_id != stage_id:
        logger.warning(
            "stage_mask stage_id={!r} does not match directory {!r}; skipping",
            config.stage_id,
            stage_id,
        )
        return None
    return config


def overlay_polygon_to_bool(
    config: OverlayMapPolygonConfig,
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


def write_overlay_map_polygon(path: Path, config: OverlayMapPolygonConfig) -> None:
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


def render_overlay_map_polygon(
    image: np.ndarray,
    config: OverlayMapPolygonConfig,
    *,
    alpha: float = 0.45,
) -> tuple[np.ndarray, int, int]:
    """Semi-transparent mask overlay plus inside/outside pixel counts.

    Returns ``(overlay_bgr, inside_pixels, outside_pixels)``.
    """
    height, width = image.shape[:2]
    mask = overlay_polygon_to_bool(config, height, width)
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
            canvas,
            line,
            (12, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 0, 0),
            3,
            cv2.LINE_AA,
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
