"""Stage map geometry: multi-rectangle sampling regions for 2D map ink.

Geometry is separate from ink classification. Packs are keyed by ``stage_id``
default YAML, with optional ``battle_mode_id`` overrides.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from splatoon3_ai_coach.types import NormalizedBox

MATCH_IDENTITY_FILENAME = "match_identity.json"


class StageMapRegion(BaseModel):
    """One rectangular sampling region on the live match map."""

    id: str
    roi: NormalizedBox

    @field_validator("roi")
    @classmethod
    def _check_roi(cls, box: NormalizedBox) -> NormalizedBox:
        x1, y1, x2, y2 = box
        if not all(0.0 <= value <= 1.0 for value in box):
            raise ValueError(f"region coordinates must be in [0, 1]: {box}")
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"region must have positive area: {box}")
        return box


class StageMapGeometry(BaseModel):
    """Sampling regions for one stage (optional battle-mode override)."""

    stage_id: str
    battle_mode_id: str | None = None
    regions: list[StageMapRegion] = Field(default_factory=list)

    @field_validator("regions")
    @classmethod
    def _require_regions(cls, regions: list[StageMapRegion]) -> list[StageMapRegion]:
        if not regions:
            raise ValueError("StageMapGeometry requires at least one region")
        return regions


def load_stage_map_geometry(path: Path) -> StageMapGeometry:
    """Load one geometry YAML file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return StageMapGeometry.model_validate(raw)


def resolve_stage_map_geometry(
    geometry_dir: Path | None,
    *,
    stage_id: str,
    battle_mode_id: str | None,
) -> StageMapGeometry | None:
    """Prefer ``stage/mode.yaml``, else ``stage/default.yaml``.

    Returns ``None`` when no pack exists (caller skips map ink).
    """
    if geometry_dir is None or not geometry_dir.is_dir():
        return None
    stage_dir = geometry_dir / stage_id
    if not stage_dir.is_dir():
        logger.debug("No stage map geometry directory for {}", stage_id)
        return None
    candidates: list[Path] = []
    if battle_mode_id:
        candidates.append(stage_dir / f"{battle_mode_id}.yaml")
    candidates.append(stage_dir / "default.yaml")
    for path in candidates:
        if path.is_file():
            return load_stage_map_geometry(path)
    logger.warning("No default geometry for stage_id={}", stage_id)
    return None
