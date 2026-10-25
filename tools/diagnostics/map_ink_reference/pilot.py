"""Pilot material from the sealed pilot-source manifest (blind; Amendment 4).

No tool searches the filesystem for videos: only operator-declared entries of
``amendment3-pilot-sources/v1`` are considered, and only their declared
paths are opened. Every declared entry is checked before any decode (corpus
and directory exclusions, capture packages, both file hashes, the analysis
manifest's ``video_identity``); any failure is a STOP, never a silent skip.
Amendment 3's selection rule is then applied to the declared entries: stage
geometry (pilot blind-input manifest), at least 8 eligible intervals and a
verified source video; the two smallest ``video_identity`` values are taken.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from map_ink_reference.constants import (
    CORRECTNESS_APPLICABLE,
    COVERAGE_ONLY,
    PILOT_AUTHORIZATION,
    PILOT_SOURCE_SCHEMA,
    STAGE_FRAME_COUNT,
)
from map_ink_reference.errors import MalformedInput, PinMismatch
from map_ink_reference.io_utils import read_json, sha256_file
from map_ink_reference.pins import (
    MEDIA_PINS,
    corpus_manifest_pins,
    require_pinned_geometry,
    verify_pin,
)
from map_ink_reference.population import eligible_intervals, reduce_manifest
from map_ink_reference.seal import require_in_order
from map_ink_reference.stage_identity import load_pilot_blind_input, step2a_paths
from splatoon3_ai_coach.media.video_identity import video_identity

CAPTURE_PACKAGE_FILENAME = "capture_package.json"
EXCLUDED_DIRS = ("alignment-v3-heldout", "review_validation")
ANALYSIS_ROOT = "analysis"
PILOT_COUNT = 2
TOP_KEYS = frozenset({"schema", "entries"})
ENTRY_KEYS = frozenset(
    {
        "analysis_dir",
        "video_path",
        "video_sha256",
        "video_identity",
        "authorization",
        "declared_by",
    }
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class PilotSource:
    """One operator-declared pilot source."""

    analysis_dir: str
    video_path: str
    video_sha256: str
    video_identity: str
    authorization: str
    declared_by: str

    @property
    def recording(self) -> str:
        """Recording name used in the pilot blind-input manifest."""
        return PurePosixPath(self.analysis_dir).name

    @property
    def vision_manifest_path(self) -> str:
        """Repo-relative analysis manifest path."""
        return f"{self.analysis_dir}/vision_manifest.json"


def _entry(raw: Any) -> PilotSource:
    if not isinstance(raw, dict) or set(raw) != ENTRY_KEYS:
        raise MalformedInput("pilot-source entry must have exactly the sealed keys")
    if any(not isinstance(raw[k], str) or not raw[k] for k in ENTRY_KEYS):
        raise MalformedInput("pilot-source entry values must be non-empty strings")
    if not _HEX64.match(raw["video_sha256"]) or not _HEX64.match(raw["video_identity"]):
        raise MalformedInput("pilot-source hashes must be 64 lowercase hex digits")
    if raw["authorization"] != PILOT_AUTHORIZATION:
        raise MalformedInput("pilot-source authorization is not the sealed literal")
    return PilotSource(**raw)


def load_pilot_source_manifest(path: Path) -> list[PilotSource]:
    """Validate an ``amendment3-pilot-sources/v1`` manifest."""
    payload = read_json(path)
    if not isinstance(payload, dict) or set(payload) != TOP_KEYS:
        raise MalformedInput(
            f"pilot-source manifest must have exactly {sorted(TOP_KEYS)}"
        )
    if payload["schema"] != PILOT_SOURCE_SCHEMA:
        raise MalformedInput("pilot-source manifest has the wrong schema")
    if not isinstance(payload["entries"], list) or not payload["entries"]:
        raise MalformedInput("pilot-source manifest declares no entries")
    sources = [_entry(raw) for raw in payload["entries"]]
    for key in ("analysis_dir", "video_path", "video_sha256", "video_identity"):
        values = [getattr(s, key) for s in sources]
        if len(set(values)) != len(values):
            raise MalformedInput(f"duplicate {key} in pilot-source manifest")
    if len({s.recording for s in sources}) != len(sources):
        raise MalformedInput("duplicate analysis directory name in pilot-source manifest")
    return sources


def corpus_exclusions(repo: Path) -> tuple[set[str], set[str]]:
    """Corpus ``video_identity`` values (all seven) and pinned video SHA-256 values."""
    identities: set[str] = set()
    for rel, digest in corpus_manifest_pins().items():
        verify_pin(repo / rel, digest)
        identities.add(str(read_json(repo / rel)["video_identity"]))
    if len(identities) != len(CORRECTNESS_APPLICABLE) + len(COVERAGE_ONLY):
        raise MalformedInput("all seven corpus video identities are required")
    return identities, {pin.video_sha256 for pin in MEDIA_PINS.values()}


def _video_file(repo: Path, source: PilotSource) -> Path:
    path = Path(source.video_path)
    return path if path.is_absolute() else repo / path


def _in_capture_package(directory: Path, video: Path) -> bool:
    if (directory / CAPTURE_PACKAGE_FILENAME).exists():
        return True
    return any((parent / CAPTURE_PACKAGE_FILENAME).exists() for parent in video.parents)


def check_declared_source(
    repo: Path, source: PilotSource, exclusions: tuple[set[str], set[str]]
) -> None:
    """Metadata-only checks of one declared entry (no media is read)."""
    parts = PurePosixPath(source.analysis_dir).parts
    if len(parts) != 2 or parts[0] != ANALYSIS_ROOT:
        raise MalformedInput(f"{source.analysis_dir}: not directly under analysis/")
    if parts[1].startswith("_") or parts[1] in EXCLUDED_DIRS:
        raise MalformedInput(f"{source.analysis_dir}: excluded analysis directory")
    directory = repo / source.analysis_dir
    if not (directory / "vision_manifest.json").is_file():
        raise MalformedInput(f"{source.analysis_dir}: no vision_manifest.json")
    if _in_capture_package(directory, _video_file(repo, source)):
        raise MalformedInput(f"{source.analysis_dir}: capture-package material")
    identities, shas = exclusions
    if source.video_identity in identities or source.video_sha256 in shas:
        raise MalformedInput(f"{source.analysis_dir}: corpus recording declared as pilot")
    manifest = read_json(repo / source.vision_manifest_path)
    if (
        not isinstance(manifest, dict)
        or manifest.get("video_identity") != source.video_identity
    ):
        raise PinMismatch(f"{source.analysis_dir}: manifest video_identity differs")


def verify_source_video(repo: Path, source: PilotSource) -> Path:
    """Hash the declared file and STOP unless both identities match."""
    path = _video_file(repo, source)
    if not path.is_file():
        raise MalformedInput(f"declared pilot video is missing: {source.video_path}")
    if sha256_file(path) != source.video_sha256:
        raise PinMismatch(f"{source.video_path}: SHA-256 differs from the declaration")
    if video_identity(path) != source.video_identity:
        raise PinMismatch(
            f"{source.video_path}: video_identity differs from the declaration"
        )
    return path


def _bind(sources: Sequence[PilotSource], entries: Iterable[Mapping[str, Any]]) -> dict:
    by_name = {e["recording"]: e for e in entries}
    if set(by_name) != {s.recording for s in sources}:
        raise MalformedInput(
            "pilot blind-input manifest does not cover the declared sources"
        )
    for s in sources:
        e = by_name[s.recording]
        if (
            e["vision_manifest_path"] != s.vision_manifest_path
            or e["video_sha256"] != s.video_sha256
            or e["video_identity"] != s.video_identity
        ):
            raise PinMismatch(
                f"{s.recording}: pilot blind-input entry differs from source"
            )
    return by_name


def _eligibility(repo: Path, entry: Mapping[str, Any]) -> dict[str, Any]:
    path = repo / entry["vision_manifest_path"]
    verify_pin(path, entry["vision_manifest_sha256"])
    intervals = len(eligible_intervals(reduce_manifest(read_json(path))))
    has_geometry = entry["stage_id"] is not None
    if has_geometry:
        require_pinned_geometry(repo, entry["stage_id"], entry["battle_mode_id"])
    return {
        "recording": entry["recording"],
        "video_identity": entry["video_identity"],
        "stage_id": entry["stage_id"],
        "battle_mode_id": entry["battle_mode_id"],
        "eligible_intervals": intervals,
        "eligible": has_geometry and intervals >= STAGE_FRAME_COUNT,
    }


def select_pilot(repo: Path, records: Path) -> dict[str, Any]:
    """Step 3 selection after step 2a; every declared entry is verified first."""
    paths = step2a_paths(records)
    require_in_order(repo, paths)
    source_path = paths[0]
    sources = load_pilot_source_manifest(source_path)
    entries = load_pilot_blind_input(repo, records)["recordings"]
    by_name = _bind(sources, entries)
    exclusions = corpus_exclusions(repo)
    for source in sources:
        check_declared_source(repo, source, exclusions)
    for source in sources:
        verify_source_video(repo, source)
    rows = [_eligibility(repo, by_name[s.recording]) for s in sources]
    eligible = sorted(
        (r for r in rows if r["eligible"]), key=lambda r: r["video_identity"]
    )
    if len(eligible) < PILOT_COUNT:
        raise MalformedInput(f"only {len(eligible)} declared pilot sources are eligible")
    chosen = [r["recording"] for r in eligible[:PILOT_COUNT]]
    return {
        "pilot_source_manifest_sha256": sha256_file(source_path),
        "declared": rows,
        "chosen": chosen,
    }
