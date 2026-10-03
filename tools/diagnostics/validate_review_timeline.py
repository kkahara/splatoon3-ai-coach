#!/usr/bin/env python3
"""Run the deterministic quality gate for a Review timeline artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from splatoon3_ai_coach.review.validation import (
    ValidationThresholds,
    load_dataset,
    load_labels,
    render_report,
    validate_artifact,
)


def main() -> int:
    """Validate a persisted artifact and write a quality-gate report."""
    args = _parse_args()
    labels = load_labels(args.labels)
    artifact_path = args.artifact or Path(labels.source_artifact)
    manifest_path = args.manifest or Path(labels.source_manifest)
    thresholds = _load_thresholds(args.thresholds)
    result = validate_artifact(
        load_dataset(artifact_path),
        labels,
        thresholds=thresholds,
        manifest_path=manifest_path,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "metrics.json").write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.out / "REPORT.md").write_text(
        render_report(result),
        encoding="utf-8",
    )
    print(f"Review timeline quality gate: {'GO' if result.go else 'NO-GO'}")
    return 0 if result.go else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--thresholds",
        type=Path,
        help="Optional YAML mapping overriding the default gate thresholds.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("analysis/review_validation"),
    )
    return parser.parse_args()


def _load_thresholds(path: Path | None) -> ValidationThresholds:
    if path is None:
        return ValidationThresholds()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ValidationThresholds.model_validate(data)


if __name__ == "__main__":
    raise SystemExit(main())

