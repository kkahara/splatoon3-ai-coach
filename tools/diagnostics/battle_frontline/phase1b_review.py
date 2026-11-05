"""Blind, human-first boundary annotation workflow for Battle Frontline Phase 1B."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from tools.diagnostics.battle_frontline.experiment import Point, sha256_file

HOST = "127.0.0.1"
MAX_BODY = 256 * 1024


class QueueCase(BaseModel):
    """Hash-pinned clean frame in the review queue."""

    case_id: str
    recording_index: int
    recording_path: str
    recording_filename: str
    recording_sha256: str
    video_time: float
    selection_slot: str
    stage_id: str | None = None
    mode_id: str | None = None
    stage_identity_status: str = "unverified"
    frame_path: str
    frame_sha256: str
    comparator_key: str | None = None


class AnnotationShape(BaseModel):
    """A human boundary polyline or marked region in normalized image space."""

    kind: Literal["boundary", "contested", "uncertain"]
    points: list[Point] = Field(min_length=2)

    @model_validator(mode="after")
    def regions_need_three_vertices(self) -> AnnotationShape:
        """Require polygon shapes to contain at least three distinct vertices."""
        if self.kind != "boundary" and len(self.points) < 3:
            raise ValueError("Contested and uncertain regions need three points")
        return self


class ComparisonAssessment(BaseModel):
    """Reviewer response to each revealed candidate, kept separate from labels."""

    provisional_sketch: Literal["accept", "reject", "unsure"]
    paint_prediction: Literal["accept", "reject", "unsure"]


class HumanJudgment(BaseModel):
    """Immutable, revisioned human-only or post-reveal annotation."""

    schema_version: Literal[1] = 1
    case_id: str
    revision: int = Field(ge=1)
    phase: Literal["independent", "post_reveal"]
    judgment: Literal["boundary", "no_meaningful_line", "insufficient_evidence"]
    shapes: list[AnnotationShape] = Field(default_factory=list)
    reasons: list[
        Literal[
            "player_positions",
            "ink_coverage",
            "active_confrontation",
            "terrain",
            "recent_movement",
        ]
    ] = Field(default_factory=list)
    comparison_assessment: ComparisonAssessment | None = None
    explanation: str = Field(default="", max_length=3000)
    source_frame_sha256: str
    source_recording_sha256: str
    source_frame_hash_verified_at_save: bool
    source_recording_hash_verified_at_save: bool
    video_time: float
    stage_id: str | None = None
    independent_baseline_filename: str | None = None
    independent_baseline_sha256: str | None = None
    reveal_filename: str | None = None
    created_at: str

    @model_validator(mode="after")
    def boundary_judgment_has_line(self) -> HumanJudgment:
        """Require a line only when the reviewer says a boundary is present."""
        lines = [shape for shape in self.shapes if shape.kind == "boundary"]
        if self.judgment == "boundary" and not lines:
            raise ValueError("Boundary judgment requires at least one line")
        if self.judgment == "no_meaningful_line" and lines:
            raise ValueError("No-meaningful-line judgment cannot contain a boundary")
        return self
class RevealRecord(BaseModel):
    """Audit record proving that the independent response preceded comparison."""

    schema_version: Literal[1] = 1
    case_id: str
    independent_baseline_filename: str
    independent_baseline_sha256: str
    comparator_available: bool
    comparator_case_key: str | None = None
    created_at: str


def _read_json(path: Path) -> Any:
    """Read one local JSON document."""
    return json.loads(path.read_text(encoding="utf-8"))


def _write_new_json(path: Path, payload: BaseModel) -> None:
    """Create an immutable versioned JSON file without replacing history."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(payload.model_dump_json(indent=2) + "\n")


def _records(directory: Path, prefix: str) -> list[Path]:
    """Return naturally ordered versioned records for a case."""
    return sorted(directory.glob(f"{prefix}.r*.json"))


def _latest_record(directory: Path, prefix: str) -> Path | None:
    """Return the latest immutable record, if present."""
    records = _records(directory, prefix)
    return records[-1] if records else None


def _next_record(directory: Path, prefix: str) -> tuple[int, Path]:
    """Choose the next revision number without overwriting prior judgments."""
    revision = len(_records(directory, prefix)) + 1
    return revision, directory / f"{prefix}.r{revision:03d}.json"


