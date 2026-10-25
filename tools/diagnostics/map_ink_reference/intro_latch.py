"""Amendment 4 derivation ``amendment4-intro-latch-v1`` (blind-input manifests).

Replays the stored ``match_intro`` readings of a vision manifest through the
production ``MatchIdentityTracker`` (called read-only) with the pinned
``intro_deadline_seconds``. Production code is the authority for the latch
semantics; this module only orders the stored readings, calls the tracker and
records when each identifier first became non-null. No frame is decoded.

The production code identity (three pinned files, imported from exactly
those paths and committed unmodified) and the pinned configuration are
verified before every derivation. The comparison tool re-executes the
derivation through :func:`reproduce_corpus_blind_input` before any
comparison; inability to re-execute or any difference is a STOP.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pydantic

import splatoon3_ai_coach.config.models as config_models
import splatoon3_ai_coach.vision.match_intro as match_intro
import splatoon3_ai_coach.vision.models as vision_models
from map_ink_reference.constants import (
    BLIND_INPUT_SCHEMA,
    CORRECTNESS_APPLICABLE,
    INTRO_LATCH_RULE,
    SCOPE_CORPUS,
    SCOPE_PILOT,
)
from map_ink_reference.errors import (
    MalformedInput,
    PinMismatch,
    ReferenceProcedureError,
    ReproductionFailure,
    SealViolation,
)
from map_ink_reference.io_utils import read_json, sha256_file, write_json
from map_ink_reference.pilot import (
    PilotSource,
    check_declared_source,
    corpus_exclusions,
    load_pilot_source_manifest,
)
from map_ink_reference.pins import (
    CONFIG_PIN,
    INTRO_LATCH_CODE_PINS,
    MEDIA_PINS,
    require_pinned_geometry,
    verify_pin,
)
from map_ink_reference.seal import head_commit, require_in_order, require_sealed
from map_ink_reference.stage_identity import (
    BlindInput,
    load_corpus_blind_input,
    step2a_paths,
    validate_blind_input,
)
from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.vision.match_intro import MatchIdentityTracker
from splatoon3_ai_coach.vision.models import MatchIntroReading

# Sealed literal (Amendment 4 step 3): the pinned configuration's value.
SEALED_INTRO_DEADLINE_SECONDS = 90.0
INTRO_KIND = "match_intro"


def production_modules() -> dict[str, ModuleType]:
    """Pinned repo path → the production module actually imported for it."""
    return {
        "src/splatoon3_ai_coach/vision/match_intro.py": match_intro,
        "src/splatoon3_ai_coach/vision/models.py": vision_models,
        "src/splatoon3_ai_coach/config/models.py": config_models,
    }


def verify_code_identity(
    repo: Path, modules: Mapping[str, ModuleType] | None = None
) -> dict[str, str]:
    """STOP unless every pinned file matches, is committed and is what was imported."""
    modules = production_modules() if modules is None else modules
    if set(modules) != set(INTRO_LATCH_CODE_PINS):
        raise PinMismatch("production module map does not cover the pinned code files")
    for rel, digest in INTRO_LATCH_CODE_PINS.items():
        path = repo / rel
        verify_pin(path, digest)
        require_sealed(repo, path)
        loaded = getattr(modules[rel], "__file__", None)
        if loaded is None or Path(loaded).resolve() != path.resolve():
            raise PinMismatch(
                f"imported module for {rel} is not the pinned file: {loaded}"
            )
    if MatchIdentityTracker.__module__ != match_intro.__name__:
        raise PinMismatch("MatchIdentityTracker is not defined by the pinned module")
    if MatchIntroReading.__module__ != vision_models.__name__:
        raise PinMismatch("MatchIntroReading is not defined by the pinned module")
    return dict(INTRO_LATCH_CODE_PINS)


def intro_deadline_seconds(repo: Path) -> float:
    """``vision.match_intro.intro_deadline_seconds`` of the pinned configuration."""
    path = repo / CONFIG_PIN[0]
    verify_pin(path, CONFIG_PIN[1])
    value = float(load_config(path).vision.match_intro.intro_deadline_seconds)
    if value != SEALED_INTRO_DEADLINE_SECONDS:
        raise PinMismatch(f"intro_deadline_seconds is {value}, sealed value is 90.0")
    return value


def environment() -> dict[str, str]:
    """Recorded interpreter and pydantic versions."""
    return {
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "pydantic_version": str(pydantic.VERSION),
    }


def stored_intro_readings(
    manifest: Mapping[str, Any],
) -> list[tuple[float, MatchIntroReading]]:
    """Stored ``match_intro`` readings by ascending frame timestamp (stable)."""
    frames = manifest.get("frame_results")
    if not isinstance(frames, list):
        raise MalformedInput("vision manifest has no frame_results list")
    found: list[tuple[float, MatchIntroReading]] = []
    for frame in frames:
        if not isinstance(frame, dict) or "timestamp" not in frame:
            raise MalformedInput("frame result without timestamp")
        timestamp = float(frame["timestamp"])
        for detection in frame.get("detections") or []:
            reading = detection.get("reading") if isinstance(detection, dict) else None
            if not isinstance(reading, dict) or reading.get("kind") != INTRO_KIND:
                continue
            try:
                parsed = MatchIntroReading.model_validate(reading)
            except pydantic.ValidationError as exc:
                raise MalformedInput(
                    f"invalid stored match_intro reading: {exc}"
                ) from exc
            found.append((timestamp, parsed))
    found.sort(key=lambda item: item[0])
    return found


def derive_latch(manifest: Mapping[str, Any], deadline: float) -> dict[str, Any]:
    """Replay stored readings through the production tracker (rule step 2-4)."""
    readings = stored_intro_readings(manifest)
    tracker = MatchIdentityTracker(intro_deadline_seconds=deadline)
    stage_at: float | None = None
    mode_at: float | None = None
    for timestamp, reading in readings:
        tracker.update(reading, video_time=timestamp)
        identity = tracker.identity
        if stage_at is None and identity.stage_id is not None:
            stage_at = timestamp
        if mode_at is None and identity.battle_mode_id is not None:
            mode_at = timestamp
    identity = tracker.identity
    return {
        "stage_id": identity.stage_id,
        "battle_mode_id": identity.battle_mode_id,
        "stage_latched_at": stage_at,
        "battle_mode_latched_at": mode_at,
        "intro_reading_count": len(readings),
    }


def derive_entry(
    repo: Path,
    *,
    recording: str,
    manifest_rel: str,
    video_sha256: str,
    deadline: float,
    pinned_manifest_sha256: str | None,
) -> dict[str, Any]:
    """One per-recording entry from its vision manifest (hash verified if pinned)."""
    path = repo / manifest_rel
    if pinned_manifest_sha256 is not None:
        digest = verify_pin(path, pinned_manifest_sha256)
    elif path.is_file():
        digest = sha256_file(path)
    else:
        raise MalformedInput(f"vision manifest is missing: {manifest_rel}")
    manifest = read_json(path)
    if not isinstance(manifest, dict) or not isinstance(
        manifest.get("video_identity"), str
    ):
        raise MalformedInput(f"{manifest_rel} has no video_identity")
    return {
        "recording": recording,
        "video_identity": manifest["video_identity"],
        "video_sha256": video_sha256,
        "vision_manifest_path": manifest_rel,
        "vision_manifest_sha256": digest,
        **derive_latch(manifest, deadline),
    }


def corpus_entries(repo: Path, deadline: float) -> list[dict[str, Any]]:
    """Entries for the five correctness-applicable recordings (STOP conditions)."""
    out = []
    for rid in CORRECTNESS_APPLICABLE:
        pins = MEDIA_PINS[rid]
        entry = derive_entry(
            repo,
            recording=rid,
            manifest_rel=pins.vision_manifest_path,
            video_sha256=pins.video_sha256,
            deadline=deadline,
            pinned_manifest_sha256=pins.vision_manifest_sha256,
        )
        if entry["stage_id"] is None:
            raise MalformedInput(
                f"{rid}: correctness-applicable recording latches no stage_id"
            )
        require_pinned_geometry(repo, entry["stage_id"], entry["battle_mode_id"])
        out.append(entry)
    return out


def pilot_entries(
    repo: Path, sources: Sequence[PilotSource], deadline: float
) -> list[dict[str, Any]]:
    """Entries for every declared pilot source (a latched stage needs pinned geometry).

    Every declared entry passes the metadata-only exclusion checks before any
    of their manifests is replayed.
    """
    exclusions = corpus_exclusions(repo)
    for source in sources:
        check_declared_source(repo, source, exclusions)
    out = []
    for source in sources:
        entry = derive_entry(
            repo,
            recording=source.recording,
            manifest_rel=source.vision_manifest_path,
            video_sha256=source.video_sha256,
            deadline=deadline,
            pinned_manifest_sha256=None,
        )
        if entry["video_identity"] != source.video_identity:
            raise PinMismatch(f"{source.recording}: manifest video_identity differs")
        if entry["stage_id"] is not None:
            require_pinned_geometry(repo, entry["stage_id"], entry["battle_mode_id"])
        out.append(entry)
    return out


def build_payload(
    repo: Path,
    scope: str,
    entries: list[dict[str, Any]],
    *,
    tool_commit: str,
    amendment4_commit: str,
    pilot_source_sha256: str | None = None,
) -> dict[str, Any]:
    """Assemble an ``amendment3-blind-input/v1`` payload (nothing else)."""
    payload: dict[str, Any] = {
        "schema": BLIND_INPUT_SCHEMA,
        "scope": scope,
        "amendment4_commit": amendment4_commit,
        "tool_commit": tool_commit,
        "rule": INTRO_LATCH_RULE,
        "code_commit": head_commit(repo),
        "code_sha256": verify_code_identity(repo),
        "config_sha256": CONFIG_PIN[1],
        **environment(),
        "recordings": entries,
    }
    if scope == SCOPE_PILOT:
        payload["pilot_source_manifest_sha256"] = pilot_source_sha256
    validate_blind_input(payload, scope)
    return payload


def write_blind_input_manifest(
    repo: Path, records: Path, scope: str, *, tool_commit: str, amendment4_commit: str
) -> Path:
    """Step 2a generation (pilot after the sealed source; corpus after the pilot)."""
    source_path, pilot_path, corpus_path = step2a_paths(records)
    out = pilot_path if scope == SCOPE_PILOT else corpus_path
    if out.exists():
        raise SealViolation(f"{out.name} already exists; it is never regenerated")
    verify_code_identity(repo)
    deadline = intro_deadline_seconds(repo)
    if scope == SCOPE_PILOT:
        require_sealed(repo, source_path)
        sources = load_pilot_source_manifest(source_path)
        entries = pilot_entries(repo, sources, deadline)
        source_sha = sha256_file(source_path)
    elif scope == SCOPE_CORPUS:
        require_in_order(repo, (source_path, pilot_path))
        entries, source_sha = corpus_entries(repo, deadline), None
    else:
        raise MalformedInput(f"unknown scope {scope!r}")
    payload = build_payload(
        repo,
        scope,
        entries,
        tool_commit=tool_commit,
        amendment4_commit=amendment4_commit,
        pilot_source_sha256=source_sha,
    )
    write_json(out, payload)
    return out


def reproduce_corpus_blind_input(repo: Path, records: Path) -> tuple[BlindInput, dict]:
    """Pre-comparison reproduction check; any failure or difference is a STOP."""
    blind = load_corpus_blind_input(repo, records)
    sealed = read_json(step2a_paths(records)[2])
    try:
        verify_code_identity(repo)
        deadline = intro_deadline_seconds(repo)
        derived = {e["recording"]: e for e in corpus_entries(repo, deadline)}
    except PinMismatch:
        raise
    except (ReferenceProcedureError, OSError, ValueError) as exc:
        raise ReproductionFailure(f"cannot re-execute {INTRO_LATCH_RULE}: {exc}") from exc
    for rid, entry in blind.entries.items():
        if derived.get(rid) != entry:
            raise ReproductionFailure(
                f"{rid}: re-executed derivation differs from the sealed blind-input "
                f"manifest (sealed {entry}, derived {derived.get(rid)})"
            )
    current = environment()
    record = {
        "rule": INTRO_LATCH_RULE,
        "blind_input_manifest_sha256": blind.manifest_sha256,
        "reproduced": True,
        "sealed_environment": {k: sealed[k] for k in current},
        "current_environment": current,
    }
    return blind, record
