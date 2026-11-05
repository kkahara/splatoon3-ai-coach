"""Isolated paint-only frontline experiment over explicitly declared source images."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from pydantic import BaseModel, Field

from splatoon3_ai_coach.config.models import MapInkAnalyzerConfig
from splatoon3_ai_coach.vision.map_ink import MapInkClassifier
from splatoon3_ai_coach.vision.overlay_map_polygon import (
    load_overlay_map_polygon,
    overlay_polygon_sha256,
)
from splatoon3_ai_coach.vision.team_color_calibration import TeamColorCalibrationResult


class Point(BaseModel):
    """Normalized screenshot coordinates."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class ManualRegistration(BaseModel):
    """Four reviewed correspondences from overlay-map to screenshot image."""

    overlay_points: list[Point] = Field(min_length=4, max_length=4)
    screenshot_points: list[Point] = Field(min_length=4, max_length=4)
    back: Point
    center: Point
    front: Point
    reviewed: bool = False


class ColorBinding(BaseModel):
    """Explicitly reviewed team-color calibration for the same source match."""

    ally_h: float = Field(ge=0, lt=180)
    opponent_h: float = Field(ge=0, lt=180)
    source: str
    reviewed_same_match: bool = False


class Case(BaseModel):
    """One declared Tower image; the orientation reference has no line label."""

    case_id: str
    role: Literal["orientation", "frontline"]
    clean_image: Path
    annotated_image: Path | None = None
    stage_id: str
    stage_polygon: Path
    registration: ManualRegistration
    colors: ColorBinding
    source_recording: str
    source_recording_sha256: str
    expected_clean_sha256: str
    video_time: float | None = None
    human_line: list[Point] = Field(default_factory=list)


class CaseResult(BaseModel):
    """Generated evidence and limitations for a single image."""

    schema_version: Literal[1] = 1
    case_id: str
    status: Literal["available", "unavailable"]
    reasons: list[str] = Field(default_factory=list)
    source_sha256: str | None = None
    source_recording_sha256: str | None = None
    source_recording_hash_verified: bool = False
    polygon_sha256: str | None = None
    classifier_config_sha256: str | None = None
    calibration: ColorBinding | None = None
    registration: ManualRegistration | None = None
    line: list[Point] = Field(default_factory=list)
    line_segments: list[list[Point]] = Field(default_factory=list)
    overlay_filename: str | None = None
    uncertainty_filename: str | None = None
    classified_fraction: float | None = None


def sha256_file(path: Path) -> str:
    """Hash the exact source bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate_case(case: Case, config: MapInkAnalyzerConfig, out: Path) -> CaseResult:
    """Classify paint and estimate a provisional image-plane line."""
    reasons = _preflight(case)
    result = CaseResult(case_id=case.case_id, status="unavailable", reasons=reasons)
    if reasons:
        return result
    source = cv2.imread(str(case.clean_image), cv2.IMREAD_COLOR)
    assert source is not None
    polygon = load_overlay_map_polygon(case.stage_polygon)
    classifier = MapInkClassifier(config)
    colors = case.colors
    classifier.apply_calibration(
        TeamColorCalibrationResult(
            ally_h=colors.ally_h,
            opponent_h=colors.opponent_h,
            separation_degrees=abs(colors.ally_h - colors.opponent_h),
            calibrated_at=case.video_time or 0,
            source=colors.source,
        )
    )
    ally, opponent, _ = classifier.classify_bgr(source)
    region = _registered_polygon_mask(
        source.shape[:2], polygon.polygon, case.registration
    )
    ally &= region
    opponent &= region
    segments, uncertain = _estimate_line(ally, opponent, region, case.registration)
    line = [point for segment in segments for point in segment]
    overlay = _paint_overlay(source, ally, opponent, region)
    out.mkdir(parents=True, exist_ok=True)
    overlay_name = f"{case.case_id}.paint.png"
    uncertainty_name = f"{case.case_id}.uncertainty.png"
    cv2.imwrite(str(out / overlay_name), overlay)
    cv2.imwrite(str(out / uncertainty_name), uncertain)
    return CaseResult(
        case_id=case.case_id,
        status="available" if line else "unavailable",
        reasons=[] if line else ["no_supported_ally_opponent_crossing"],
        source_sha256=sha256_file(case.clean_image),
        source_recording_sha256=case.source_recording_sha256,
        source_recording_hash_verified=True,
        polygon_sha256=overlay_polygon_sha256(polygon),
        classifier_config_sha256=hashlib.sha256(
            config.model_dump_json().encode()
        ).hexdigest(),
        calibration=colors,
        registration=case.registration,
        line=line,
        line_segments=segments,
        overlay_filename=overlay_name,
        uncertainty_filename=uncertainty_name,
        classified_fraction=float(
            np.count_nonzero(ally | opponent) / max(1, np.count_nonzero(region))
        ),
    )


def _preflight(case: Case) -> list[str]:
    reasons = []
    if not case.clean_image.is_file():
        reasons.append("clean_source_missing")
    if not case.stage_polygon.is_file():
        reasons.append("stage_polygon_missing")
    if not case.registration.reviewed:
        reasons.append("registration_unreviewed")
    if not case.colors.reviewed_same_match:
        reasons.append("calibration_match_unverified")
    if not case.source_recording:
        reasons.append("recording_identity_missing")
    elif not Path(case.source_recording).is_file():
        reasons.append("recording_missing")
    elif sha256_file(Path(case.source_recording)) != case.source_recording_sha256:
        reasons.append("recording_hash_mismatch")
    if (
        case.clean_image.is_file()
        and sha256_file(case.clean_image) != case.expected_clean_sha256
    ):
        reasons.append("clean_source_hash_mismatch")
    if not reasons and cv2.imread(str(case.clean_image)) is None:
        reasons.append("clean_source_unreadable")
    if (
        not reasons
        and load_overlay_map_polygon(case.stage_polygon).stage_id != case.stage_id
    ):
        reasons.append("polygon_stage_mismatch")
    return reasons


def load_cases(path: Path) -> list[Case]:
    """Expand shared, versioned study metadata into validated case records."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported frontline study schema")
    shared = payload["shared"]
    cases = [Case.model_validate({**shared, **item}) for item in payload["cases"]]
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("Duplicate frontline case IDs")
    if sum(case.role == "orientation" for case in cases) != 1:
        raise ValueError("Study requires exactly one orientation reference")
    return cases


