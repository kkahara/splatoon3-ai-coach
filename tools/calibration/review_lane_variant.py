#!/usr/bin/env python3
"""One-factor event-lane variant: re-import baseline and variant, diff outputs.

Re-imports every recording's stored frames twice (frozen config, and frozen
config plus ``--set key=value`` overrides), writes both artifacts under
``--out/{baseline,variant}/<rid>/review_timeline.json``, and reports per
recording:

* every death marker and special event added, removed or relabelled anywhere
  in the recording (not only inside labelled windows);
* special-gate metrics, death slot accuracy and the GO gate
  (``review_timeline_experiment._metrics``);
* death timestamps vs roster-lamp bounds.

Score the extraction change with ``diagnostics/review_extraction_audit.py
--artifact-root <out>/<arm>``. Tuning/held-out split follows
``review_timeline_experiment.RECORDINGS``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from review_timeline_experiment import RECORDINGS, _metrics, lamp_references

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.review.models import ReviewTimelineDataset
from splatoon3_ai_coach.review.timeline_pipeline import import_timeline
from splatoon3_ai_coach.review.validation import load_labels

SCREEN_WIDTH = 1920


def main() -> int:
    """Run both arms on every recording and write ``variant.md`` / ``.json``."""
    args = _parse_args()
    base = load_config(default_config_path()).review
    update = dict(_parse_set(item) for item in args.set)
    variant = base.model_copy(update=update)
    go_labels = load_labels(args.go_labels)
    results: dict[str, Any] = {"overrides": update, "recordings": {}}
    for rid, split in RECORDINGS.items():
        arms = {
            "baseline": _import(
                args.imports / rid, base, args.out / "baseline" / rid, rid
            ),
            "variant": _import(
                args.imports / rid, variant, args.out / "variant" / rid, rid
            ),
        }
        references = lamp_references(arms["baseline"])
        labels = load_labels(args.labels_root / rid / "gate_labels.yaml")
        go = go_labels if rid == "21-23-30" else None
        results["recordings"][rid] = {
            "split": split,
            "diff": diff_outputs(arms["baseline"], arms["variant"]),
            **{
                arm: _metrics(ds, arms["baseline"], references, labels, go, cfg)
                for (arm, ds), cfg in zip(arms.items(), (base, variant), strict=True)
            },
        }
        print(rid, json.dumps(results["recordings"][rid]["diff"]["summary"]))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "variant.json").write_text(json.dumps(results, indent=2) + "\n", "utf-8")
    (args.out / "variant.md").write_text(render(results), "utf-8")
    return 0


def _import(
    source: Path, config: ReviewTimelineConfig, out: Path, rid: str
) -> ReviewTimelineDataset:
    out.mkdir(parents=True, exist_ok=True)
    return import_timeline(
        source / "timeline_samples",
        config=config,
        manifest_path=source / "timeline_samples_manifest.yaml",
        output_dir=out,
        source_recording_id=rid,
    )


def _items(ds: ReviewTimelineDataset) -> dict[tuple[str, int], dict[str, Any]]:
    """Markers and specials keyed by (row slot, rounded screen x)."""
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for m in ds.match_history_events:
        x = m.x_norm * SCREEN_WIDTH
        out[(m.player_slot_id or "?", round(x))] = {
            "type": f"marker:{m.kind}",
            "x": round(x, 1),
            "elapsed": round(m.elapsed_estimate, 2),
        }
    for e in ds.special_events:
        x = e.x_norm * SCREEN_WIDTH
        out[(e.slot_id, round(x))] = {
            "type": f"special:{e.special_id}",
            "x": round(x, 1),
            "elapsed": round(e.elapsed_estimate, 2),
        }
    return out


def diff_outputs(
    base: ReviewTimelineDataset, variant: ReviewTimelineDataset
) -> dict[str, Any]:
    """Added / removed / relabelled lane outputs across the whole recording."""
    a, b = _items(base), _items(variant)
    added = [{"slot": k[0], **b[k]} for k in sorted(b.keys() - a.keys())]
    removed = [{"slot": k[0], **a[k]} for k in sorted(a.keys() - b.keys())]
    changed = [
        {"slot": k[0], "x": a[k]["x"], "from": a[k]["type"], "to": b[k]["type"]}
        for k in sorted(a.keys() & b.keys())
        if a[k]["type"] != b[k]["type"]
    ]
    return {
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
        },
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def render(results: dict[str, Any]) -> str:
    """Markdown report."""
    lines = ["# Event-lane variant", "", f"Overrides: `{results['overrides']}`", ""]
    for rid, r in results["recordings"].items():
        lines += [f"## {rid} ({r['split']})", ""]
        for kind in ("added", "removed", "changed"):
            for item in r["diff"][kind]:
                lines.append(f"- {kind}: `{item}`")
        if not any(r["diff"][k] for k in ("added", "removed", "changed")):
            lines.append("- no lane output changes")
        lines += ["", "| Metric | baseline | variant |", "|---|---|---|"]
        for key in ("gate", "deaths", "special_checks_pass", "unknown_specials", "go"):
            if key in r["baseline"]:
                lines.append(
                    f"| {key} | `{r['baseline'][key]}` | `{r['variant'][key]}` |"
                )
        lines.append("")
    return "\n".join(lines)


def _parse_set(item: str) -> tuple[str, Any]:
    key, _, raw = item.partition("=")
    if key not in ReviewTimelineConfig.model_fields:
        raise SystemExit(f"unknown review config field {key!r}")
    return key, json.loads(raw)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", action="append", required=True, help="key=json")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--imports", type=Path, default=Path("analysis/review_validation/gate")
    )
    parser.add_argument(
        "--labels-root", type=Path, default=Path("analysis/review_validation/specials")
    )
    parser.add_argument(
        "--go-labels",
        type=Path,
        default=Path("analysis/review_validation/go_gate/labels.yaml"),
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
