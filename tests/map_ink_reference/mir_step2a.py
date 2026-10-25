"""Synthetic step 2a workspace (Amendment 4): no corpus media or manifests.

Writes synthetic v3-replay manifests for the seven recording ids and
synthetic pilot sources inside a temporary repository, and points the pin
tables at them. The real derivation (``amendment4-intro-latch-v1`` through
the production ``MatchIdentityTracker``), writers, loaders and seal checks
run unchanged; only the production code/config identity checks are replaced
because the temporary repository does not contain the production files.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import map_ink_reference.intro_latch as intro_latch
import map_ink_reference.pins as pins
import pytest
from map_ink_reference.constants import (
    AMENDMENT4_COMMIT,
    CORRECTNESS_APPLICABLE,
    COVERAGE_ONLY,
    PILOT_AUTHORIZATION,
    PILOT_SOURCE_SCHEMA,
    SCOPE_CORPUS,
    SCOPE_PILOT,
)
from map_ink_reference.io_utils import sha256_file, write_json
from map_ink_reference.stage_identity import step2a_paths
from mir_helpers import commit, interval_specs, manifest_from_specs

from splatoon3_ai_coach.media.video_identity import video_identity

SYNTH_STAGE = "synth"
TOOL_COMMIT = "a" * 40


def intro_detection(stage: str | None, mode: str | None, score: float = 0.9) -> dict:
    """A stored ``match_intro`` detection as the vision manifest holds it."""
    return {
        "detector_name": "match_intro",
        "reading": {
            "kind": "match_intro",
            "stage_id": stage,
            "battle_mode_id": mode,
            "stage_template_score": score if stage else 0.0,
            "battle_mode_template_score": score if mode else 0.0,
        },
    }


def with_intro(
    manifest: dict, readings: list[tuple[int, str | None, str | None]]
) -> dict:
    """Attach intro detections to frames by index."""
    for index, stage, mode in readings:
        manifest["frame_results"][index]["detections"].append(
            intro_detection(stage, mode)
        )
    return manifest


def corpus_manifest(rid: str, stage: str | None = SYNTH_STAGE) -> dict:
    """Synthetic v3-replay manifest: ten eligible intervals and one intro reading."""
    manifest = manifest_from_specs(interval_specs([2] * 10), video_identity=f"vid-{rid}")
    return with_intro(manifest, [(0, stage, None)])


def _write(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return sha256_file(path)


class SyntheticStep2a:
    """Synthetic corpus/pilot inputs plus the three step 2a records."""

    def __init__(
        self, repo: Path, records: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.repo, self.records, self.mp = repo, records, monkeypatch
        monkeypatch.setattr(
            intro_latch,
            "verify_code_identity",
            lambda repo, modules=None: dict(pins.INTRO_LATCH_CODE_PINS),
        )
        monkeypatch.setattr(intro_latch, "intro_deadline_seconds", lambda repo: 90.0)
        monkeypatch.setitem(
            pins.STAGE_GEOMETRY_PINS,
            f"{pins.GEOMETRY_DIR}/{SYNTH_STAGE}/default.yaml",
            "0",
        )
        for rid in (*CORRECTNESS_APPLICABLE, *COVERAGE_ONLY):
            self.write_corpus_manifest(rid, corpus_manifest(rid))
        self.sources = [
            self.pilot_source("pilot-a", 10),
            self.pilot_source("pilot-b", 9),
            self.pilot_source("pilot-c", 5),
        ]

    def write_corpus_manifest(self, rid: str, manifest: dict) -> str:
        """Write a corpus manifest and pin it (as the blind pins would)."""
        rel = f"{pins.v3_dir(rid)}/vision_manifest.json"
        digest = _write(self.repo / rel, manifest)
        if rid in CORRECTNESS_APPLICABLE:
            video_sha = hashlib.sha256(f"video {rid}".encode()).hexdigest()
            self.mp.setitem(pins.MEDIA_PINS, rid, pins.MediaPins(rid, video_sha, digest))
        else:
            self.mp.setitem(pins.COVERAGE_MANIFEST_PINS, rid, digest)
        return digest

    def pilot_source(
        self, name: str, intervals: int, stage: str | None = SYNTH_STAGE
    ) -> dict[str, str]:
        """A declared pilot entry with a synthetic (non-decodable) video file."""
        video = self.repo / "videos" / f"{name}.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(f"synthetic video bytes {name}".encode())
        ident = video_identity(video)
        manifest = manifest_from_specs(
            interval_specs([2] * intervals), video_identity=ident
        )
        _write(
            self.repo / "analysis" / name / "vision_manifest.json",
            with_intro(manifest, [(0, stage, None)]),
        )
        return {
            "analysis_dir": f"analysis/{name}",
            "video_path": f"videos/{name}.mp4",
            "video_sha256": sha256_file(video),
            "video_identity": ident,
            "authorization": PILOT_AUTHORIZATION,
            "declared_by": "synthetic operator",
        }

    def write_source_manifest(self, entries: list[dict] | None = None) -> Path:
        path = step2a_paths(self.records)[0]
        write_json(
            path, {"schema": PILOT_SOURCE_SCHEMA, "entries": entries or self.sources}
        )
        return path

    def write_blind(self, scope: str) -> Path:
        return intro_latch.write_blind_input_manifest(
            self.repo,
            self.records,
            scope,
            tool_commit=TOOL_COMMIT,
            amendment4_commit=AMENDMENT4_COMMIT,
        )

    def seal(self) -> None:
        """Step 2a: source, pilot blind-input, corpus blind-input, in that order."""
        commit(self.repo, [self.write_source_manifest()], "2a pilot sources")
        commit(self.repo, [self.write_blind(SCOPE_PILOT)], "2a pilot blind input")
        commit(self.repo, [self.write_blind(SCOPE_CORPUS)], "2a corpus blind input")