def _decision_summary(directory: Path, prefix: str) -> list[dict[str, Any]]:
    """Expose saved judgments for revisiting without exposing comparisons."""
    rows = []
    for path in _records(directory, prefix):
        payload = _read_json(path)
        rows.append(
            {
                "filename": path.name,
                "phase": payload["phase"],
                "judgment": payload["judgment"],
                "explanation": payload["explanation"],
            }
        )
    return rows


def _load_comparison(
    case: QueueCase, comparison_study: Path | None, comparison_results: Path | None
) -> dict[str, Any] | None:
    """Resolve an exact-frame Phase 1 comparison, rejecting mismatched sources."""
    if (
        case.comparator_key is None
        or comparison_study is None
        or comparison_results is None
    ):
        return None
    source_cases = {
        item["case_id"]: item for item in _read_json(comparison_study)["cases"]
    }
    source = source_cases.get(case.comparator_key)
    if (
        source is None
        or source["expected_clean_sha256"] != case.frame_sha256
        or sha256_file(Path(source["clean_image"])) != case.frame_sha256
    ):
        return None
    results = {
        item["case_id"]: item for item in _read_json(comparison_results / "results.json")
    }
    result = results.get(case.comparator_key)
    if (
        result is None
        or result.get("source_sha256") != case.frame_sha256
        or result.get("status") != "available"
    ):
        return None
    return {
        "case_id": case.case_id,
        "source_frame_sha256": case.frame_sha256,
        "provisional_reference_line": source.get("human_line", []),
        "generated_line_segments": result.get("line_segments")
        or [result.get("line", [])],
        "paint_image_available": bool(result.get("overlay_filename")),
        "uncertainty_image_available": bool(result.get("uncertainty_filename")),
        "polygon_sha256": result.get("polygon_sha256"),
        "classifier_config_sha256": result.get("classifier_config_sha256"),
        "calibration": result.get("calibration"),
    }


def _load_study_cases(study_path: Path) -> list[QueueCase]:
    """Validate either the complete queue or a small human review batch."""
    payload = _read_json(study_path)
    workflow = payload.get("workflow")
    if workflow not in {
        "human_boundary_phase1b",
        "human_boundary_phase1b_batch",
    }:
        raise ValueError("Not a Phase 1B human-boundary study")
    cases = [QueueCase.model_validate(item) for item in payload["cases"]]
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("Phase 1B study cases must have unique identifiers")
    if workflow == "human_boundary_phase1b":
        if len(cases) != 20:
            raise ValueError("Full Phase 1B study must contain 20 queue cases")
        if {case.recording_index for case in cases} != set(range(1, 17)):
            raise ValueError("Full Phase 1B queue must cover all 16 recordings")
    elif len(cases) < 1 or len(cases) > 5:
        raise ValueError("A Phase 1B review batch must contain 1–5 cases")
    return cases


