"""Production-equivalence check for the promoted roster reach extensions.

Runs the production ``PlayerCountDetector`` (frozen A1 +16 / O3 +8 right-side
search extensions) and the same detector with extensions zeroed on every
roster-baseline frame and every held-out frame. Passes only when:

- visibility and per-slot edge energy are identical;
- every non-extended slot scores identically;
- extended slots equal the independent Experiment 3 reference
  (``roster_roi_reach.best_placement`` over the widened map) and, on held-out
  frames, the sealed ``candidate_score``; the zero-extension detector equals
  the reference at R=0 and the sealed ``production_score``. Raw scores above
  1.0 are compared under production's 1.0 starting best (see ``same_score``)
  and counted separately.

Read-only: nothing is tuned; a failure means the implementation is wrong.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from roster_heldout_reach import blind_image, read_frame
from roster_roi_reach import best_placement, label_of, slot_maps
from roster_x_appearance import SLOTS, _load, decode_verified, slot_boxes

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import PlayerCountDetectorConfig
from splatoon3_ai_coach.vision.player_count import PlayerCountDetector

TOLERANCE = 1e-6
FROZEN = {"a1": 16, "o3": 8}
PRODUCTION_CEILING = 1.0


def same_score(production: float, reference: float) -> bool:
    """Equal within tolerance, or both above production's 1.0 starting best.

    Masked ``TM_SQDIFF_NORMED`` can exceed 1.0; production initializes the best
    score at 1.0 and so reports 1.0 there, while the reference keeps the raw min.
    """
    if abs(production - reference) <= TOLERANCE:
        return True
    return production == PRODUCTION_CEILING and reference > PRODUCTION_CEILING


def clamped(production: float, reference: float) -> bool:
    """The match relied on the production 1.0 ceiling rule."""
    return abs(production - reference) > TOLERANCE and same_score(production, reference)


def zeroed(config: PlayerCountDetectorConfig) -> PlayerCountDetectorConfig:
    """The production detector config with every search extension at 0."""
    return config.model_copy(
        update={
            "ally_search_extend_right_px": [0, 0, 0, 0],
            "opponent_search_extend_right_px": [0, 0, 0, 0],
        }
    )


def extensions(config: PlayerCountDetectorConfig) -> dict[str, int]:
    """Configured right extension per slot name."""
    values = [
        *config.ally_search_extend_right_px,
        *config.opponent_search_extend_right_px,
    ]
    return dict(zip(SLOTS, values, strict=True))


def compare_frame(
    frame: np.ndarray,
    new: PlayerCountDetector,
    old: PlayerCountDetector,
    boxes: list[tuple[int, int, int, int]],
    sealed: dict[str, Any] | None,
) -> dict[str, Any]:
    """Every check for one frame; ``mismatches`` lists failures."""
    reading_new, _ = new.detect(frame)
    reading_old, _ = old.detect(frame)
    mismatches: list[str] = []
    if reading_new.slot_edge_energy != reading_old.slot_edge_energy:
        mismatches.append("edge_energy")
    if reading_new.roster_visible != reading_old.roster_visible:
        mismatches.append("roster_visible")
    result: dict[str, Any] = {"visible": reading_new.roster_visible, "slots": {}}
    if not reading_new.roster_visible:
        result["mismatches"] = mismatches
        return result
    scores_new = [*reading_new.ally_slot_scores, *reading_new.opponent_slot_scores]
    scores_old = [*reading_old.ally_slot_scores, *reading_old.opponent_slot_scores]
    ext = extensions(new.config)
    for index, slot in enumerate(SLOTS):
        item = _compare_slot(
            frame,
            boxes[index],
            new,
            slot,
            ext[slot],
            scores_new[index],
            scores_old[index],
        )
        if sealed is not None:
            item.update(_compare_sealed(sealed["slots"][slot], slot, item))
        mismatches += [f"{slot}:{name}" for name in item.pop("failed")]
        result["slots"][slot] = item
    result["mismatches"] = mismatches
    return result


def _compare_slot(
    frame: np.ndarray,
    box: tuple[int, int, int, int],
    detector: PlayerCountDetector,
    slot: str,
    extend: int,
    score_new: float,
    score_old: float,
) -> dict[str, Any]:
    maps = slot_maps(frame, box, detector._templates)  # noqa: SLF001
    ref_old = best_placement(maps, box, 0, 0).score
    ref_new = best_placement(maps, box, 0, extend).score
    failed = []
    if not same_score(score_old, ref_old):
        failed.append("zero_extension_vs_reference")
    if extend == 0 and score_new != score_old:
        failed.append("unextended_slot_changed")
    if not same_score(score_new, ref_new) or label_of(score_new) != label_of(ref_new):
        failed.append("extended_vs_reference")
    return {
        "new": score_new,
        "old": score_old,
        "reference_new": ref_new,
        "reference_old": ref_old,
        "clamped": clamped(score_new, ref_new) or clamped(score_old, ref_old),
        "label_new": label_of(score_new),
        "label_old": label_of(score_old),
        "failed": failed,
    }


def _compare_sealed(
    sealed: dict[str, Any], slot: str, item: dict[str, Any]
) -> dict[str, Any]:
    want = sealed["candidate_score"] if slot in FROZEN else sealed["production_score"]
    failed = list(item["failed"])
    if not same_score(item["new"], want):
        failed.append("new_vs_sealed")
    if not same_score(item["old"], sealed["production_score"]):
        failed.append("old_vs_sealed_production")
    return {"sealed_new": want, "failed": failed}


def baseline_frames(baseline: Path, video_dir: Path, config: Any) -> list[tuple]:
    """Every roster-baseline sample, decoded and hash-verified."""
    manifest, _labels, keys = _load(baseline)
    frames = []
    for sample in manifest["samples"]:
        sid = sample["sample_id"]
        frame, verified = decode_verified(sample, keys[sid], video_dir, config)
        if not verified:
            raise RuntimeError(f"{sid}: decoded frame does not match sealed hash")
        frames.append((sid, sample["source_class"], frame, None))
    return frames


def heldout_frames(heldout: Path, video_dir: Path, config: Any) -> list[tuple]:
    """Every sealed held-out sample, decoded and checked against its image hash."""
    key = json.loads((heldout / "sealed" / "key.json").read_text("utf-8"))
    frames = []
    for entry in key["samples"]:
        capture = cv2.VideoCapture(str(video_dir / entry["video"]))
        frame = read_frame(capture, entry["t"])
        capture.release()
        image = blind_image(frame, slot_boxes(config, frame.shape), entry["sample_id"])
        digest = hashlib.sha256(cv2.imencode(".png", image)[1].tobytes()).hexdigest()
        if digest != entry["image_sha256"]:
            raise RuntimeError(f"{entry['sample_id']}: frame does not match sealed hash")
        frames.append((entry["sample_id"], "heldout_live", frame, entry))
    return frames


def run(
    baseline: Path, heldout: Path, video_dir: Path, output: Path, config_path: Path
) -> dict:
    """Compare production and zero-extension detectors on every frame; persist."""
    config = load_config(config_path)
    pc = config.vision.player_count
    if extensions(pc) != {s: FROZEN.get(s, 0) for s in SLOTS}:
        raise RuntimeError(f"configured extensions differ from frozen: {extensions(pc)}")
    new, old = PlayerCountDetector(pc), PlayerCountDetector(zeroed(pc))
    frames = baseline_frames(baseline, video_dir, config)
    frames += heldout_frames(heldout, video_dir, config)
    rows, summary = [], Counter()
    for sid, source, frame, sealed in frames:
        row = compare_frame(frame, new, old, slot_boxes(config, frame.shape), sealed)
        row.update({"sample_id": sid, "source": source})
        rows.append(row)
        summary[f"frames_{source}"] += 1
        summary["visible_frames"] += row["visible"]
        summary["mismatches"] += len(row["mismatches"])
        for slot, item in row["slots"].items():
            summary["slots_compared"] += 1
            summary["slots_clamped_above_one"] += item["clamped"]
            if item["label_new"] != item["label_old"]:
                summary[f"label_changes_{slot}"] += 1
    payload = {
        "schema_version": 1,
        "extensions": extensions(pc),
        "threshold": pc.sqdiff_match_threshold,
        "tolerance": TOLERANCE,
        "passed": summary["mismatches"] == 0,
        "summary": dict(sorted(summary.items())),
        "mismatch_rows": [r for r in rows if r["mismatches"]],
        "frames": rows,
    }
    output.write_text(json.dumps(payload, indent=1) + "\n", "utf-8")
    return payload


def main() -> int:
    """Run the equivalence check; exit 1 on any mismatch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--heldout", type=Path, required=True)
    parser.add_argument("--video-dir", type=Path, default=Path.home() / "Movies")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=default_config_path())
    args = parser.parse_args()
    payload = run(args.baseline, args.heldout, args.video_dir, args.output, args.config)
    print(json.dumps({"passed": payload["passed"], **payload["summary"]}, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
