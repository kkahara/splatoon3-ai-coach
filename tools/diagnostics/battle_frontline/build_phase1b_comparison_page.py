"""Build a read-only human/model comparison page from frozen Phase 1B data."""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> Any:
    """Read a UTF-8 JSON document."""
    return json.loads(path.read_text(encoding="utf-8"))


def _relative(path: Path, page_dir: Path) -> str:
    """Return a browser-relative URL for a workspace file."""
    return Path(os.path.relpath(path.resolve(), page_dir.resolve())).as_posix()


def _baseline_case(
    case_id: str, manifest: dict[str, Any]
) -> dict[str, Any]:
    """Return the latest frozen independent revision for a case."""
    row = next(item for item in manifest["cases"] if item["case_id"] == case_id)
    revisions = row["independent_revisions"]
    if not revisions:
        raise ValueError(f"No frozen independent judgment for {case_id}")
    path = Path(revisions[-1]["snapshot_path"])
    return _read_json(path)


def _reason_codes(case: dict[str, Any], prediction: bool) -> list[str]:
    """List unmet provenance requirements without inventing causes."""
    reasons = []
    if case.get("stage_identity_status") != "verified" or not case.get("stage_id"):
        reasons.append("stage_identity_unverified")
    reasons.append("no_reviewed_stage_registration_for_recording")
    reasons.append("no_verified_same_recording_team_color_calibration")
    if not prediction:
        reasons.append("prediction_not_generated_without_required_provenance")
    return reasons


def build_page(
    full_study: Path,
    pilot_study: Path,
    phase1_study: Path,
    phase1_results: Path,
    frozen_manifest_path: Path,
    output: Path,
) -> dict[str, Any]:
    """Write a static read-only comparison interface and its data manifest."""
    studies = [_read_json(full_study), _read_json(pilot_study)]
    cases_by_id = {
        case["case_id"]: case for study in studies for case in study["cases"]
    }
    frozen = _read_json(frozen_manifest_path)
    phase1 = _read_json(phase1_study)
    result_by_id = {
        row["case_id"]: row for row in _read_json(phase1_results / "results.json")
    }
    phase1_by_id = {row["case_id"]: row for row in phase1["cases"]}
    page_dir = output.parent
    rows = []
    for case_id, case in cases_by_id.items():
        human = _baseline_case(case_id, frozen)
        comparator = case.get("comparator_key")
        result = result_by_id.get(comparator) if comparator else None
        source = phase1_by_id.get(comparator) if comparator else None
        exact = bool(
            result
            and source
            and result.get("status") == "available"
            and result.get("source_sha256") == case["frame_sha256"]
            and source.get("expected_clean_sha256") == case["frame_sha256"]
            and result.get("source_recording_sha256") == case["recording_sha256"]
        )
        source_path = Path(case["frame_path"])
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        entry: dict[str, Any] = {
            "case_id": case_id,
            "recording_filename": case["recording_filename"],
            "recording_index": case["recording_index"],
            "video_time": case["video_time"],
            "frame_sha256": case["frame_sha256"],
            "human_judgment": human["judgment"],
            "human_reasons": human["reasons"],
            "human_explanation": human["explanation"],
            "human_shapes": human["shapes"],
            "source_image": _relative(source_path, page_dir),
            "prediction_status": "existing_exact_prediction" if exact else "unavailable",
            "unavailable_reasons": [] if exact else _reason_codes(case, False),
            "stage_id": case.get("stage_id"),
            "stage_identity_status": case.get("stage_identity_status"),
            "comparator_key": comparator,
        }
        if exact and result is not None:
            overlay = phase1_results / result["overlay_filename"]
            entry.update(
                {
                    "generated_segments": result.get("line_segments")
                    or [result.get("line", [])],
                    "paint_image": _relative(overlay, page_dir),
                    "polygon_sha256": result["polygon_sha256"],
                    "classifier_config_sha256": result[
                        "classifier_config_sha256"
                    ],
                    "calibration": result["calibration"],
                    "registration": result["registration"],
                }
            )
        rows.append(entry)
    payload = {
        "schema_version": 1,
        "purpose": "read_only_human_vs_paint_comparison",
        "human_baseline_manifest": _relative(frozen_manifest_path, page_dir),
        "case_count": len(rows),
        "exact_predictions": sum(
            row["prediction_status"] == "existing_exact_prediction" for row in rows
        ),
        "unavailable_predictions": sum(
            row["prediction_status"] == "unavailable" for row in rows
        ),
        "cases": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_page(payload), encoding="utf-8")
    data_path = output.with_suffix(".json")
    data_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def _page(payload: dict[str, Any]) -> str:
    """Render a compact, offline-safe page with side-by-side SVG line views."""
    encoded = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    title = "Battle frontline: frozen human labels vs exact paint predictions"
    template = Path(__file__).with_name("phase1b_comparison.html").read_text(
        encoding="utf-8"
    )
    return template.replace("__TITLE__", html.escape(title)).replace(
        "__DATA__", encoded
    )


def main() -> None:
    """Build the read-only comparison page for a frozen Phase 1B baseline."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("full_study", type=Path)
    parser.add_argument("pilot_study", type=Path)
    parser.add_argument("phase1_study", type=Path)
    parser.add_argument("phase1_results", type=Path)
    parser.add_argument("frozen_manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    payload = build_page(
        args.full_study,
        args.pilot_study,
        args.phase1_study,
        args.phase1_results,
        args.frozen_manifest,
        args.output,
    )
    print(
        f"Wrote {payload['case_count']} cases: "
        f"{payload['exact_predictions']} exact predictions, "
        f"{payload['unavailable_predictions']} unavailable"
    )


if __name__ == "__main__":
    main()
