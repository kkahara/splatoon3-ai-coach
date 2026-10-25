"""Blind-safe Amendment 3 / 4 pins (hashes copied from the sealed text).

Only media, frame-selection manifests, stage geometry, configuration and the
production code that Amendment 4 pins for the intro-latch derivation.
Calibration-bearing pins and the A1 implementation digests live in
``unblinded_pins`` and may be imported only by the comparison/report stages
and the CLI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from map_ink_reference.errors import MalformedInput, PinMismatch
from map_ink_reference.io_utils import sha256_file

V3_DIR_TEMPLATE = "analysis/en-live-playerview-2026-10-11 {rid}"
GEOMETRY_DIR = "configs/stage_maps"
_GEOMETRY_KEY = re.compile(r"^[a-z0-9_]+$")

# Pinned configuration (Amendment 3), read for the classifier and for
# ``vision.match_intro.intro_deadline_seconds`` (Amendment 4).
CONFIG_PIN = (
    "configs/default.yaml",
    "e0b96fb6ac4d747a74e33a835ecba1effdf32317428125421c7c68799c01945a",
)

# Amendment 4: production code identity of ``amendment4-intro-latch-v1``
# (repo path → SHA-256 of the file bytes; unmodified at ``228e037``).
INTRO_LATCH_CODE_PINS: dict[str, str] = {
    "src/splatoon3_ai_coach/vision/match_intro.py": (
        "f84363f90bdd666d652c52065f481039cd004598a9d9ff86270f17529f51df83"
    ),
    "src/splatoon3_ai_coach/vision/models.py": (
        "2a8d0c4df2271268c0b801e5274a01f6acb4c23c93f508f30f43c45a2954aa37"
    ),
    "src/splatoon3_ai_coach/config/models.py": (
        "bb1b690ef807a06b3455a39bceb07b0138757ac54d33b6c381b11233a05a9f8d"
    ),
}


def v3_dir(rid: str) -> str:
    """Repo-relative v3-replay analysis directory."""
    return V3_DIR_TEMPLATE.format(rid=rid)


@dataclass(frozen=True)
class MediaPins:
    """Blind-stage pins for one correctness-applicable recording."""

    recording_id: str
    video_sha256: str
    vision_manifest_sha256: str

    @property
    def vision_manifest_path(self) -> str:
        """Repo-relative v3-replay vision manifest path."""
        return f"{v3_dir(self.recording_id)}/vision_manifest.json"


MEDIA_PINS: dict[str, MediaPins] = {
    pin.recording_id: pin
    for pin in (
        MediaPins(
            "18-18-00",
            "04e9c2799ec385e1c24bcdb9387ad30e75c0b61489d8f74c150568fc4b921f3f",
            "fcd5b55aaa89b5acfcd028d2a140d1ee45a44011acb55e83e9e856b87dea6429",
        ),
        MediaPins(
            "20-33-31",
            "9390fa05c32bc25fbf957b26f7986ce68dfd3d29f1b678bd5a798128b8cafc4a",
            "783295e27da1f98402d9b91c89e0afabf3a77d54658fbe3a4affbf85bc77ca41",
        ),
        MediaPins(
            "22-31-53",
            "9435ceba6f19beeb1e7d8854926c8af9af2ce610531919b526cc28ebf2a414e6",
            "c5e5770da8fa1804e4f1be70290765d6e8cb433db89e758513fa19a908b4eeda",
        ),
        MediaPins(
            "22-37-47",
            "7d069efc4d4798bb6d9d0a92c38320d0cd1d97aaf98b4ac5fc3b51e6e098956c",
            "ee913984c71cf53b19ca821fffa4e4122294c5dcc30c862b3bfa95539441acee",
        ),
        MediaPins(
            "22-57-30",
            "ffb7636db643aa817e600c0597e0ef71bcf36eb7cd61df370eae89e00f4b9e36",
            "1c68c81f8dd76f4fa3d73ae5ac248592fe294fac086697455c4a6f49cd5b776f",
        ),
    )
}

# Coverage-only recordings: pinned v3-replay vision manifests (metadata only;
# their media must never be opened).
COVERAGE_MANIFEST_PINS: dict[str, str] = {
    "18-25-16": "7fcabed3387883fd01ec08793843858d22a920d8d1f65112b8bd87a6c34f9002",
    "22-20-42": "a29a467b3b7d484b21fd6991e1eac410752bf3c87685ab8041de27aedf78f9ef",
}


def corpus_manifest_pins() -> dict[str, str]:
    """Repo-relative v3-replay manifest path → pinned SHA-256, all seven."""
    out = {
        pin.vision_manifest_path: pin.vision_manifest_sha256
        for pin in MEDIA_PINS.values()
    }
    for rid, digest in COVERAGE_MANIFEST_PINS.items():
        out[f"{v3_dir(rid)}/vision_manifest.json"] = digest
    return out


# Stage geometry (point domain).
STAGE_GEOMETRY_PINS: dict[str, str] = {
    "configs/stage_maps/makomart/default.yaml": (
        "ffb3f11d39bec2abb8e976c370c3fea66ab40b3adf1e852ea0c80f7275148ad2"
    ),
    "configs/stage_maps/shipshape_cargo_co/default.yaml": (
        "64ee7e24d9824db005adf0cda55c45345ff706b015095028f9fd0bfd62a781e5"
    ),
    "configs/stage_maps/manta_maria/default.yaml": (
        "4d192e69d981c88d4b0ee9abb9e8869884baeaadcde38a7a34b4364eeeb5b913"
    ),
    "configs/stage_maps/manta_maria/stage_mask.yaml": (
        "f4a4d7a88b65fda9b05aabdb2d0fb50f162f1278997b46810ee54f980b184621"
    ),
    "configs/stage_maps/wahoo_world/default.yaml": (
        "ec9940b9151e8f8574347df6de089df1311fb44c6d0bf374221b5a09eded853a"
    ),
}


def verify_pin(path: Path, expected_sha256: str) -> str:
    """Hash ``path`` and STOP (raise) unless it equals the pinned digest."""
    if not path.is_file():
        raise PinMismatch(f"pinned file is missing: {path}")
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise PinMismatch(
            f"pinned hash mismatch for {path}: expected {expected_sha256}, got {actual}"
        )
    return actual


def verify_geometry_pins(repo: Path) -> Path:
    """Verify every pinned stage-geometry file; return the geometry directory."""
    for rel, digest in STAGE_GEOMETRY_PINS.items():
        verify_pin(repo / rel, digest)
    return repo / GEOMETRY_DIR


def require_pinned_geometry(
    repo: Path, stage_id: str, battle_mode_id: str | None
) -> None:
    """STOP unless the production geometry for this identity resolves to pinned files.

    Production prefers ``<stage>/<mode>.yaml`` over ``<stage>/default.yaml``
    and applies ``<stage>/stage_mask.yaml`` when present, so an existing but
    unpinned mode or mask file is also a pin failure.
    """
    for key in (stage_id, battle_mode_id):
        if key is not None and not _GEOMETRY_KEY.match(key):
            raise MalformedInput(f"invalid stage/mode identifier {key!r}")
    default = f"{GEOMETRY_DIR}/{stage_id}/default.yaml"
    if default not in STAGE_GEOMETRY_PINS:
        raise PinMismatch(f"stage_id {stage_id!r} has no pinned geometry file")
    optional = [f"{GEOMETRY_DIR}/{stage_id}/stage_mask.yaml"]
    if battle_mode_id is not None:
        optional.append(f"{GEOMETRY_DIR}/{stage_id}/{battle_mode_id}.yaml")
    for rel in optional:
        if (repo / rel).exists() and rel not in STAGE_GEOMETRY_PINS:
            raise PinMismatch(f"unpinned geometry file would be used: {rel}")
