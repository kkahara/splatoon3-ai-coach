#!/usr/bin/env python3
"""Write a trimmed golden-trace fixture from a real analysis directory.

The fixture keeps everything inside the video-time window, plus the
match-wide inputs that match-level rules read:

- every frame, so cadence times stay complete;
- outside the window, only ``timer`` (playback-rate estimate), ``map_overlay``
  (episode map coverage from the previous ACTIVE_AGAIN) and ``match_intro``
  (battle mode) detections;
- every game event (match-level map coverage and unbounded map lookback);
- state snapshots inside the window, or from ``--snapshots-from`` when the
  roster series must start at a full 4v4 (a series that begins mid-dip makes
  the initial deficit look like fresh deaths).

An optional model output (``--model-output``) is stored verbatim for
validator golden tests.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

MATCH_WIDE_DETECTORS = frozenset({"timer", "map_overlay", "match_intro"})
_COPIED_KEYS = (
    "analysis",
    "extraction_manifest_path",
    "schema_version",
    "timing",
    "video",
    "video_identity",
)


def trim_manifest(
    manifest: dict[str, Any],
    start: float,
    end: float,
    *,
    snapshots_from: float | None = None,
) -> dict[str, Any]:
    """Window-trimmed copy of a raw ``vision_manifest.json`` payload."""
    snap_start = start if snapshots_from is None else snapshots_from
    trimmed = {key: manifest.get(key) for key in _COPIED_KEYS}
    trimmed["frame_results"] = [
        _trim_frame(frame, start, end) for frame in manifest.get("frame_results") or []
    ]
    trimmed["state_snapshots"] = [
        snap
        for snap in manifest.get("state_snapshots") or []
        if snap_start <= float(snap.get("timestamp", -1.0)) <= end
    ]
    trimmed["game_events"] = list(manifest.get("game_events") or [])
    return trimmed


def _trim_frame(frame: dict[str, Any], start: float, end: float) -> dict[str, Any]:
    if start <= float(frame["timestamp"]) <= end:
        return frame
    kept = [
        det
        for det in frame.get("detections") or []
        if det.get("detector_name") in MATCH_WIDE_DETECTORS
    ]
    return {**frame, "detections": kept}


def main() -> int:
    """Read an analysis dir and write ``{name}.json`` under the fixture dir."""
    args = _parse_args()
    manifest = json.loads(
        (args.analysis_dir / "vision_manifest.json").read_text(encoding="utf-8")
    )
    fixture: dict[str, Any] = {
        "source_analysis": args.analysis_dir.name,
        "window": [args.start, args.end],
        "snapshots_from": args.snapshots_from,
        "manifest": trim_manifest(
            manifest, args.start, args.end, snapshots_from=args.snapshots_from
        ),
    }
    if args.model_output is not None:
        output = args.model_output.read_text(encoding="utf-8")
        fixture["model_output"] = json.loads(output)
        fixture["model_output_source"] = args.model_output.name
    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(fixture, separators=(",", ":")) + "\n"
    args.out.write_text(payload, encoding="utf-8")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("analysis_dir", type=Path)
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--snapshots-from", type=float, default=None)
    parser.add_argument("--model-output", type=Path, default=None)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
