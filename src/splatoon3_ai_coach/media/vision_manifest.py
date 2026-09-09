"""Reading and writing vision manifests."""

import json
from pathlib import Path

from loguru import logger

from splatoon3_ai_coach.config.models import VisionConfig
from splatoon3_ai_coach.exceptions import ManifestError
from splatoon3_ai_coach.vision.canonical import canonical_json, sha256_hex
from splatoon3_ai_coach.vision.models import VisionManifest

VISION_MANIFEST_FILENAME = "vision_manifest.json"


def hash_extraction_manifest(path: Path) -> str:
    """Hash the canonical bytes of an extraction manifest file."""
    manifest_file = path / "manifest.json" if path.is_dir() else path
    return sha256_hex(manifest_file.read_bytes())


def hash_vision_config(config: VisionConfig) -> str:
    """Hash a portable semantic view of the vision configuration."""
    payload = config.model_dump(mode="json")
    payload["timer"]["template_dir"] = Path(payload["timer"]["template_dir"]).name
    for key in ("death", "splat", "respawn", "map_overlay", "match_intro", "player_count"):
        section = payload.get(key) or {}
        template_dir = section.get("template_dir")
        if template_dir:
            section["template_dir"] = Path(template_dir).name
            payload[key] = section
    map_ink = payload.get("map_ink") or {}
    geometry_dir = map_ink.get("geometry_dir")
    if geometry_dir:
        map_ink["geometry_dir"] = Path(geometry_dir).name
        payload["map_ink"] = map_ink
    return sha256_hex(canonical_json(payload))


def save_vision_manifest(manifest: VisionManifest, output_dir: Path) -> Path:
    """Write a vision manifest to disk."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / VISION_MANIFEST_FILENAME
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    logger.info("Wrote vision manifest to {}", path)
    return path


def load_vision_manifest(path: Path) -> VisionManifest:
    """Load and validate a vision manifest."""
    manifest_file = path / VISION_MANIFEST_FILENAME if path.is_dir() else path
    if not manifest_file.exists():
        raise ManifestError(f"Vision manifest not found: {manifest_file}")
    try:
        text = manifest_file.read_text(encoding="utf-8")
        return VisionManifest.model_validate_json(text)
    except Exception as exc:
        raise ManifestError(f"Invalid vision manifest at {manifest_file}: {exc}") from exc