def _registered_polygon_mask(
    shape: tuple[int, int],
    polygon: list[tuple[float, float]],
    registration: ManualRegistration,
) -> np.ndarray:
    height, width = shape
    source = np.float32([[p.x, p.y] for p in registration.overlay_points])
    target = np.float32([[p.x, p.y] for p in registration.screenshot_points])
    homography = cv2.getPerspectiveTransform(source, target)
    if not np.isfinite(homography).all() or abs(np.linalg.det(homography)) < 1e-8:
        raise ValueError("Degenerate manual map registration")
    points = np.asarray(polygon, dtype=np.float32).reshape(-1, 1, 2)
    mapped = cv2.perspectiveTransform(points, homography).reshape(-1, 2)
    if not np.isfinite(mapped).all():
        raise ValueError("Invalid registered polygon coordinates")
    pixels = np.rint(mapped * [width, height]).astype(np.int32)
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [pixels], 255)
    return mask > 0


def _estimate_line(
    ally: np.ndarray,
    opponent: np.ndarray,
    region: np.ndarray,
    registration: ManualRegistration,
) -> tuple[list[list[Point]], np.ndarray]:
    height, width = ally.shape
    signed = ally.astype(np.float32) - opponent.astype(np.float32)
    support = (ally | opponent).astype(np.float32)
    numerator = cv2.GaussianBlur(signed, (0, 0), 18)
    denominator = cv2.GaussianBlur(support, (0, 0), 18)
    field = np.divide(
        numerator, denominator, out=np.zeros_like(numerator), where=denominator > 0.15
    )
    unknown = (denominator <= 0.15) | (np.abs(field) < 0.18)
    back = np.array([registration.back.x, registration.back.y])
    front = np.array([registration.front.x, registration.front.y])
    direction = front - back
    norm = float(np.linalg.norm(direction))
    if norm < 0.1:
        return [], _uncertainty_overlay(unknown & region)
    direction /= norm
    normal = np.array([-direction[1], direction[0]])
    center = np.array([registration.center.x, registration.center.y])
    segments: list[list[Point]] = []
    current: list[Point] = []
    for lane in np.linspace(-0.46, 0.46, 55):
        origin = center + normal * lane
        candidates = origin + np.linspace(-0.6, 0.6, 121)[:, None] * direction
        px = np.rint(candidates[:, 0] * (width - 1)).astype(int)
        py = np.rint(candidates[:, 1] * (height - 1)).astype(int)
        valid = (px >= 0) & (px < width) & (py >= 0) & (py < height)
        crossings = _crossings(field, denominator, px, py, valid)
        if crossings:
            index = min(crossings, key=lambda k: abs(k - 60))
            current.append(
                Point(x=float(candidates[index, 0]), y=float(candidates[index, 1]))
            )
        elif current:
            if len(current) >= 2:
                segments.append(current)
            current = []
    if len(current) >= 2:
        segments.append(current)
    return segments, _uncertainty_overlay(unknown & region)


def _uncertainty_overlay(mask: np.ndarray) -> np.ndarray:
    """Encode weak support as transparent yellow, confined to the stage polygon."""
    overlay = np.zeros((*mask.shape, 4), dtype=np.uint8)
    overlay[mask] = (20, 205, 255, 190)
    return overlay


def _crossings(
    field: np.ndarray,
    support: np.ndarray,
    px: np.ndarray,
    py: np.ndarray,
    valid: np.ndarray,
) -> list[int]:
    crossings = []
    for i in range(1, len(px)):
        if not valid[i - 1] or not valid[i]:
            continue
        if support[py[i - 1], px[i - 1]] < 0.25 or support[py[i], px[i]] < 0.25:
            continue
        if field[py[i - 1], px[i - 1]] * field[py[i], px[i]] < 0:
            crossings.append(i)
    return crossings


def _paint_overlay(
    source: np.ndarray, ally: np.ndarray, opponent: np.ndarray, region: np.ndarray
) -> np.ndarray:
    overlay = source.copy()
    overlay[ally] = (overlay[ally] * 0.35 + np.array([40, 210, 50]) * 0.65).astype(
        np.uint8
    )
    overlay[opponent] = (
        overlay[opponent] * 0.35 + np.array([190, 70, 210]) * 0.65
    ).astype(np.uint8)
    contours, _ = cv2.findContours(
        region.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    cv2.drawContours(overlay, contours, -1, (0, 255, 255), 2)
    return overlay


def write_results(
    cases: list[Case], config: MapInkAnalyzerConfig, out: Path
) -> list[CaseResult]:
    """Write only new experimental derivatives, preserving every original file."""
    if (out / "results.json").exists():
        raise FileExistsError("Experiment results already exist")
    results = [evaluate_case(case, config, out) for case in cases]
    (out / "results.json").write_text(
        json.dumps([result.model_dump(mode="json") for result in results], indent=2)
        + "\n",
        encoding="utf-8",
    )
    return results
