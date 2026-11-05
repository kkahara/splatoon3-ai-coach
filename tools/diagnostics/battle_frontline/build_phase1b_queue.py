"""Build the first human-only review queue from the inventoried recordings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
from pydantic import BaseModel

from tools.diagnostics.battle_frontline.prepare_phase1b import (
    RecordingInventoryRow,
    sha256_file,
)


class QueueCase(BaseModel):
    """One immutable source frame and its exact recording provenance."""

    case_id: str
    recording_index: int
    recording_path: str
    recording_filename: str
    recording_sha256: str
    video_time: float
    selection_slot: str
    stage_id: str | None = None
    mode_id: str | None = None
    stage_identity_status: str = "unverified"
    frame_path: str
    frame_sha256: str
    comparator_key: str | None = None


def selected_times(recording_index: int, duration: float) -> list[tuple[str, float]]:
    """Return one time-stratified sample per match and four paired examples."""
    paired = {
        3: [("early", duration * 0.25), ("late", duration * 0.75)],
        4: [("early", duration * 0.25), ("late", duration * 0.75)],
        8: [("331s", 105.0), ("224s", 172.0)],
        11: [("early", duration * 0.25), ("late", duration * 0.75)],
    }
    return paired.get(recording_index, [("middle", duration * 0.5)])


def extract_frame(path: Path, video_time: float, destination: Path) -> str:
    """Extract and hash a clean PNG still from its declared top-down source."""
    capture = cv2.VideoCapture(str(path))
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, video_time * 1000)
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok:
        raise ValueError(f"Could not decode {video_time:.3f}s from {path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), frame):
        raise OSError(f"Could not write source frame derivative: {destination}")
    return sha256_file(destination)


def build(
    inventory_path: Path, output_dir: Path, phase1a_study: Path | None = None
) -> list[QueueCase]:
    """Write 20 queued stills without changing the recording or prior study."""
    if (output_dir / "study.json").exists() or (output_dir / "frames").exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {output_dir}")
    inventory = [
        RecordingInventoryRow.model_validate(row)
        for row in json.loads(inventory_path.read_text(encoding="utf-8"))
    ]
    prior_cases = {}
    if phase1a_study is not None:
        prior_payload = json.loads(phase1a_study.read_text(encoding="utf-8"))
        prior_cases = {case["case_id"]: case for case in prior_payload["cases"]}
    if len(inventory) != 16:
        raise ValueError("Phase 1B queue requires the inventoried 16 recordings")
    frames_dir = output_dir / "frames"
    queue = []
    for index, row in enumerate(inventory, start=1):
        path = Path(row.recording_path)
        if sha256_file(path) != row.sha256:
            raise ValueError(f"Recording hash changed since inventory: {row.filename}")
        for slot, timestamp in selected_times(index, row.duration_seconds):
            case_id = f"m{index:02d}_{slot}"
            frame_path = frames_dir / f"{case_id}.png"
            comparator = None
            if index == 8 and slot in {"331s", "224s"}:
                comparator = {
                    "331s": "frontline_331",
                    "224s": "frontline_224",
                }[slot]
            prior = prior_cases.get(comparator) if comparator else None
            if prior is not None:
                frame_path = Path(prior["clean_image"])
                frame_hash = sha256_file(frame_path)
                if frame_hash != prior["expected_clean_sha256"]:
                    raise ValueError(f"Prior comparator source hash mismatch: {case_id}")
            else:
                frame_hash = extract_frame(path, timestamp, frame_path)
            queue.append(
                QueueCase(
                    case_id=case_id,
                    recording_index=index,
                    recording_path=str(path.resolve()),
                    recording_filename=row.filename,
                    recording_sha256=row.sha256,
                    video_time=timestamp,
                    selection_slot=slot,
                    frame_path=str(frame_path.resolve()),
                    frame_sha256=frame_hash,
                    comparator_key=comparator,
                )
            )
    if len(queue) != 20:
        raise AssertionError(f"Expected 20 review examples; built {len(queue)}")
    payload = {
        "schema_version": 1,
        "workflow": "human_boundary_phase1b",
        "purpose": "independent_human_boundary_definition",
        "stage_mode_policy": "Unknown unless explicitly verified; no guesses.",
        "comparison_policy": (
            "Only exact prior Phase 1 comparators are revealed after an "
            "independent annotation is saved."
        ),
        "cases": [case.model_dump(mode="json") for case in queue],
    }
    (output_dir / "study.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    return queue


def main() -> None:
    """Build the versioned Phase 1B review queue."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--phase1a-study", type=Path)
    args = parser.parse_args()
    queue = build(args.inventory, args.output, args.phase1a_study)
    print(f"Built {len(queue)} blind review frames")


if __name__ == "__main__":
    main()
