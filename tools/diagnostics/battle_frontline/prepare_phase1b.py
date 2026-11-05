"""Inventory declared top-down replays and make visual sampling sheets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cv2
from pydantic import BaseModel


class RecordingInventoryRow(BaseModel):
    """Identity and basic decode metadata for one source recording."""

    recording_path: str
    filename: str
    sha256: str
    duration_seconds: float
    width: int
    height: int
    fps: float


def sha256_file(path: Path) -> str:
    """Hash source bytes without loading the full recording into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory_recording(path: Path) -> RecordingInventoryRow:
    """Read stream metadata and hash one explicitly discovered recording."""
    capture = cv2.VideoCapture(str(path))
    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = capture.get(cv2.CAP_PROP_FPS)
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if not capture.isOpened() or fps <= 0 or width <= 0 or height <= 0:
            raise ValueError(f"Could not read video metadata: {path}")
        return RecordingInventoryRow(
            recording_path=str(path.resolve()),
            filename=path.name,
            sha256=sha256_file(path),
            duration_seconds=frame_count / fps,
            width=width,
            height=height,
            fps=fps,
        )
    finally:
        capture.release()


def sample_frame(path: Path, video_time: float) -> cv2.typing.MatLike:
    """Decode the frame nearest a requested video timestamp."""
    capture = cv2.VideoCapture(str(path))
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, video_time * 1000)
        ok, frame = capture.read()
        if not ok:
            raise ValueError(f"Could not decode {video_time:.3f}s from {path}")
        return frame
    finally:
        capture.release()


def prepare(recordings_dir: Path, output_dir: Path) -> list[RecordingInventoryRow]:
    """Write inventory and sampled contact sheets without source edits."""
    recordings = sorted(recordings_dir.glob("*.mov"))
    if len(recordings) != 16:
        raise ValueError(
            f"Expected 16 declared top-down .mov files; found {len(recordings)}"
        )
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Refusing to overwrite existing output: {output_dir}")
    preview_dir = output_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    inventory = [inventory_recording(path) for path in recordings]
    fractions = (0.25, 0.5, 0.75)
    contact_tiles: dict[float, list[cv2.typing.MatLike]] = {
        fraction: [] for fraction in fractions
    }
    for index, row in enumerate(inventory, start=1):
        path = Path(row.recording_path)
        for fraction in fractions:
            timestamp = row.duration_seconds * fraction
            frame = sample_frame(path, timestamp)
            tile = cv2.resize(frame, (480, 270))
            label = f"M{index:02d} {fraction:.0%} | t={timestamp:.0f}s"
            cv2.putText(
                tile,
                label,
                (10, 26),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.imwrite(
                str(preview_dir / f"m{index:02d}_{int(fraction * 100)}.jpg"),
                tile,
            )
            contact_tiles[fraction].append(tile)
    for fraction, tiles in contact_tiles.items():
        rows = [cv2.hconcat(tiles[offset : offset + 4]) for offset in range(0, 16, 4)]
        cv2.imwrite(
            str(preview_dir / f"contact_{int(fraction * 100)}.jpg"),
            cv2.vconcat(rows),
        )
    manifest = [row.model_dump(mode="json") for row in inventory]
    (output_dir / "recording_inventory.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return inventory


def main() -> None:
    """Run the bounded inventory and preview preparation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recordings_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    rows = prepare(args.recordings_dir, args.output_dir)
    print(f"Inventoried {len(rows)} recordings")


if __name__ == "__main__":
    main()
