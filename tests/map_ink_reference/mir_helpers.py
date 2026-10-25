"""Synthetic fixtures for the Amendment 3 tooling tests (no corpus media)."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np

TB_DEN = 1000
FRAME_W, FRAME_H = 320, 240


def frame_spec(
    t: float,
    *,
    eligible: bool = True,
    lifecycle: str = "alive",
    phase: str | None = None,
    roster: bool | None = None,
) -> dict[str, Any]:
    """One cadence frame description."""
    return {
        "t": t,
        "map": eligible,
        "roster": (not eligible) if roster is None else roster,
        "phase": phase or "in_match",
        "lifecycle": lifecycle,
    }


def manifest_from_specs(
    specs: Sequence[dict[str, Any]],
    *,
    video_identity: str = "vid-synthetic",
    match_end: float | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A minimal vision manifest with the fields the frame rule reads."""
    frames, snaps = [], []
    for i, s in enumerate(specs):
        frames.append(
            {
                "frame_id": f"f{i:05d}",
                "timestamp": s["t"],
                "source_pts": int(round(s["t"] * TB_DEN)),
                "source_time_base_num": 1,
                "source_time_base_den": TB_DEN,
                "source_frame_index": i,
                "detections": [
                    {"detector_name": "map_overlay", "reading": {"present": s["map"]}},
                    {
                        "detector_name": "player_count",
                        "reading": {"roster_visible": s["roster"]},
                    },
                ],
            }
        )
        snaps.append(
            {
                "timestamp": s["t"],
                "match_phase": s["phase"],
                "player_lifecycle": s["lifecycle"],
            }
        )
    events = (
        []
        if match_end is None
        else [{"event_type": "MATCH_END", "start_time": match_end}]
    )
    manifest = {
        "video_identity": video_identity,
        "video": {"analysis_width": FRAME_W, "analysis_height": FRAME_H},
        "frame_results": frames,
        "state_snapshots": snaps,
        "game_events": events,
    }
    manifest.update(extra or {})
    return manifest


def interval_specs(lengths: Iterable[int], start: float = 0.0) -> list[dict[str, Any]]:
    """Eligible runs of the given lengths, separated by one ineligible frame."""
    specs, t = [], start
    for length in lengths:
        for _ in range(length):
            specs.append(frame_spec(t))
            t += 0.5
        specs.append(frame_spec(t, eligible=False))
        t += 0.5
    return specs


def write_geometry(root: Path, stage_id: str = "synth") -> Path:
    """A geometry directory whose single region covers the full frame."""
    stage = root / "geometry" / stage_id
    stage.mkdir(parents=True)
    (stage / "default.yaml").write_text(
        f"stage_id: {stage_id}\nbattle_mode_id: null\n"
        "regions:\n  - id: R01\n    roi: [0.0, 0.0, 1.0, 1.0]\n"
    )
    return root / "geometry"


def hsv_pixel(h: int, s: int, v: int) -> tuple[int, int, int]:
    """BGR value of an OpenCV HSV triple."""
    bgr = cv2.cvtColor(np.uint8([[[h, s, v]]]), cv2.COLOR_HSV2BGR)[0, 0]
    return int(bgr[0]), int(bgr[1]), int(bgr[2])


def ink_image(seed: int, w: int = FRAME_W, h: int = FRAME_H) -> np.ndarray:
    """Two saturated ink hues on a dark background, in random blocks."""
    rng = np.random.default_rng(seed)
    image = np.full((h, w, 3), 20, dtype=np.uint8)
    colours = (hsv_pixel(30, 220, 220), hsv_pixel(120, 220, 220))
    for y in range(0, h, 16):
        for x in range(0, w, 16):
            pick = rng.integers(3)
            if pick < 2:
                image[y : y + 16, x : x + 16] = colours[pick]
    return image


def git(repo: Path, *args: str) -> str:
    """Run git in ``repo``."""
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def init_repo(path: Path) -> Path:
    """Fresh repository with a committer identity."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    git(path, "config", "commit.gpgsign", "false")
    return path


def commit(repo: Path, paths: Sequence[Path], message: str) -> str:
    """Add and commit the given paths; return the commit id."""
    git(repo, "add", "-f", *(str(p) for p in paths))
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


class Counter:
    """Deterministic seed/id source for sessions."""

    def __init__(self, start: int = 1) -> None:
        self.value = start

    def seed(self) -> int:
        self.value += 1
        return self.value * 7919

    def new_id(self) -> str:
        self.value += 1
        return f"id{self.value:06d}"
