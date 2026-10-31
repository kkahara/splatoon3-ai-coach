"""Bounded observed-open basket runs; no gameplay events or coaching facts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from splatoon3_ai_coach.analysis.match_bounds import match_end_time
from splatoon3_ai_coach.vision.canonical import canonical_json, sha256_hex
from splatoon3_ai_coach.vision.clam_basket_fusion import basket_schedule_status
from splatoon3_ai_coach.vision.models import (
    ClamBasketStateReading,
    GameStateSnapshot,
    VisionFrameResult,
    VisionManifest,
)
from splatoon3_ai_coach.vision.score_fusion import resolve_battle_mode

ARTIFACT_FILENAME = "basket_open_runs.json"
Schedule = Literal["scheduled", "not_scheduled", "schedule_unknown"]


class BasketSample(BaseModel):
    """An observed open or closed sample with frame and reading provenance."""

    video_time: float = Field(ge=0)
    frame_id: str
    evidence_ids: list[str] = Field(min_length=1)


class BasketBreak(BaseModel):
    """A frame where continuity was interrupted, never a closure assertion."""

    video_time: float = Field(ge=0)
    frame_id: str
    reason: Literal[
        "unknown_observation",
        "missing_expected_observation",
        "schedule_unknown",
        "missing_snapshot",
    ]


class BasketOpenRun(BaseModel):
    """A sequence of observed opens; continuity between samples is not inferred."""

    first_observed_open: BasketSample
    last_observed_open: BasketSample
    preceding_closed: BasketSample | None = None
    following_closed: BasketSample | None = None
    left_censor_reason: Literal["no_prior_closed", "unknown_break"] | None = None
    right_censor_reason: Literal["unknown_break", "match_end", "video_end"] | None = None
    ending_break: BasketBreak | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> BasketOpenRun:
        """Reject inconsistent bounds and censoring in persisted artifacts."""
        if self.first_observed_open.video_time > self.last_observed_open.video_time:
            raise ValueError("open sample times are out of order")
        if (self.preceding_closed is None) != (self.left_censor_reason is not None):
            raise ValueError("opening must be bounded or left-censored")
        if (self.following_closed is None) != (self.right_censor_reason is not None):
            raise ValueError("closing must be bounded or right-censored")
        if (
            self.preceding_closed
            and self.preceding_closed.video_time >= self.first_observed_open.video_time
        ):
            raise ValueError("preceding closed sample must precede first open")
        if (
            self.following_closed
            and self.following_closed.video_time <= self.last_observed_open.video_time
        ):
            raise ValueError("following closed sample must follow last open")
        if self.ending_break and self.right_censor_reason != "unknown_break":
            raise ValueError("break requires unknown-break right censoring")
        return self


class BasketOpenRunsArtifact(BaseModel):
    """Versioned, explicitly available or unavailable derived analysis artifact."""

    schema_version: Literal[1] = 1
    schedule_contract: Literal["frame_flag_or_legacy_result_v1"] = (
        "frame_flag_or_legacy_result_v1"
    )
    analysis_id: str
    source_manifest_sha256: str
    battle_mode_id: str | None
    availability: Literal["available", "partial", "unavailable"]
    limitations: list[str] = Field(default_factory=list)
    runs: list[BasketOpenRun] = Field(default_factory=list)


@dataclass(frozen=True)
class _FrameSnapshot:
    frame: VisionFrameResult
    snapshot: GameStateSnapshot | None


def source_manifest_sha256(manifest: VisionManifest) -> str:
    """Fingerprint the complete persisted source, including refused snapshots."""
    return sha256_hex(canonical_json(manifest.model_dump(mode="json")))


def _join(manifest: VisionManifest, end: float | None) -> list[_FrameSnapshot] | None:
    """Join frames and snapshots by identity, refusing ambiguous or missing links."""
    frames = [f for f in manifest.frame_results if end is None or f.timestamp < end]
    snapshots = [s for s in manifest.state_snapshots if end is None or s.timestamp < end]
    by_id = {f.frame_id: f for f in frames}
    if len(by_id) != len(frames) or len({f.timestamp for f in frames}) != len(frames):
        return None
    by_frame: dict[str, GameStateSnapshot] = {}
    for snap in snapshots:
        source = snap.source_frame
        if source is None or source.frame_id not in by_id or source.frame_id in by_frame:
            return None
        if (
            snap.timestamp != by_id[source.frame_id].timestamp
            or source.timestamp != snap.timestamp
        ):
            return None
        by_frame[source.frame_id] = snap
    return [
        _FrameSnapshot(f, by_frame.get(f.frame_id))
        for f in sorted(frames, key=lambda x: (x.timestamp, x.frame_id))
    ]


def _observation(item: _FrameSnapshot) -> tuple[str, BasketSample | BasketBreak | None]:
    """Convert one frame/snapshot pair into skip, usable sample, or break."""
    frame, snap = item.frame, item.snapshot
    if snap is not None and snap.match_phase != "in_match":
        return "outside", None
    status = basket_schedule_status(frame)
    if status == "not_scheduled":
        return "skip", None
    reason = None
    if status == "schedule_unknown":
        reason = "schedule_unknown"
    elif snap is None:
        reason = "missing_snapshot"
    elif not any(d.detector_name == "clam_basket_state" for d in frame.detections):
        reason = "missing_expected_observation"
    elif (
        snap.map_overlay_present is True
        or snap.basket_state == "unknown"
        or not _observed_evidence_matches(frame, snap)
    ):
        reason = "unknown_observation"
    if reason is not None:
        return "break", BasketBreak(
            video_time=frame.timestamp, frame_id=frame.frame_id, reason=reason
        )
    assert snap is not None
    return snap.basket_state, BasketSample(
        video_time=frame.timestamp,
        frame_id=frame.frame_id,
        evidence_ids=list(snap.basket_state_evidence_ids),
    )


def _observed_evidence_matches(frame: VisionFrameResult, snap: GameStateSnapshot) -> bool:
    """Reject held, stale, missing, low-confidence, or inconsistent evidence."""
    ids = set(snap.basket_state_evidence_ids)
    return bool(ids) and all(
        any(
            result.id == evidence_id
            and result.detector_name == "clam_basket_state"
            and result.confidence >= 0.5
            and isinstance(result.reading, ClamBasketStateReading)
            and result.reading.visual_state == snap.basket_state
            for result in frame.detections
        )
        for evidence_id in ids
    )


def build_basket_open_runs(manifest: VisionManifest) -> BasketOpenRunsArtifact:
    """Build observed-open runs without altering the input or other artifacts."""
    mode = resolve_battle_mode(manifest.frame_results)
    base = dict(
        analysis_id=manifest.analysis.analysis_id,
        source_manifest_sha256=source_manifest_sha256(manifest),
        battle_mode_id=mode,
    )
    if mode != "clam_blitz":
        return BasketOpenRunsArtifact(
            **base,
            availability="unavailable",
            limitations=["mode_not_resolved_clam_blitz"],
        )
    if "clam_basket_state" not in manifest.analysis.detector_versions:
        return BasketOpenRunsArtifact(
            **base,
            availability="unavailable",
            limitations=["basket_detector_not_enabled"],
        )
    end = match_end_time(manifest.game_events)
    joined = _join(manifest, end)
    if joined is None:
        return BasketOpenRunsArtifact(
            **base,
            availability="unavailable",
            limitations=["ambiguous_frame_snapshot_link"],
        )
    if not any(
        basket_schedule_status(item.frame) != "not_scheduled"
        and (item.snapshot is None or item.snapshot.match_phase == "in_match")
        for item in joined
    ):
        return BasketOpenRunsArtifact(
            **base,
            availability="unavailable",
            limitations=["no_basket_observations_scheduled"],
        )
    runs, has_gaps = _build_runs(joined, has_match_end=end is not None)
    if has_gaps and not runs:
        return BasketOpenRunsArtifact(
            **base,
            availability="unavailable",
            limitations=["observation_or_schedule_gaps_prevent_run_analysis"],
        )
    return BasketOpenRunsArtifact(
        **base,
        availability="partial" if has_gaps else "available",
        limitations=["observation_or_schedule_gaps"] if has_gaps else [],
        runs=runs,
    )


def _build_runs(
    items: list[_FrameSnapshot], *, has_match_end: bool
) -> tuple[list[BasketOpenRun], bool]:
    """Apply unknown-breaks-continuity to ordered frame observations."""
    runs: list[BasketOpenRun] = []
    current: BasketOpenRun | None = None
    previous_closed: BasketSample | None = None
    after_break = False
    partial = False
    for item in items:
        state, evidence = _observation(item)
        if state == "skip":
            continue
        if state == "outside":
            if current is None:
                previous_closed = None
                continue
            state = "break"
            evidence = BasketBreak(
                video_time=item.frame.timestamp,
                frame_id=item.frame.frame_id,
                reason="unknown_observation",
            )
        if state == "break":
            assert isinstance(evidence, BasketBreak)
            partial = True
            if current is not None:
                current.right_censor_reason = "unknown_break"
                current.ending_break = evidence
                runs.append(current)
                current = None
            previous_closed, after_break = None, True
        elif state == "closed":
            assert isinstance(evidence, BasketSample)
            if current is not None:
                current.following_closed = evidence
                current.right_censor_reason = None
                runs.append(current)
                current = None
            previous_closed, after_break = evidence, False
        elif state == "open":
            assert isinstance(evidence, BasketSample)
            if current is None:
                current = BasketOpenRun(
                    first_observed_open=evidence,
                    last_observed_open=evidence,
                    preceding_closed=previous_closed,
                    right_censor_reason="video_end",
                    left_censor_reason=None
                    if previous_closed
                    else ("unknown_break" if after_break else "no_prior_closed"),
                )
            else:
                current.last_observed_open = evidence
            previous_closed, after_break = None, False
    if current is not None:
        current.right_censor_reason = "match_end" if has_match_end else "video_end"
        runs.append(current)
    return runs, partial


def write_basket_open_runs(manifest: VisionManifest, output_dir: Path) -> Path:
    """Write a derived artifact; rerun after refusion or source changes."""
    artifact = build_basket_open_runs(manifest)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / ARTIFACT_FILENAME
    path.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def basket_open_runs_are_current(
    artifact: BasketOpenRunsArtifact, manifest: VisionManifest
) -> bool:
    """Check source identity after refusion before a future consumer uses output."""
    return artifact.source_manifest_sha256 == source_manifest_sha256(manifest)
