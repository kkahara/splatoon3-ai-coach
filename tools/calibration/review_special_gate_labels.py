#!/usr/bin/env python3
"""Convert reviewed event-lane labels into Review quality-gate labels.

Reads ``sheet/labels.yaml`` (``review_status: reviewed``) and writes a
``ValidationLabels`` YAML with the confirmed specials and deaths for
``tools/diagnostics/validate_review_timeline.py``. Uncertain and rejected
entries are dropped. No per-sample clock/cursor/roster labels are written,
so those gate checks stay unlabeled for these files; only the event checks
are meaningful.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def main() -> int:
    """Write one gate-label file per recording."""
    args = _parse_args()
    data = yaml.safe_load(args.labels.read_text(encoding="utf-8")) or {}
    if data.get("review_status") != "reviewed":
        raise SystemExit(f"{args.labels}: labels are not reviewed")
    confirmed = [e for e in data["events"] if e.get("status") == "confirmed"]
    gate = {
        "schema_version": "1",
        "source_artifact": str(args.artifact_dir / "review_timeline.json"),
        "source_manifest": str(args.artifact_dir / "timeline_samples_manifest.yaml"),
        "samples": [],
        "specials": [
            {
                "elapsed": float(e["elapsed"]),
                "slot": e["slot"],
                "special_id": e["special_id"],
            }
            for e in confirmed
            if e["kind"] == "special"
        ],
        "deaths": [
            {"elapsed": float(e["elapsed"]), "slot": e["slot"]}
            for e in confirmed
            if e["kind"] == "death"
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(yaml.safe_dump(gate, sort_keys=False), encoding="utf-8")
    print(f"{len(gate['specials'])} specials, {len(gate['deaths'])} deaths -> {args.out}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", type=Path)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
