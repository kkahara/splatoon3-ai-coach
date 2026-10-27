"""File-level comparison (unblinded): runs only on sealed labels, keys and gate.

Reads the pinned calibrations, derives the A/B → team mapping from the
sealed team-identity answers, applies the production classifier and writes
``results_stage{s}.json``. Before any comparison it re-executes the sealed
blind-input derivation (Amendment 4; failure is a STOP) and requires the
sealed criterion 4a record (step 5a). It also reports, descriptively only,
whether the blind-input stage identity agrees with the pinned v3 and A1
``match_identity.json``. When Stage 1 has INCONCLUSIVE recordings it writes
``stage2_trigger.json`` (step 6a) with only the results digest and their ids.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

from map_ink_reference.comparison import (
    FrameOutcome,
    LabelledPoint,
    calibrated_classifier,
    class_map,
    frame_outcome,
    identity_holds,
    q_all,
    stage1_verdict,
    stage2_verdict,
)
from map_ink_reference.constants import (
    CORRECTNESS_APPLICABLE,
    Q_ALL_UNDEFINED,
    STAGE_1,
    STAGE_2,
)
from map_ink_reference.corpus_capacity import stage2_capacity
from map_ink_reference.domain import point_domain
from map_ink_reference.errors import MalformedInput, ProcedureOrderError, SealViolation
from map_ink_reference.intro_latch import reproduce_corpus_blind_input
from map_ink_reference.io_utils import read_json, sha256_file, write_json
from map_ink_reference.mapping import consistent_own_swatch, team_answers
from map_ink_reference.pins import CONFIG_PIN, verify_geometry_pins, verify_pin
from map_ink_reference.pipeline_blind import (
    _repo_of,
    extraction_path,
    gate_path,
    plan_path,
    points_path,
)
from map_ink_reference.plan import eligible_frame_ids, stage_frame_ids
from map_ink_reference.points import load_verified_png
from map_ink_reference.records import (
    SWATCHES,
    TOKEN_KEY,
    labels_name,
    queue_key_name,
    queue_name,
    team_identity_name,
)
from map_ink_reference.seal import require_sealed, require_strictly_after
from map_ink_reference.stage_identity import BlindInput, StageIdentity
from map_ink_reference.trigger import read_sealed_trigger, trigger_path, trigger_payload
from map_ink_reference.unblinded_pins import (
    CALIBRATION_PINS,
    MAP_INK_CODE_PIN,
    identity_pins,
)
from map_ink_reference.verdicts import INCONCLUSIVE, NOT_EVALUABLE
from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import MapInkAnalyzerConfig

CRITERION_4A = "criterion_4a.json"


def results_path(records: Path, stage: int) -> Path:
    """Committed comparison results of a stage."""
    return records / f"results_stage{stage}.json"


def criterion_4a_path(records: Path) -> Path:
    """Committed criterion 4a record (step 5a)."""
    return records / CRITERION_4A


def _stage_record_files(records: Path, stage: int) -> list[Path]:
    names = (
        labels_name(stage),
        team_identity_name(stage),
        queue_name(stage),
        queue_key_name(stage),
        f"duplicates_stage{stage}.json",
    )
    return [points_path(records, stage), *(records / n for n in names)]


def require_comparison_seals(records: Path, stage: int) -> None:
    """Every input sealed, in the preregistered order, and the gate passed."""
    repo = _repo_of(records)
    for s in range(STAGE_1, stage + 1):
        for path in _stage_record_files(records, s):
            require_sealed(repo, path)
        require_sealed(repo, gate_path(records, s))
    require_sealed(repo, records / SWATCHES)
    require_strictly_after(repo, records / labels_name(STAGE_1), records / TOKEN_KEY)
    require_strictly_after(repo, gate_path(records, STAGE_1), criterion_4a_path(records))
    for rid in read_json(points_path(records, STAGE_1))["recordings"]:
        require_sealed(repo, plan_path(records, rid))
        require_sealed(repo, extraction_path(records, rid))
    if stage == STAGE_2:
        require_strictly_after(
            repo, results_path(records, STAGE_1), points_path(records, STAGE_2)
        )
    gate = read_json(gate_path(records, stage))
    if gate.get("passed") is not True:
        raise ProcedureOrderError(f"Stage {stage} gate did not pass; no comparison")


def load_map_ink_config(repo: Path) -> MapInkAnalyzerConfig:
    """Pinned classifier configuration (and pinned classifier code)."""
    verify_pin(repo / MAP_INK_CODE_PIN[0], MAP_INK_CODE_PIN[1])
    config_path = repo / CONFIG_PIN[0]
    verify_pin(config_path, CONFIG_PIN[1])
    return load_config(config_path).vision.map_ink


def load_identity_sidecar(repo: Path, rid: str, which: str) -> tuple[dict[str, Any], str]:
    """Pinned ``match_identity.json`` payload and its digest (``v3`` / ``a1``)."""
    pins = identity_pins(rid)
    rel, digest = {
        "v3": (pins.v3_identity_path, pins.v3_identity_sha256),
        "a1": (pins.a1_identity_path, pins.a1_identity_sha256),
    }[which]
    verify_pin(repo / rel, digest)
    payload = read_json(repo / rel)
    if not isinstance(payload, dict):
        raise MalformedInput(f"{rel} is not an object")
    return payload, digest


def load_calibration(repo: Path, rid: str, which: str) -> dict[str, Any]:
    """Pinned ``team_color_calibration`` payload (``which`` = ``v3`` / ``a1``)."""
    if rid not in CALIBRATION_PINS:
        raise MalformedInput(f"{rid} is not correctness-applicable")
    payload = load_identity_sidecar(repo, rid, which)[0].get("team_color_calibration")
    if not isinstance(payload, dict):
        raise MalformedInput(f"{rid} {which} has no calibration")
    return payload


def verified_stage_identities(
    repo: Path, records: Path
) -> tuple[dict[str, StageIdentity], dict[str, Any], BlindInput]:
    """Reproduce the sealed derivation and bind it to the Stage 1 point manifest."""
    blind, reproduction = reproduce_corpus_blind_input(repo, records)
    manifest = read_json(points_path(records, STAGE_1))
    if manifest.get("blind_input_manifest_sha256") != blind.manifest_sha256:
        raise SealViolation("point manifest was not built from the sealed blind-input")
    recorded = {
        rid: StageIdentity(v["stage_id"], v["battle_mode_id"])
        for rid, v in manifest["stage_identities"].items()
    }
    if any(recorded[rid] != blind.identities[rid] for rid in recorded):
        raise SealViolation("point manifest stage identities differ from the blind input")
    return blind.identities, reproduction, blind


def stage_identity_crosscheck(
    repo: Path, blind: BlindInput, recordings: list[str]
) -> list[dict[str, Any]]:
    """Descriptive only: blind-input identity vs pinned v3 and A1 sidecars.

    A disagreement is reported (with both values and source hashes) and logged
    prominently. It never alters the manifest, domain, labels or any verdict
    and is not a stop condition; pin or integrity failures still are.
    """
    rows = []
    for rid in recordings:
        ident = blind.identities[rid]
        row: dict[str, Any] = {
            "recording_id": rid,
            "blind_input": {
                "stage_id": ident.stage_id,
                "battle_mode_id": ident.battle_mode_id,
                "manifest_sha256": blind.manifest_sha256,
            },
        }
        for which in ("v3", "a1"):
            payload, digest = load_identity_sidecar(repo, rid, which)
            values = {k: payload.get(k) for k in ("stage_id", "battle_mode_id")}
            agrees = values == {
                "stage_id": ident.stage_id,
                "battle_mode_id": ident.battle_mode_id,
            }
            row[which] = {**values, "sha256": digest}
            row[f"{which}_agrees"] = agrees
            if not agrees:
                logger.warning(
                    "POST-SEAL STAGE-IDENTITY DISCREPANCY {} ({}): blind-input {} / {} "
                    "(sha256 {}) vs {} / {} (sha256 {}); descriptive only",
                    rid,
                    which,
                    ident.stage_id,
                    ident.battle_mode_id,
                    blind.manifest_sha256,
                    values["stage_id"],
                    values["battle_mode_id"],
                    digest,
                )
        rows.append(row)
    return rows


def labelled_points(records: Path, stage: int) -> dict[str, list[LabelledPoint]]:
    """Every manifest point with all recorded copies, keyed by recording."""
    manifest = read_json(points_path(records, stage))
    queue = read_json(records / queue_name(stage))["items"]
    refs = read_json(records / queue_key_name(stage))["point_refs"]
    labels = {
        e["item_id"]: e["label"]
        for e in read_json(records / labels_name(stage))["labels"]
    }
    copies: dict[str, dict[int, str | None]] = {}
    for item in queue:
        copies.setdefault(refs[item["point_ref"]], {})[int(item["copy"])] = labels.get(
            item["item_id"]
        )
    out: dict[str, list[LabelledPoint]] = {}
    for p in manifest["points"]:
        recorded = copies.get(p["point_id"], {})
        out.setdefault(p["recording_id"], []).append(
            LabelledPoint(
                p["point_id"],
                p["frame_id"],
                int(p["x"]),
                int(p["y"]),
                tuple(recorded[k] for k in sorted(recorded)),
            )
        )
    return out


class _Recording:
    """Per-recording comparison context (classifiers, frames, identity)."""

    def __init__(
        self,
        repo: Path,
        records: Path,
        work: Path,
        rid: str,
        config: MapInkAnalyzerConfig,
    ) -> None:
        self.rid, self.records, self.work = rid, records, work
        self.v3_payload = load_calibration(repo, rid, "v3")
        self.a1_payload = load_calibration(repo, rid, "a1")
        self.v3 = calibrated_classifier(config, self.v3_payload)
        self.a1 = calibrated_classifier(config, self.a1_payload)
        self.plan = read_json(plan_path(records, rid))
        self.frames = {
            f["frame_id"]: f for f in read_json(extraction_path(records, rid))["frames"]
        }

    def image(self, frame_id: str) -> np.ndarray:
        entry = self.frames[frame_id]
        return load_verified_png(
            self.work / "frames" / self.rid / entry["png"], entry["png_sha256"]
        )

    def hues_equal(self) -> bool:
        return all(
            float(self.v3_payload[k]) == float(self.a1_payload[k])
            for k in ("ally_h", "opponent_h")
        )

    def identity(self, geometry_dir: Path, identity: StageIdentity) -> dict[str, Any]:
        """Amendment 4 ruling 3/4: identity iff no candidate pixel differs (N > 0).

        Hue equality is a sufficient condition and consistency check only.
        N = 0 leaves ``q_all`` undefined and identity unheld (not a defect).
        Equal hues with N > 0 and any differing pixel is a tool defect: STOP.
        """
        images = (self.image(fid) for fid in eligible_frame_ids(self.plan))
        differing, total = q_all(
            images,
            lambda img: point_domain(img, geometry_dir, identity),
            self.a1,
            self.v3,
        )
        if self.hues_equal() and total > 0 and differing > 0:
            raise SealViolation(
                f"{self.rid}: equal hues but {differing} candidate pixels differ "
                "(tool defect; STOP)"
            )
        return {
            "differing": differing,
            "total": total,
            "holds": identity_holds(differing, total),
            "q_all": differing / total if total else Q_ALL_UNDEFINED,
            "hues_equal": self.hues_equal(),
        }

    def outcomes(
        self, stage: int, points: list[LabelledPoint], own: str | None
    ) -> list[FrameOutcome]:
        if own is None:
            return []
        by_frame: dict[str, list[LabelledPoint]] = {}
        for p in points:
            by_frame.setdefault(p.frame_id, []).append(p)
        out = []
        for fid in stage_frame_ids(self.plan, stage):
            image = self.image(fid)
            out.append(
                frame_outcome(
                    fid,
                    by_frame.get(fid, []),
                    own,
                    class_map(image, self.a1),
                    class_map(image, self.v3),
                )
            )
        return out


def _frames_payload(frames: list[FrameOutcome]) -> list[dict[str, Any]]:
    return [
        {
            "frame_id": f.frame_id,
            "judgeable_points": f.judgeable_points,
            "judgeable": f.judgeable,
            "b": f.b,
            "c": f.c,
            "d": f.d,
            "a1_correct": f.a1_correct,
            "v3_correct": f.v3_correct,
            "team_points": f.team_points,
            "a1_inverted": f.a1_inverted,
            "v3_inverted": f.v3_inverted,
            "points": f.points,
        }
        for f in frames
    ]


def _context(records: Path) -> dict[str, Any]:
    tokens = read_json(records / TOKEN_KEY)["tokens"]
    swatches = read_json(records / SWATCHES)["swatches"]
    return {"tokens": tokens, "swatches": swatches}


def _check_undefined_identity(rid: str, ident: Mapping[str, Any], verdict: str) -> None:
    """N = 0 means no judgeable point exists, so precedence 1 must apply."""
    if ident["total"] == 0 and verdict != NOT_EVALUABLE:
        raise MalformedInput(
            f"{rid}: no candidate pixels but verdict {verdict} (tool defect; STOP)"
        )


def compare_recording(
    rec: _Recording,
    stage: int,
    ctx: Mapping[str, Any],
    geometry_dir: Path,
    identity: StageIdentity,
) -> dict[str, Any]:
    """One recording's look at ``stage`` (Stage 2 = combined, final)."""
    token = ctx["tokens"][rec.rid]
    swatch_valid = ctx["swatches"].get(token, {}).get("status") == "valid"
    team1 = [read_json(rec.records / team_identity_name(STAGE_1))]
    own1 = consistent_own_swatch(team_answers(team1, token)) if swatch_valid else None
    points1 = labelled_points(rec.records, STAGE_1).get(rec.rid, [])
    frames1 = rec.outcomes(STAGE_1, points1, own1)
    ident = rec.identity(geometry_dir, identity)
    if stage == STAGE_1:
        verdict, test = stage1_verdict(
            swatch_valid=swatch_valid,
            own_swatch=own1,
            frames=frames1,
            identity=ident["holds"],
        )
        own, frames = own1, {"stage1": _frames_payload(frames1)}
    else:
        team_all = team1 + [read_json(rec.records / team_identity_name(STAGE_2))]
        own = consistent_own_swatch(team_answers(team_all, token))
        points2 = labelled_points(rec.records, STAGE_2).get(rec.rid, [])
        frames2 = rec.outcomes(STAGE_2, points2, own)
        verdict, test = stage2_verdict(
            own_swatch_all=own,
            stage1_frames=frames1,
            stage2_frames=frames2,
            identity=ident["holds"],
        )
        frames = {"stage1": _frames_payload(frames1), "stage2": _frames_payload(frames2)}
    _check_undefined_identity(rec.rid, ident, verdict)
    return {
        "recording_id": rec.rid,
        "verdict": verdict,
        "swatch_valid": swatch_valid,
        "own_swatch": own,
        "identity": ident,
        "test": test.to_dict() if test else None,
        "frames": frames,
    }


