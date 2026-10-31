"""Legacy import compatibility; use vision.overlay_map_polygon in new code."""

from splatoon3_ai_coach.vision.overlay_map_polygon import (
    STAGE_MASK_FILENAME,
)
from splatoon3_ai_coach.vision.overlay_map_polygon import (
    OverlayMapPolygonConfig as StageMaskConfig,
)
from splatoon3_ai_coach.vision.overlay_map_polygon import (
    load_overlay_map_polygon as load_stage_mask,
)
from splatoon3_ai_coach.vision.overlay_map_polygon import (
    overlay_polygon_sha256 as stage_mask_sha256,
)
from splatoon3_ai_coach.vision.overlay_map_polygon import (
    overlay_polygon_to_bool as stage_mask_to_bool,
)
from splatoon3_ai_coach.vision.overlay_map_polygon import (
    render_overlay_map_polygon as render_stage_mask_overlay,
)
from splatoon3_ai_coach.vision.overlay_map_polygon import (
    resolve_overlay_map_polygon as resolve_stage_mask,
)
from splatoon3_ai_coach.vision.overlay_map_polygon import (
    write_overlay_map_polygon as write_stage_mask,
)

__all__ = [
    "StageMaskConfig",
    "load_stage_mask",
    "resolve_stage_mask",
    "stage_mask_sha256",
    "stage_mask_to_bool",
    "write_stage_mask",
    "render_stage_mask_overlay",
    "STAGE_MASK_FILENAME",
]
