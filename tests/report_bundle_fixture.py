"""A synthetic, minimized report folder for report-publishing tests.

Built at test time with the real models and writers (``VisionManifest``,
``compute_analysis_id``, ``make_result_id``, the coaching index and prototype
writers). Every value is synthetic; media come from ffmpeg lavfi, so no
gameplay footage is committed. The JSON shapes carry every identifier-bearing
key path listed in ``IDENTIFIER_PATHS`` (checked against the committed
key-path inventory by ``test_public_report_content``).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from coach_artifact_helpers import index_entry, write_prototype_unit

from splatoon3_ai_coach.coach.artifacts import (
    CoachingIndexEntry,
    unit_file_names,
    write_coaching_index,
)
from splatoon3_ai_coach.vision.ids import (
    compute_analysis_id,
    make_frame_id,
    make_result_id,
)
from splatoon3_ai_coach.vision.models import (
    AnalysisIdentity,
    DetectorResult,
    GameEvent,
    GameEventType,
    GameLanguagePolicy,
    GameLanguageResult,
    GameStateSnapshot,
    RespawnReading,
    SourceFrameReference,
    SplatReading,
    VisionFrameResult,
    VisionManifest,
)

STATEMENT = "Synthetic statement for the published bundle."
INVENTORY = Path(__file__).parent / "fixtures" / "report_bundle" / "key_inventory.json"
IDENTIFIER_LEAVES = frozenset(
    {
        "analysis_id",
        "video_identity",
        "frame_id",
        "evidence_ids",
        "observation_id",
        "victim_name",
        "ocr_text",
    }
)
# Inventory paths the fixture must contain: every fingerprint-bearing or
# forbidden-key path, plus representative JSON-path and code fields.
REPRESENTATIVE = {
    "coach_input": {".score_facts.source_path", ".zone_control_facts.source_path"},
    "coaching": {".supporting_evidence[].path", ".roster_before_death.source_path"},
    "llm_view": {".evidence_limits[].code", ".timeline.source_path"},
    "prototype_llm_view": {".evidence_limits[].code", ".timeline.source_path"},
    "flags": {
        ".evidence_path_resolution[].path",
        ".evidence_path_resolution[].source_path",
    },
    "vision_manifest": {
        ".frame_results[].detections[].id",
        ".frame_results[].frame_path",
        ".extraction_manifest_path",
    },
}
_COACH_INPUT_ID_PATHS = (
    ".player_count_samples[].observation.evidence_ids",
    ".player_count_window[].observation.evidence_ids",
    ".primary_context.score.at_anchor.evidence_ids",
    ".primary_context.score.lookback.evidence_ids",
    ".primary_context.score.pre_death.evidence_ids",
    ".primary_context.special.last_ready_before_anchor.observation_id",
    ".primary_context.special.last_visible_before_anchor.observation_id",
    ".primary_context.special.nearest_before_anchor.observation_id",
    ".primary_context.special.observations[].observation_id",
    ".primary_context.special.ready_onsets[].observation_id",
    ".primary_context.zone_control.at_anchor.evidence_ids",
    ".primary_context.zone_control.lookback.evidence_ids",
    ".primary_context.zone_control.pre_death.evidence_ids",
    ".primary_context.zone_control.trajectory[].evidence_ids",
    ".primary_context.zone_control.transitions[].evidence_ids",
    ".related[].context.score.at_anchor.evidence_ids",
    ".related[].context.special.last_ready_before_anchor.observation_id",
    ".related[].context.special.last_visible_before_anchor.observation_id",
    ".related[].context.special.nearest_before_anchor.observation_id",
    ".related[].context.special.observations[].observation_id",
    ".related[].context.special.ready_onsets[].observation_id",
    ".related[].context.zone_control.at_anchor.evidence_ids",
    ".related[].context.zone_control.trajectory[].evidence_ids",
)


def synthetic_video_identity(seed: str = "synthetic") -> str:
    """A 64-hex stand-in for ``sha256(size + bytes)`` of an upload."""
    return hashlib.sha256(f"video-identity:{seed}".encode()).hexdigest()


def _policy() -> GameLanguagePolicy:
    return GameLanguagePolicy(
        intro_roi=(0.0, 0.0, 0.1, 0.1),
        intro_threshold=0.5,
        intro_language_margin=0.1,
        intro_deadline_seconds=10.0,
        callout_roi=(0.0, 0.0, 0.1, 0.1),
        callout_cues=["synthetic"],
        callout_threshold=0.5,
        callout_language_margin=0.1,
        max_fallback_observations=1,
        max_fallback_live_seconds=1.0,
        max_seconds_before_live=1.0,
        sample_fps=1.0,
        languages=["en", "ja"],
    )


def build_manifest(video_identity: str, death_times: list[float]) -> VisionManifest:
    """A schema-valid manifest whose IDs derive from ``video_identity``."""
    analysis_id = compute_analysis_id(video_identity, "", "c" * 64)
    frames, events, snapshots = [], [], []
    for index, when in enumerate([1.0, *death_times]):
        splat = SplatReading(detected=True)
        respawn = RespawnReading()
        detections = [
            DetectorResult(
                id=make_result_id(analysis_id, index, when, name, "1.0.0", reading),
                detector_name=name,
                detector_version="1.0.0",
                confidence=0.9,
                reading=reading,
            )
            for name, reading in (("splat", splat), ("respawn", respawn))
        ]
        frame_id = make_frame_id(analysis_id, index, when)
        source = SourceFrameReference(
            frame_id=frame_id,
            source_frame_index=index,
            timestamp=when,
            frame_path=f"frames/{index:06d}.jpg",
        )
        frames.append(
            VisionFrameResult(
                frame_id=frame_id,
                timestamp=when,
                source="cadence",
                frame_path=f"frames/{index:06d}.jpg",
                detections=detections,
            )
        )
        evidence = [item.id for item in detections]
        snapshots.append(
            GameStateSnapshot(timestamp=when, evidence_ids=evidence, source_frame=source)
        )
        if when in death_times:
            events.append(
                GameEvent(
                    start_time=when,
                    event_type=GameEventType.DEATH,
                    confidence=0.9,
                    evidence_ids=evidence,
                    source_frames=[source],
                )
            )
    return VisionManifest(
        analysis=AnalysisIdentity(
            analysis_id=analysis_id,
            package_version="0.0.0",
            git_sha="abc123",
            vision_config_sha256="c" * 64,
            video_identity=video_identity,
            language="en",
        ),
        video_identity=video_identity,
        extraction_manifest_path=None,
        frame_results=frames,
        state_snapshots=snapshots,
        game_events=events,
        game_language=GameLanguageResult(
            language="en",
            source="intro_mode",
            termination="intro_accepted",
            policy=_policy(),
        ),
    )


def _set(document: dict, path: str, value: Any) -> None:
    """Create ``path`` (``.a.b[].c``) inside ``document`` and set its leaf."""
    node: Any = document
    parts = path.lstrip(".").split(".")
    for position, part in enumerate(parts):
        last = position == len(parts) - 1
        name, is_list = (part[:-2], True) if part.endswith("[]") else (part, False)
        if is_list:
            items = node.setdefault(name, [{}] if not last else [])
            if last:
                items.append(value)
                return
            node = items[0]
        elif last:
            node[name] = value
        else:
            node = node.setdefault(name, {})


def coach_input_document(analysis_id: str, result_id: str) -> dict:
    """A ``coach_input.json`` shape carrying every inventory identifier path."""
    document: dict = {"evidence_version": 3}
    for path in _COACH_INPUT_ID_PATHS:
        if path.endswith("observation_id"):
            _set(document, path, f"{analysis_id}:special:000001")
        else:
            _set(document, path, [result_id])
    _set(document, ".score_facts.source_path", "primary_context.score")
    _set(document, ".zone_control_facts.source_path", "primary_context.zone_control")
    _set(document, ".evidence_limits[].code", "roster_not_visible")
    return document


def llm_view_document() -> dict:
    """A ``llm_view.json`` shape with JSON paths and evidence-limit codes."""
    return {
        "evidence_limits": [{"code": "map_unknown", "statement": "Synthetic limit."}],
        "timeline": {"source_path": "primary_context.timeline", "death_time": 12.0},
    }


def write_report_fixture(
    analysis: Path,
    *,
    video_identity: str | None = None,
    death_times: tuple[float, ...] = (0.5,),
    coaching_valid: bool = True,
) -> dict[str, str]:
    """Write a complete, coached report folder. Returns the two fingerprints."""
    video_identity = video_identity or synthetic_video_identity(analysis.name)
    manifest = build_manifest(video_identity, list(death_times))
    analysis.mkdir(parents=True, exist_ok=True)
    (analysis / "vision_manifest.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    analysis_id = manifest.analysis.analysis_id
    result_id = manifest.frame_results[0].detections[0].id
    (analysis / "match_identity.json").write_text(
        json.dumps({"stage_id": "synthetic_stage", "battle_mode_id": "splat_zones"}),
        encoding="utf-8",
    )
    entries = [
        index_entry(f"death_episode_{when}", video_time=when, rank=rank)
        for rank, when in enumerate(death_times, start=1)
    ]
    _write_inputs(analysis, entries, analysis_id, result_id)
    for entry in entries:
        _write_prototype(analysis, entry["safe_id"])
    return {"video_identity": video_identity, "analysis_id": analysis_id}


def _write_inputs(
    analysis: Path, entries: list[dict], analysis_id: str, result_id: str
) -> None:
    inputs = analysis / "coach_inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    write_coaching_index(
        inputs,
        [CoachingIndexEntry.model_validate(entry) for entry in entries],
        analysis_id=analysis_id,
    )
    (inputs / "coach_inputs_meta.json").write_text(
        json.dumps({"analysis_dir": "/srv/excluded/never-published"}), encoding="utf-8"
    )
    for entry in entries:
        names = unit_file_names(entry["safe_id"])
        coaching = {
            "factors": [{"active": True, "statement_player": STATEMENT}],
            "supporting_evidence": [{"path": "primary_context.death"}],
            "roster_before_death": {"source_path": "primary_context.players"},
        }
        (inputs / names.coaching).write_text(json.dumps(coaching), encoding="utf-8")
        (inputs / names.coach_input).write_text(
            json.dumps(coach_input_document(analysis_id, result_id)), encoding="utf-8"
        )
        (inputs / names.llm_view).write_text(
            json.dumps(llm_view_document()), encoding="utf-8"
        )


def _write_prototype(analysis: Path, safe_id: str) -> None:
    prototype = analysis / "coach_prototype"
    write_prototype_unit(
        prototype,
        safe_id,
        {"model": "Synthetic assessment prose."},
        primary_model="model",
    )
    names = unit_file_names(safe_id)
    (prototype / names.llm_view).write_text(
        json.dumps(llm_view_document()), encoding="utf-8"
    )
    flags = next(prototype.glob(f"{safe_id}.*.flags.json"))
    payload = json.loads(flags.read_text(encoding="utf-8"))
    payload["evidence_path_resolution"] = [
        {"path": "death.time", "source_path": "primary_context.death"}
    ]
    flags.write_text(json.dumps(payload), encoding="utf-8")
    (prototype / "llm_metrics.jsonl").write_text(
        '{"error": "excluded"}\n', encoding="utf-8"
    )
    (prototype / f"{safe_id}.model.raw.txt").write_text("excluded", encoding="utf-8")


def lavfi_clip(path: Path, *, seconds: float = 1.0) -> None:
    """A tiny clean H.264/AAC clip from lavfi (no metadata)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=s=160x90:d={seconds}:r=10",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-map_metadata",
            "-1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-movflags",
            "+faststart",
            str(path),
        ],
        check=True,
    )


