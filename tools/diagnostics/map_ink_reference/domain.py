"""Point domain: production active sample mask ∩ S/V candidate gate (blind)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from map_ink_reference.constants import DOMAIN_S_MIN, DOMAIN_V_MIN
from map_ink_reference.errors import MalformedInput
from map_ink_reference.stage_identity import StageIdentity
from splatoon3_ai_coach.vision.map_ink import _sample_mask
from splatoon3_ai_coach.vision.stage_maps import resolve_stage_map_geometry
from splatoon3_ai_coach.vision.stage_mask import resolve_stage_mask


def candidate_gate(image_bgr: np.ndarray) -> np.ndarray:
    """OpenCV HSV ``S >= 70`` and ``V >= 40`` (literal sealed values)."""
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    return (hsv[:, :, 1] >= DOMAIN_S_MIN) & (hsv[:, :, 2] >= DOMAIN_V_MIN)


def sample_mask(
    geometry_dir: Path, identity: StageIdentity, height: int, width: int
) -> tuple[np.ndarray, str]:
    """Production active sample mask (stage polygon, else ROI union)."""
    geometry = resolve_stage_map_geometry(
        geometry_dir, stage_id=identity.stage_id, battle_mode_id=identity.battle_mode_id
    )
    if geometry is None:
        raise MalformedInput(f"no stage geometry for {identity.stage_id!r}")
    stage_mask = resolve_stage_mask(geometry_dir, stage_id=identity.stage_id)
    return _sample_mask(height, width, geometry, stage_mask)


def point_domain(
    image_bgr: np.ndarray, geometry_dir: Path, identity: StageIdentity
) -> np.ndarray:
    """Boolean domain mask for one extracted frame."""
    if image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise MalformedInput("frame must be an HxWx3 BGR image")
    height, width = image_bgr.shape[:2]
    mask, _source = sample_mask(geometry_dir, identity, height, width)
    return mask & candidate_gate(image_bgr)
