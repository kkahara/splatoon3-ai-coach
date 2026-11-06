"""Run an existing DBNet model on pinned development frames for visual review."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from pydantic import BaseModel, Field, model_validator


class TextRegion(BaseModel):
    """One detected text-shaped region in source-image pixel coordinates."""

    polygon_px: list[tuple[int, int]] = Field(min_length=4, max_length=4)
    confidence: float = Field(ge=0.0, le=1.0)


class FrameTextResults(BaseModel):
    """Text-region outputs tied to one exact development source frame."""

    frame_id: str
    video_time_seconds: float
    source_frame_sha256: str
    detections: list[TextRegion]
    preview_path: str


class DBNetParameters(BaseModel):
    """Fixed OpenCV demo settings used without development-set tuning."""

    binary_threshold: float
    polygon_threshold: float
    max_candidates: int
    unclip_ratio: float
    input_size: tuple[int, int]


class TextModelProvenance(BaseModel):
    """Model identity and inference settings for reproducibility."""

    name: str
    weights_sha256: str
    published_language_scope: str
    opencv_version: str
    parameters: DBNetParameters


class DBNetInvestigation(BaseModel):
    """Versioned, development-only DBNet result artifact."""

    schema_version: Literal[1]
    investigation_id: str
    source_manifest_sha256: str
    model: TextModelProvenance
    split: Literal["development_only"]
    frame_count: int
    frames: list[FrameTextResults]
    limitations: list[str]

    @model_validator(mode="after")
    def check_frame_count(self) -> "DBNetInvestigation":
        """Ensure the recorded count agrees with the artifact contents."""
        if self.frame_count != len(self.frames):
            raise ValueError("frame_count does not match result records")
        if len({frame.frame_id for frame in self.frames}) != len(self.frames):
            raise ValueError("duplicate frame_id in investigation results")
        return self


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file's exact bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def make_dbnet(model_path: Path) -> cv2.dnn_TextDetectionModel_DB:
    """Load the documented OpenCV DBNet configuration without tuning."""
    model = cv2.dnn_TextDetectionModel_DB(str(model_path))
    model.setBinaryThreshold(0.3)
    model.setPolygonThreshold(0.5)
    model.setMaxCandidates(200)
    model.setUnclipRatio(2.0)
    model.setInputParams(
        1.0 / 255.0,
        (736, 736),
        (122.67891434, 116.66876762, 104.00698793),
        True,
    )
    return model


def polygon_record(polygon: np.ndarray) -> list[list[int]]:
    """Convert one detector polygon to JSON-safe integer coordinates."""
    return [[int(point[0]), int(point[1])] for point in polygon]


def annotate_preview(image: np.ndarray, detections: list[dict]) -> np.ndarray:
    """Draw detector regions on a copy for the browser review page."""
    preview = image.copy()
    for item in detections:
        points = np.asarray(item["polygon_px"], dtype=np.int32)
        cv2.polylines(preview, [points], True, (255, 220, 40), 2)
        x, y = points[0]
        cv2.putText(
            preview,
            f"DB {item['confidence']:.2f}",
            (int(x), max(20, int(y) - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 220, 40),
            1,
            cv2.LINE_AA,
        )
    return preview


def evaluate_frame(
    model: cv2.dnn_TextDetectionModel_DB,
    frame: dict,
    study_dir: Path,
    preview_dir: Path,
) -> dict:
    """Verify one pinned image, infer text regions, and save its preview."""
    image_path = study_dir / frame["image_path"]
    actual_hash = sha256_file(image_path)
    if actual_hash != frame["image_sha256"]:
        raise ValueError(f"source frame hash mismatch: {frame['frame_id']}")
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"source frame unreadable: {frame['frame_id']}")
    polygons, confidences = model.detect(image)
    detections = [
        {"polygon_px": polygon_record(poly), "confidence": float(score)}
        for poly, score in zip(polygons, confidences, strict=True)
    ]
    preview = annotate_preview(image, detections)
    preview_path = preview_dir / f"{frame['frame_id']}.dbnet.png"
    if not cv2.imwrite(str(preview_path), preview):
        raise OSError(f"could not write preview: {preview_path}")
    return {
        "frame_id": frame["frame_id"],
        "video_time_seconds": frame["video_time_seconds"],
        "source_frame_sha256": actual_hash,
        "detections": detections,
        "preview_path": preview_path.name,
    }


def build_artifact(study_dir: Path, model_path: Path, output_dir: Path) -> dict:
    """Evaluate development-only frames and return a versioned result record."""
    manifest_path = study_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    frames = [item for item in manifest["frames"] if item["split"] == "development"]
    if any(item["split"] != "development" for item in frames):
        raise ValueError("non-development frame selected")
    preview_dir = output_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    model = make_dbnet(model_path)
    return {
        "schema_version": 1,
        "investigation_id": "m08_dbnet_text_regions_visual_v1",
        "source_manifest_sha256": sha256_file(manifest_path),
        "model": {
            "name": "OpenCV DBNet TD500 ResNet-18",
            "weights_sha256": sha256_file(model_path),
            "published_language_scope": "English and Chinese",
            "opencv_version": cv2.__version__,
            "parameters": {
                "binary_threshold": 0.3,
                "polygon_threshold": 0.5,
                "max_candidates": 200,
                "unclip_ratio": 2.0,
                "input_size": [736, 736],
            },
        },
        "split": "development_only",
        "frame_count": len(frames),
        "frames": [
            evaluate_frame(model, frame, study_dir, preview_dir) for frame in frames
        ],
        "limitations": [
            "Text-region detections are not recognized names or player identities.",
            "The model was not trained for Japanese coverage.",
            "No threshold tuning or post-filtering was applied.",
        ],
    }


def main() -> None:
    """Run the read-only development-frame DBNet investigation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    artifact = DBNetInvestigation.model_validate(
        build_artifact(args.study_dir, args.model, args.output_dir)
    )
    target = args.output_dir / "dbnet_text_regions.json"
    target.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target} ({artifact.frame_count} development frames)")


if __name__ == "__main__":
    main()
