"""Local-only visual review for the isolated paint-only experiment."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from tools.diagnostics.battle_frontline.experiment import (
    Case,
    CaseResult,
    ColorBinding,
    ManualRegistration,
    Point,
    load_cases,
    sha256_file,
)

HOST = "127.0.0.1"
MAX_BODY = 128 * 1024


class ReviewDecision(BaseModel):
    """Immutable, versioned reviewer decision for one generated line."""

    schema_version: Literal[2] = 2
    case_id: str
    role: Literal["frontline"]
    action: Literal["approve", "reject", "adjust", "confirm_reference"]
    stage_id: str
    coordinate_space: Literal["normalized_screenshot_uv"] = "normalized_screenshot_uv"
    adjusted_line: list[Point] = Field(default_factory=list)
    generated_line_segments: list[list[Point]] = Field(default_factory=list)
    provisional_reference_line: list[Point] = Field(default_factory=list)
    note: str = Field(default="", max_length=2000)
    source_sha256: str
    source_image_hash_verified_at_save: bool = True
    source_recording_sha256: str
    source_recording_hash_verified: bool
    video_time: float | None
    polygon_sha256: str | None
    classifier_config_sha256: str | None
    registration: ManualRegistration
    calibration: ColorBinding
    reference_confirmed: bool = False
    created_at: str


def make_handler(
    cases: list[Case], results: list[CaseResult], output_dir: Path
) -> type[BaseHTTPRequestHandler]:
    """Create a handler serving only declared case images and saved derivatives."""
    case_by_id = {case.case_id: case for case in cases}
    result_by_id = {result.case_id: result for result in results}
    page = Path(__file__).with_name("review.html")
    decisions = output_dir / "decisions"

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, data: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self, status: int, value: Any) -> None:
            self._send(status, json.dumps(value).encode(), "application/json")

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                return self._send(200, page.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/cases":
                return self._json(
                    200,
                    [
                        {
                            "id": case.case_id,
                            "role": case.role,
                            "video_time": case.video_time,
                            "source_sha256": result_by_id[case.case_id].source_sha256,
                            "human_line": [p.model_dump() for p in case.human_line],
                            "generated": result_by_id[case.case_id].model_dump(
                                mode="json"
                            ),
                            "decisions": self._decisions(case.case_id),
                        }
                        for case in cases
                    ],
                )
            if self.path.startswith("/api/decision/latest/"):
                case_id = self.path.rsplit("/", 1)[-1]
                if case_id not in case_by_id:
                    return self._json(404, {"error": "case_not_found"})
                history = self._decisions(case_id)
                if not history:
                    return self._json(404, {"error": "no_decision_saved"})
                path = decisions / history[-1]["filename"]
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{path.name}"',
                )
                self.send_header("Cache-Control", "no-store")
                payload = path.read_bytes()
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                return self.wfile.write(payload)
            parts = self.path.split("/")
            if len(parts) == 4 and parts[1] == "api" and parts[2] == "image":
                return self._image(parts[3])
            self._json(404, {"error": "not_found"})

        def _image(self, token: str) -> None:
            for case in cases:
                if token == f"{case.case_id}.clean":
                    return self._send(200, case.clean_image.read_bytes(), "image/png")
                result = result_by_id[case.case_id]
                names = {
                    f"{case.case_id}.paint": result.overlay_filename,
                    f"{case.case_id}.uncertainty": result.uncertainty_filename,
                }
                if token in names and names[token] is not None:
                    path = output_dir / names[token]
                    return self._send(200, path.read_bytes(), "image/png")
            self._json(404, {"error": "image_not_found"})

        def _decisions(self, case_id: str) -> list[dict[str, str]]:
            paths = sorted(decisions.glob(f"{case_id}.r*.json"))
            records = []
            for path in paths:
                payload = json.loads(path.read_text(encoding="utf-8"))
                records.append({"filename": path.name, "action": payload["action"]})
            return records

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/decision":
                return self._json(404, {"error": "not_found"})
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY:
                return self._json(413, {"error": "invalid_body_size"})
            try:
                body = json.loads(self.rfile.read(length))
                case = case_by_id[body["case_id"]]
                result = result_by_id[case.case_id]
                if case.role != "frontline":
                    raise ValueError("Only frontline cases accept a line decision")
                image_hash = sha256_file(case.clean_image)
                if (
                    result.source_sha256 != image_hash
                    or image_hash != case.expected_clean_sha256
                ):
                    raise ValueError("Source image changed since classification")
                recording_path = Path(case.source_recording)
                if (
                    not recording_path.is_file()
                    or sha256_file(recording_path) != case.source_recording_sha256
                ):
                    raise ValueError("Source recording hash does not match the study")
                action = body["action"]
                points = body.get("adjusted_line", [])
                if action == "approve" and result.status != "available":
                    raise ValueError("Cannot accept a generated line that is unavailable")
                if action == "adjust" and len(points) < 2:
                    raise ValueError("Adjusted line needs at least two points")
                decision = ReviewDecision(
                    case_id=case.case_id,
                    role="frontline",
                    action=action,
                    stage_id=case.stage_id,
                    adjusted_line=points if action == "adjust" else [],
                    generated_line_segments=result.line_segments or [result.line],
                    provisional_reference_line=case.human_line,
                    note=body.get("note", ""),
                    source_sha256=result.source_sha256,
                    source_image_hash_verified_at_save=True,
                    source_recording_sha256=case.source_recording_sha256,
                    source_recording_hash_verified=True,
                    video_time=case.video_time,
                    polygon_sha256=result.polygon_sha256,
                    classifier_config_sha256=result.classifier_config_sha256,
                    registration=case.registration,
                    calibration=case.colors,
                    reference_confirmed=bool(body.get("reference_confirmed", False)),
                    created_at=datetime.now(UTC).isoformat(),
                )
                decisions.mkdir(parents=True, exist_ok=True)
                revision = len(list(decisions.glob(f"{case.case_id}.r*.json"))) + 1
                target = decisions / f"{case.case_id}.r{revision:03d}.json"
                with target.open("x", encoding="utf-8") as stream:
                    stream.write(decision.model_dump_json(indent=2) + "\n")
                self._json(200, {"ok": True, "revision": revision})
            except (KeyError, ValueError, TypeError) as exc:
                self._json(400, {"error": str(exc)})

    return Handler


def serve(
    study: Path,
    output_dir: Path,
    port: int,
    comparison_study: Path | None = None,
    comparison_results: Path | None = None,
) -> None:
    """Serve Phase 1 or blind Phase 1B on loopback until interrupted."""
    payload = json.loads(study.read_text(encoding="utf-8"))
    if str(payload.get("workflow", "")).startswith("human_boundary_phase1b"):
        from tools.diagnostics.battle_frontline.phase1b_review import (
            serve as serve_phase1b,
        )

        return serve_phase1b(
            study,
            output_dir,
            port,
            comparison_study,
            comparison_results,
        )
    cases = load_cases(study)
    results = [
        CaseResult.model_validate(row)
        for row in json.loads((output_dir / "results.json").read_text(encoding="utf-8"))
    ]
    if {r.case_id for r in results} != {c.case_id for c in cases}:
        raise ValueError("Study cases do not match results")
    server = ThreadingHTTPServer((HOST, port), make_handler(cases, results, output_dir))
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main() -> None:
    """CLI entry point for an existing, explicit experiment directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("generated", type=Path)
    parser.add_argument("--port", type=int, default=8774)
    parser.add_argument("--comparison-study", type=Path)
    parser.add_argument("--comparison-results", type=Path)
    args = parser.parse_args()
    serve(
        args.study,
        args.generated,
        args.port,
        args.comparison_study,
        args.comparison_results,
    )


if __name__ == "__main__":
    main()