def stage1_inconclusive(stage1_records: list[Mapping[str, Any]]) -> list[str]:
    """Sorted ids of Stage 1 INCONCLUSIVE recordings."""
    return sorted(
        r["recording_id"] for r in stage1_records if r["verdict"] == INCONCLUSIVE
    )


def capacity_exhausted_ids(
    records: Path, stage1_records: list[Mapping[str, Any]]
) -> list[str]:
    """Stage 1 INCONCLUSIVE recordings with no unselected Stage 2 interval."""
    return sorted(
        rid
        for rid in stage1_inconclusive(stage1_records)
        if stage2_capacity(read_json(plan_path(records, rid))) == 0
    )


def stage2_trigger_ids(
    records: Path, stage1_records: list[Mapping[str, Any]]
) -> list[str]:
    """Stage 1 INCONCLUSIVE recordings that have Stage 2 capacity."""
    exhausted = set(capacity_exhausted_ids(records, stage1_records))
    return [rid for rid in stage1_inconclusive(stage1_records) if rid not in exhausted]


def require_trigger_matches(repo: Path, records: Path) -> list[str]:
    """Sealed trigger bound to the sealed Stage 1 results (digest and ids)."""
    stage1_file = results_path(records, STAGE_1)
    require_sealed(repo, stage1_file)
    trigger = read_sealed_trigger(repo, records)
    if trigger["stage1_comparison_sha256"] != sha256_file(stage1_file):
        raise SealViolation("Stage 2 trigger is bound to another Stage 1 results record")
    expected = stage2_trigger_ids(records, read_json(stage1_file)["recordings"])
    if trigger["recordings"] != expected:
        raise MalformedInput("Stage 2 trigger differs from the Stage 1 INCONCLUSIVE set")
    return expected


