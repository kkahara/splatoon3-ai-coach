"""Freeze Phase 1B human decisions and render exact-frame model comparisons."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hash of a file's original bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_history(source: Path, destination: Path, phase: str) -> list[dict[str, Any]]:
    """Copy immutable revisions of one phase and record byte hashes."""
    rows = []
    for original in sorted(source.glob(f"{phase}.r*.json")):
        target = destination / original.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        rows.append(
            {
                "original_path": str(original.resolve()),
                "snapshot_path": str(target.resolve()),
                "sha256": sha256_file(target),
                "filename": original.name,
            }
        )
    return rows


def _combined_cases(study_paths: list[Path]) -> list[dict[str, Any]]:
    """Merge queue and pilot cases while rejecting conflicting source identity."""
    indexed: dict[str, dict[str, Any]] = {}
    for study_path in study_paths:
        study = json.loads(study_path.read_text(encoding="utf-8"))
        for case in study["cases"]:
            prior = indexed.get(case["case_id"])
            if prior is not None and prior["frame_sha256"] != case["frame_sha256"]:
                raise ValueError(f"Conflicting source frame for {case['case_id']}")
            indexed[case["case_id"]] = case
    return list(indexed.values())


def freeze_annotations(study_paths: list[Path], output_dir: Path) -> dict[str, Any]:
    """Copy all independent revisions and isolate post-reveal revisions."""
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite frozen baseline: {output_dir}")
    output_dir.mkdir(parents=True)
    cases_to_freeze = _combined_cases(study_paths)
    source_root = study_paths[0].parent / "annotations"
    cases = []
    for item in cases_to_freeze:
        case_id = item["case_id"]
        source = source_root / case_id
        independent = _copy_history(
            source, output_dir / "independent" / case_id, "independent"
        )
        post_reveal = _copy_history(
            source, output_dir / "post_reveal_separate" / case_id, "post_reveal"
        )
        cases.append(
            {
                "case_id": case_id,
                "recording_sha256": item["recording_sha256"],
                "source_frame_sha256": item["frame_sha256"],
                "independent_revisions": independent,
                "post_reveal_revisions_separate": post_reveal,
            }
        )
    manifest = {
        "schema_version": 1,
        "purpose": "frozen_human_baseline_before_model_comparison",
        "source_studies": [
            {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for path in study_paths
        ],
        "case_count": len(cases),
        "independent_revision_count": sum(
            len(case["independent_revisions"]) for case in cases
        ),
        "post_reveal_revision_count": sum(
            len(case["post_reveal_revisions_separate"]) for case in cases
        ),
        "cases": cases,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def _point_xy(point: dict[str, float], width: int, height: int) -> tuple[float, float]:
    """Convert normalized screenshot coordinates to image pixels."""
    return point["x"] * width, point["y"] * height


def render_comparison(
    case: dict[str, Any],
    human_path: Path,
    result: dict[str, Any],
    comparison_results: Path,
    output: Path,
) -> None:
    """Render paint overlay with independent human and generated lines."""
    image_path = Path(case["frame_path"])
    if sha256_file(image_path) != case["frame_sha256"]:
        raise ValueError(f"Source frame hash mismatch: {case['case_id']}")
    overlay_path = comparison_results / result["overlay_filename"]
    if not overlay_path.is_file() or result["source_sha256"] != case["frame_sha256"]:
        raise ValueError(
            f"Generated result is not an exact-frame match: {case['case_id']}"
        )
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    paint = cv2.imread(str(overlay_path), cv2.IMREAD_COLOR)
    if image is None or paint is None:
        raise ValueError("Could not load source or paint overlay image")
    if image.shape != paint.shape:
        raise ValueError("Paint overlay dimensions differ from the clean source")
    image = cv2.addWeighted(image, 0.72, paint, 0.28, 0)
    height, width = image.shape[:2]
    human = json.loads(human_path.read_text(encoding="utf-8"))
    for shape in human["shapes"]:
        points = np.array(
            [_point_xy(point, width, height) for point in shape["points"]],
            dtype=np.int32,
        )
        if shape["kind"] == "boundary":
            cv2.polylines(image, [points], False, (255, 255, 0), 8, cv2.LINE_AA)
        elif shape["kind"] == "contested":
            cv2.polylines(image, [points], True, (0, 210, 255), 5, cv2.LINE_AA)
        else:
            cv2.polylines(image, [points], True, (255, 255, 255), 5, cv2.LINE_AA)
    for segment in result.get("line_segments") or [result.get("line", [])]:
        points = np.array(
            [_point_xy(point, width, height) for point in segment], dtype=np.int32
        )
        if len(points) >= 2:
            cv2.polylines(image, [points], False, (70, 45, 255), 6, cv2.LINE_AA)
    cv2.rectangle(image, (15, 15), (680, 80), (20, 20, 20), -1)
    cv2.putText(
        image,
        "Cyan: independent human boundary  Red: paint-only",
        (28, 42),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        f"{case['case_id']} | {case['video_time']:.3f}s",
        (28, 67),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.62,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), image):
        raise OSError(f"Could not write comparison visualization: {output}")


def render_no_line_reference(case: dict[str, Any], output: Path) -> None:
    """Show the human no-line frame without fabricating a model prediction."""
    source = Path(case["frame_path"])
    if sha256_file(source) != case["frame_sha256"]:
        raise ValueError("No-line reference frame hash mismatch")
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Could not read no-line reference frame")
    cv2.rectangle(image, (15, 15), (700, 80), (20, 20, 20), -1)
    cv2.putText(
        image,
        "Human judgment: no meaningful line",
        (28, 43),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        "No exact-frame paint-only prediction available",
        (28, 68),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.62,
        (220, 220, 220),
        1,
        cv2.LINE_AA,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), image):
        raise OSError(f"Could not write no-line reference: {output}")


def main() -> None:
    """Freeze annotations and render exact-hash Phase 1 comparisons."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("comparison_study", type=Path)
    parser.add_argument("comparison_results", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--additional-study", type=Path, action="append", default=[])
    args = parser.parse_args()
    studies = [args.study, *args.additional_study]
    manifest = freeze_annotations(studies, args.output / "baseline_snapshot")
    queue = json.loads(args.study.read_text(encoding="utf-8"))["cases"]
    phase1 = json.loads(args.comparison_study.read_text(encoding="utf-8"))
    results = {
        row["case_id"]: row
        for row in json.loads((args.comparison_results / "results.json").read_text())
    }
    phase1_cases = {row["case_id"]: row for row in phase1["cases"]}
    comparisons = []
    for case in queue:
        key = case.get("comparator_key")
        candidate = phase1_cases.get(key) if key else None
        result = results.get(key) if key else None
        exact = bool(
            candidate
            and result
            and candidate.get("expected_clean_sha256") == case["frame_sha256"]
            and result.get("source_sha256") == case["frame_sha256"]
            and result.get("status") == "available"
            and result.get("source_recording_sha256")
            == case["recording_sha256"]
        )
        row: dict[str, Any] = {
            "case_id": case["case_id"],
            "human_independent_revisions": next(
                entry["independent_revisions"]
                for entry in manifest["cases"]
                if entry["case_id"] == case["case_id"]
            ),
            "comparator_key": key,
            "exact_frame_comparison_available": exact,
        }
        if exact and result is not None:
            human_path = (
                args.output
                / "baseline_snapshot"
                / "independent"
                / case["case_id"]
                / "independent.r001.json"
            )
            render_comparison(
                case,
                human_path,
                result,
                args.comparison_results,
                args.output / "visuals" / f"{case['case_id']}.png",
            )
            row["generated_source_sha256"] = result["source_sha256"]
            row["source_recording_sha256"] = result["source_recording_sha256"]
            row["polygon_sha256"] = result["polygon_sha256"]
            row["classifier_config_sha256"] = result["classifier_config_sha256"]
            row["generated_line_segments"] = result.get("line_segments") or [
                result.get("line", [])
            ]
            row["visual_path"] = str(
                (args.output / "visuals" / f"{case['case_id']}.png").resolve()
            )
        elif case["case_id"] == "m04_late":
            render_no_line_reference(
                case, args.output / "visuals" / "m04_late_no_line.png"
            )
            row["visual_path"] = str(
                (args.output / "visuals" / "m04_late_no_line.png").resolve()
            )
            row["comparison_limitation"] = "no_exact_frame_prediction"
        comparisons.append(row)
    (args.output / "comparison_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "human_baseline_manifest": str(
                    (args.output / "baseline_snapshot" / "manifest.json").resolve()
                ),
                "phase1_study_sha256": sha256_file(args.comparison_study),
                "phase1_results_sha256": sha256_file(
                    args.comparison_results / "results.json"
                ),
                "comparisons": comparisons,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"Frozen {manifest['case_count']} cases / "
        f"{manifest['independent_revision_count']} independent revisions / "
        f"{manifest['post_reveal_revision_count']} separate post-reveal revisions"
    )
    exact_count = sum(row["exact_frame_comparison_available"] for row in comparisons)
    print(f"Exact frame comparisons: {exact_count}")


if __name__ == "__main__":
    main()