def lavfi_jpeg(path: Path) -> None:
    """A tiny JPEG still from lavfi (ffmpeg writes only its encoder comment)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=160x90:d=1:r=1",
            "-frames:v",
            "1",
            str(path),
        ],
        check=True,
    )


def add_media(analysis: Path, *, skip_clips: tuple[str, ...] = ()) -> None:
    """Clips and stills for every indexed moment (except ``skip_clips``)."""
    index = json.loads((analysis / "coach_inputs" / "coaching_index.json").read_text())
    for entry in index["entries"]:
        safe_id = entry["safe_id"]
        lavfi_jpeg(analysis / "public_frames" / f"{safe_id}.jpg")
        if safe_id not in skip_clips:
            lavfi_clip(analysis / "public_clips" / f"{safe_id}.mp4")


def folder_digest(path: Path) -> str:
    """SHA-256 over every file's relative path and bytes (local-unchanged checks)."""
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode())
        digest.update(hashlib.sha256(item.read_bytes()).digest())
    return digest.hexdigest()


def fixture_command(
    death_times: tuple[float, ...] = (0.5,),
) -> Callable[[list[str]], int]:
    """A fake public-site ``run_command`` whose ``analyze`` writes the fixture."""

    def run(argv: list[str]) -> int:
        if "analyze" in argv:
            out = Path(argv[argv.index("--out") + 1])
            write_report_fixture(out, death_times=death_times)
        return 0

    return run