def make_handler(
    cases: list[QueueCase],
    output_dir: Path,
    comparison_study: Path | None = None,
    comparison_results: Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Build local handler with blind baseline and gated reveal endpoints."""
    by_id = {case.case_id: case for case in cases}
    ordered: dict[int, list[QueueCase]] = {}
    for case in cases:
        ordered.setdefault(case.recording_index, []).append(case)
    for rows in ordered.values():
        rows.sort(key=lambda item: item.video_time)
    positions = {
        case.case_id: (recording_index, index)
        for recording_index, rows in ordered.items()
        for index, case in enumerate(rows)
    }
    annotation_dir = output_dir / "annotations"
    reveal_dir = output_dir / "reveals"
    page = Path(__file__).with_name("review_phase1b.html")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _json(self, status: int, value: Any) -> None:
            self._send(status, json.dumps(value).encode(), "application/json")

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                return self._send(200, page.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/cases":
                return self._json(
                    200,
                    [self._case_summary(case) for case in cases],
                )
            parts = self.path.split("/")
            if len(parts) == 4 and parts[1:3] == ["api", "comparison"]:
                return self._comparison(parts[3])
            if len(parts) == 4 and parts[1:3] == ["api", "frame"]:
                return self._frame(parts[3])
            if len(parts) == 5 and parts[1:3] == ["api", "record"]:
                return self._record(parts[3], parts[4])
            if len(parts) == 5 and parts[1:3] == ["api", "revealed-image"]:
                return self._revealed_image(parts[3], parts[4])
            if len(parts) == 4 and parts[1:3] == ["api", "decision"]:
                return self._download_decision(parts[2], parts[3])
            self._json(404, {"error": "not_found"})

        def _case_summary(self, case: QueueCase) -> dict[str, Any]:
            independent = _decision_summary(annotation_dir / case.case_id, "independent")
            post = _decision_summary(annotation_dir / case.case_id, "post_reveal")
            recording_cases = ordered[case.recording_index]
            index = positions[case.case_id][1]
            return {
                "id": case.case_id,
                "recording_index": case.recording_index,
                "recording_filename": case.recording_filename,
                "video_time": case.video_time,
                "selection_slot": case.selection_slot,
                "frame_sha256": case.frame_sha256,
                "stage_identity_status": case.stage_identity_status,
                "has_independent": bool(independent),
                "has_reveal": bool(_records(reveal_dir, case.case_id)),
                "independent_history": independent,
                "post_reveal_history": post,
                "previous_case": (
                    recording_cases[index - 1].case_id if index > 0 else None
                ),
                "next_case": (
                    recording_cases[index + 1].case_id
                    if index + 1 < len(recording_cases)
                    else None
                ),
            }

        def _frame(self, case_id: str) -> None:
            case = by_id.get(case_id)
            if case is None:
                return self._json(404, {"error": "case_not_found"})
            path = Path(case.frame_path)
            if sha256_file(path) != case.frame_sha256:
                return self._json(409, {"error": "frame_hash_mismatch"})
            return self._send(200, path.read_bytes(), "image/png")

        def _record(self, case_id: str, filename: str) -> None:
            if case_id not in by_id or Path(filename).name != filename:
                return self._json(404, {"error": "record_not_found"})
            path = annotation_dir / case_id / filename
            if not path.is_file():
                return self._json(404, {"error": "record_not_found"})
            return self._send(200, path.read_bytes(), "application/json")

        def _revealed_image(self, case_id: str, image_kind: str) -> None:
            if case_id not in by_id:
                return self._json(404, {"error": "case_not_found"})
            if not _records(reveal_dir, case_id):
                return self._json(403, {"error": "reveal_comparison_first"})
            comparison = _load_comparison(
                by_id[case_id], comparison_study, comparison_results
            )
            if comparison is None:
                return self._json(404, {"error": "comparison_unavailable"})
            if not _records(reveal_dir, case_id):
                return self._json(403, {"error": "reveal_comparison_first"})
            source = {
                "paint": "overlay_filename",
                "uncertainty": "uncertainty_filename",
            }.get(image_kind)
            result = next(
                item
                for item in _read_json(comparison_results / "results.json")  # type: ignore[operator]
                if item["case_id"] == by_id[case_id].comparator_key
            )
            filename = result.get(source) if source else None
            if filename is None or Path(filename).name != filename:
                return self._json(404, {"error": "comparison_image_unavailable"})
            image_path = comparison_results / filename  # type: ignore[operator]
            return self._send(200, image_path.read_bytes(), "image/png")

        def _comparison(self, case_id: str) -> None:
            if case_id not in by_id:
                return self._json(404, {"error": "case_not_found"})
            if not _records(reveal_dir, case_id):
                return self._json(403, {"error": "save_independent_annotation_first"})
            comparison = _load_comparison(
                by_id[case_id], comparison_study, comparison_results
            )
            if comparison is None:
                return self._json(
                    200,
                    {
                        "available": False,
                        "message": (
                            "No exact-frame provisional sketch or paint-only "
                            "result is available for this recording frame."
                        ),
                    },
                )
            return self._json(200, {"available": True, **comparison})

        def _download_decision(self, case_id: str, filename: str) -> None:
            if case_id not in by_id or Path(filename).name != filename:
                return self._json(404, {"error": "decision_not_found"})
            path = annotation_dir / case_id / filename
            if not path.is_file():
                return self._json(404, {"error": "decision_not_found"})
            return self._send(200, path.read_bytes(), "application/json")

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in {"/api/decision", "/api/reveal"}:
                return self._json(404, {"error": "not_found"})
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY:
                return self._json(413, {"error": "invalid_body_size"})
            try:
                body = json.loads(self.rfile.read(length))
                case = by_id[body["case_id"]]
                if self.path == "/api/reveal":
                    return self._reveal(case)
                return self._save_decision(case, body)
            except (KeyError, TypeError, ValueError) as exc:
                return self._json(400, {"error": str(exc)})

        def _verify_sources(self, case: QueueCase) -> None:
            """Refuse annotations if frame or recording identity changed."""
            if sha256_file(Path(case.frame_path)) != case.frame_sha256:
                raise ValueError("Frame hash changed since queue creation")
            recording = Path(case.recording_path)
            if not recording.is_file() or sha256_file(recording) != case.recording_sha256:
                raise ValueError("Recording hash changed since inventory")

        def _save_decision(self, case: QueueCase, body: dict[str, Any]) -> None:
            phase = body["phase"]
            directory = annotation_dir / case.case_id
            independent = _latest_record(directory, "independent")
            reveal = _latest_record(reveal_dir, case.case_id)
            if phase == "independent" and reveal is not None:
                return self._json(
                    409, {"error": "independent_judgment_locked_after_reveal"}
                )
            if phase == "post_reveal" and (independent is None or reveal is None):
                return self._json(409, {"error": "save_independent_and_reveal_first"})
            comparison = _load_comparison(case, comparison_study, comparison_results)
            assessment = body.get("comparison_assessment")
            if phase == "post_reveal" and comparison is not None and assessment is None:
                return self._json(400, {"error": "comparison_assessment_required"})
            if phase == "independent":
                assessment = None
            if comparison is None:
                assessment = None
            self._verify_sources(case)
            revision, destination = _next_record(directory, phase)
            baseline_hash = sha256_file(independent) if independent else None
            decision = HumanJudgment(
                case_id=case.case_id,
                revision=revision,
                phase=phase,
                judgment=body["judgment"],
                shapes=body.get("shapes", []),
                reasons=body.get("reasons", []),
                comparison_assessment=assessment,
                explanation=body.get("explanation", ""),
                source_frame_sha256=case.frame_sha256,
                source_recording_sha256=case.recording_sha256,
                source_frame_hash_verified_at_save=True,
                source_recording_hash_verified_at_save=True,
                video_time=case.video_time,
                stage_id=case.stage_id,
                independent_baseline_filename=independent.name
                if phase == "post_reveal" and independent
                else None,
                independent_baseline_sha256=baseline_hash
                if phase == "post_reveal"
                else None,
                reveal_filename=reveal.name
                if phase == "post_reveal" and reveal
                else None,
                created_at=datetime.now(UTC).isoformat(),
            )
            _write_new_json(destination, decision)
            self._json(200, {"ok": True, "filename": destination.name})

        def _reveal(self, case: QueueCase) -> None:
            directory = annotation_dir / case.case_id
            independent = _latest_record(directory, "independent")
            if independent is None:
                return self._json(409, {"error": "save_independent_annotation_first"})
            self._verify_sources(case)
            comparison = _load_comparison(case, comparison_study, comparison_results)
            revision, destination = _next_record(reveal_dir, case.case_id)
            record = RevealRecord(
                case_id=case.case_id,
                independent_baseline_filename=independent.name,
                independent_baseline_sha256=sha256_file(independent),
                comparator_available=comparison is not None,
                comparator_case_key=case.comparator_key if comparison else None,
                created_at=datetime.now(UTC).isoformat(),
            )
            _write_new_json(destination, record)
            payload: dict[str, Any] = {
                "ok": True,
                "filename": destination.name,
                "comparison": comparison,
            }
            if comparison is None:
                payload["comparison_message"] = (
                    "No exact-frame provisional sketch or paint-only result is "
                    "available for this recording frame."
                )
            return self._json(200, payload)

    return Handler


def serve(
    study_path: Path,
    output_dir: Path,
    port: int,
    comparison_study: Path | None = None,
    comparison_results: Path | None = None,
) -> None:
    """Serve the blind Phase 1B review workflow on loopback."""
    cases = _load_study_cases(study_path)
    if comparison_study is None:
        comparison_results = None
    server = ThreadingHTTPServer(
        (HOST, port),
        make_handler(cases, output_dir, comparison_study, comparison_results),
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