def _stage_recordings(records: Path, stage: int) -> list[str]:
    recordings = list(read_json(points_path(records, stage))["recordings"])
    if stage == STAGE_1 and sorted(recordings) != sorted(CORRECTNESS_APPLICABLE):
        raise MalformedInput("Stage 1 must cover exactly the correctness-applicable set")
    if stage == STAGE_2:
        expected = require_trigger_matches(_repo_of(records), records)
        if sorted(recordings) != expected:
            raise MalformedInput(
                "Stage 2 must cover exactly the Stage 1 INCONCLUSIVE set"
            )
    return recordings


def _write_trigger(records: Path, results: list[dict[str, Any]], digest: str) -> None:
    """Step 6a: only for INCONCLUSIVE recordings with Stage 2 capacity."""
    ids = stage2_trigger_ids(records, results)
    if ids:
        write_json(trigger_path(records), trigger_payload(digest, ids))


def run_comparison(
    records: Path, work: Path, stage: int, tool: Mapping[str, Any]
) -> dict[str, Any]:
    """Seal-checked comparison for a stage; writes results (and the Stage 2 trigger)."""
    require_comparison_seals(records, stage)
    if stage == STAGE_1 and trigger_path(records).exists():
        raise MalformedInput(
            "a Stage 2 trigger already exists before the Stage 1 comparison"
        )
    repo = _repo_of(records)
    identities, reproduction, blind = verified_stage_identities(repo, records)
    config = load_map_ink_config(repo)
    geometry_dir = verify_geometry_pins(repo)
    ctx = _context(records)
    recordings = _stage_recordings(records, stage)
    results = [
        compare_recording(
            _Recording(repo, records, work, rid, config),
            stage,
            ctx,
            geometry_dir,
            identities[rid],
        )
        for rid in recordings
    ]
    payload = {
        "stage": stage,
        "tool": dict(tool),
        "blind_input_reproduction": reproduction,
        "stage_identity_crosscheck": stage_identity_crosscheck(repo, blind, recordings),
        "recordings": results,
    }
    digest = write_json(results_path(records, stage), payload)
    if stage == STAGE_1:
        _write_trigger(records, results, digest)
    return payload
