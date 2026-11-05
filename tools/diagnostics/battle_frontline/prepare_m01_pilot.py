"""Prepare a three-frame M01 review batch without changing the full queue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools.diagnostics.battle_frontline.build_phase1b_queue import extract_frame


def build_pilot(
    full_study: Path, inventory_path: Path, destination: Path
) -> dict[str, Any]:
    """Write M01 early, middle and late frames as a separate 3-case study."""
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite pilot study: {destination}")
    study = json.loads(full_study.read_text(encoding="utf-8"))
    middle = next(case for case in study["cases"] if case["case_id"] == "m01_middle")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    duration = next(
        row["duration_seconds"]
        for row in inventory
        if row["filename"] == middle["recording_filename"]
    )
    recording_path = Path(middle["recording_path"])
    frames_dir = full_study.parent / "frames"
    cases = []
    for slot, fraction in (("early", 0.25), ("middle", 0.5), ("late", 0.75)):
        timestamp = duration * fraction
        frame_path = (
            Path(middle["frame_path"])
            if slot == "middle"
            else frames_dir / f"m01_{slot}.png"
        )
        frame_hash = (
            middle["frame_sha256"]
            if slot == "middle"
            else extract_frame(recording_path, timestamp, frame_path)
        )
        cases.append(
            {
                **middle,
                "case_id": f"m01_{slot}",
                "video_time": timestamp,
                "selection_slot": slot,
                "frame_path": str(frame_path.resolve()),
                "frame_sha256": frame_hash,
                "comparator_key": None,
            }
        )
    payload = {
        "schema_version": 1,
        "workflow": "human_boundary_phase1b_batch",
        "batch_id": "m01_first_three",
        "purpose": "human_first_boundary_definition",
        "parent_study": str(full_study.resolve()),
        "recording_sha256": middle["recording_sha256"],
        "cases": cases,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    """Create the initial M01 pilot batch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("full_study", type=Path)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    payload = build_pilot(args.full_study, args.inventory, args.destination)
    print(f"Prepared {len(payload['cases'])} M01 frames in {args.destination}")
    for case in payload["cases"]:
        print(f"{case['case_id']}: {case['video_time']:.3f}s {case['frame_sha256']}")


if __name__ == "__main__":
    main()
