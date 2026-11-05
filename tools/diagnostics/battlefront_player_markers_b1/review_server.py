"""Local review server for inspection-only player-marker proposals."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

import cv2
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[3]
STATIC = Path(__file__).resolve().parent / "review.html"
LOCALIZATION_TOLERANCE_PX = 24.0


def latest_review(directory: Path, frame_id: str) -> dict | None:
    """Load the latest versioned reviewer record for one frame."""
    paths = sorted(directory.glob(f"{frame_id}.r[0-9][0-9][0-9][0-9].json"))
    return json.loads(paths[-1].read_text(encoding="utf-8")) if paths else None


def file_sha256(path: Path) -> str:
    """Hash source bytes before trusting provenance-bound geometry."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def metrics(raw_outputs: list[dict], reviews_dir: Path) -> dict:
    """Compute only reviewed-frame classification metrics; never infer labels."""
    result = {}
    for split in ("development", "held_out"):
        totals = {
            "frames_total": 0,
            "frames_complete": 0,
            "tp": 0,
            "fp": 0,
            "fn": 0,
            "team_truth_cases": 0,
            "team_prediction_known": 0,
            "team_prediction_correct": 0,
            "team_prediction_unknown": 0,
            "team_truth_unknown": 0,
            "color_side_compared": 0,
            "color_side_correct": 0,
        }
        for frame in raw_outputs:
            if frame["split"] != split:
                continue
            totals["frames_total"] += 1
            review = latest_review(reviews_dir, frame["frame_id"])
            if not review or not review.get("review_complete"):
                continue
            decisions = {
                item["candidate_id"]: item
                for item in review.get("candidate_decisions", [])
            }
            if set(decisions) != {item["candidate_id"] for item in frame["candidates"]}:
                continue
            totals["frames_complete"] += 1
            totals["fn"] += len(review.get("missed_markers", []))
            for candidate in frame["candidates"]:
                decision = decisions[candidate["candidate_id"]]
                if decision.get("decision") in {"accept", "correct"}:
                    totals["tp"] += 1
                    truth = decision.get("reviewer_team", "unknown")
                    if truth == "unknown":
                        totals["team_truth_unknown"] += 1
                    else:
                        totals["team_truth_cases"] += 1
                        expected = "ally_color" if truth == "ally" else "opponent_color"
                        totals["color_side_compared"] += 1
                        totals["color_side_correct"] += int(
                            candidate.get("observed_color_side") == expected
                        )
                        association = candidate.get("team_association", "unknown")
                        if association in {"ally", "opponent"}:
                            totals["team_prediction_known"] += 1
                            totals["team_prediction_correct"] += int(association == truth)
                        else:
                            totals["team_prediction_unknown"] += 1
                elif decision.get("decision") == "reject":
                    totals["fp"] += 1
        valid = totals["frames_complete"] > 0
        precision_denom = totals["tp"] + totals["fp"]
        recall_denom = totals["tp"] + totals["fn"]
        totals["precision"] = totals["tp"] / precision_denom if precision_denom else None
        totals["recall"] = totals["tp"] / recall_denom if recall_denom else None
        totals["f1"] = (
            2
            * totals["precision"]
            * totals["recall"]
            / (totals["precision"] + totals["recall"])
            if totals["precision"] is not None
            and totals["recall"] is not None
            and totals["precision"] + totals["recall"] > 0
            else None
        )
        totals["color_side_accuracy"] = (
            totals["color_side_correct"] / totals["color_side_compared"]
            if totals["color_side_compared"]
            else None
        )
        totals["team_association_accuracy"] = (
            totals["team_prediction_correct"] / totals["team_prediction_known"]
            if totals["team_prediction_known"]
            else None
        )
        totals["team_association_coverage"] = (
            totals["team_prediction_known"] / totals["team_truth_cases"]
            if totals["team_truth_cases"]
            else None
        )
        totals["status"] = "measured" if valid else "awaiting_complete_human_labels"
        result[split] = totals
    return result


def compare_annotations(frame: dict, annotation: dict) -> dict:
    """Compare proposals to clear human points using one-to-one distance matches."""
    if not annotation.get("frame_complete"):
        return {"status": "frame_not_complete"}
    proposals = frame["candidates"]
    human = annotation["annotations"]
    certain = [item for item in human if not item["identity_uncertain"]]
    uncertain = [item for item in human if item["identity_uncertain"]]
    pairs = []
    for proposal_index, proposal in enumerate(proposals):
        px, py = proposal["center_px"]
        for human_index, item in enumerate(certain):
            hx = item["image_xy"][0] * 1920
            hy = item["image_xy"][1] * 1080
            distance = float(np.hypot(px - hx, py - hy))
            if distance <= LOCALIZATION_TOLERANCE_PX:
                pairs.append((distance, proposal_index, human_index))
    pairs.sort()
    matched_proposals: set[int] = set()
    matched_humans: set[int] = set()
    errors: list[float] = []
    color_checked = 0
    color_correct = 0
    for distance, proposal_index, human_index in pairs:
        if proposal_index in matched_proposals or human_index in matched_humans:
            continue
        matched_proposals.add(proposal_index)
        matched_humans.add(human_index)
        errors.append(distance)
        observed_side = proposals[proposal_index]["observed_color_side"]
        expected_side = {
            "orange": "ally_color",
            "purple": "opponent_color",
        }.get(certain[human_index]["team"])
        if expected_side:
            color_checked += 1
            color_correct += int(observed_side == expected_side)
    ambiguous = 0
    false_positive = 0
    for proposal_index, proposal in enumerate(proposals):
        if proposal_index in matched_proposals:
            continue
        px, py = proposal["center_px"]
        uncertain_distances = [
            float(
                np.hypot(
                    px - item["image_xy"][0] * 1920,
                    py - item["image_xy"][1] * 1080,
                )
            )
            for item in uncertain
        ]
        if uncertain_distances and min(uncertain_distances) <= LOCALIZATION_TOLERANCE_PX:
            ambiguous += 1
        else:
            false_positive += 1
    missed = len(certain) - len(matched_humans)
    precision_denominator = len(matched_proposals) + false_positive
    recall_denominator = len(matched_humans) + missed
    return {
        "status": "measured",
        "human_markers": len(human),
        "clear_human_markers": len(certain),
        "uncertain_identity_markers": len(uncertain),
        "computer_proposals": len(proposals),
        "matched_markers": len(matched_humans),
        "false_positives": false_positive,
        "missed_markers": missed,
        "ambiguous_proposals_near_uncertain_markers": ambiguous,
        "localization_tolerance_pixels": LOCALIZATION_TOLERANCE_PX,
        "localization_errors_pixels": {
            "count": len(errors),
            "mean": sum(errors) / len(errors) if errors else None,
            "median": float(np.median(errors)) if errors else None,
            "max": max(errors) if errors else None,
        },
        "color_side_accuracy": color_correct / color_checked if color_checked else None,
        "color_side_cases": color_checked,
        "precision": len(matched_proposals) / precision_denominator
        if precision_denominator
        else None,
        "recall": len(matched_humans) / recall_denominator
        if recall_denominator
        else None,
    }


def annotation_summary(raw_outputs: list[dict], annotations_dir: Path) -> dict:
    """Summarize completed annotation comparisons by split."""
    result = {}
    for split in ("development", "held_out"):
        frames = [item for item in raw_outputs if item["split"] == split]
        compared = []
        for frame in frames:
            annotation = latest_annotation(annotations_dir, frame["frame_id"])
            if annotation and annotation.get("frame_complete"):
                compared.append(compare_annotations(frame, annotation))
        result[split] = {
            "completed_frames": len(compared),
            "available_frames": len(frames),
            "comparisons": compared,
            "status": "measured" if compared else "awaiting_human_annotations",
        }
    return result


def latest_annotation(directory: Path, frame_id: str) -> dict | None:
    """Load the latest human-first annotation revision for one frame."""
    paths = sorted(directory.glob(f"{frame_id}.annotation.r[0-9][0-9][0-9][0-9].json"))
    return json.loads(paths[-1].read_text(encoding="utf-8")) if paths else None


def image_to_map_point(manifest: dict, image_xy: list[float]) -> list[float] | None:
    """Transform an image point with the reviewed registration when in-stage."""
    stage = manifest["stage"]
    if stage["registration_status"] != "reviewed_same_recording":
        return None
    registration = stage["registration"]
    source = np.asarray(
        [[point["x"], point["y"]] for point in registration["screenshot_points"]],
        dtype=np.float32,
    )
    target = np.asarray(
        [[point["x"], point["y"]] for point in registration["overlay_points"]],
        dtype=np.float32,
    )
    homography = cv2.getPerspectiveTransform(source, target)
    point = np.asarray([[image_xy]], dtype=np.float32)
    mapped = cv2.perspectiveTransform(point, homography)[0, 0]
    polygon_path = ROOT / stage["polygon_path"]
    if file_sha256(polygon_path) != stage["polygon_sha256"]:
        return None
    polygon_data = yaml.safe_load(polygon_path.read_text(encoding="utf-8"))
    contour = np.asarray(polygon_data["polygon"], dtype=np.float32).reshape((-1, 1, 2))
    if cv2.pointPolygonTest(contour, (float(mapped[0]), float(mapped[1])), False) < 0:
        return None
    return [round(float(mapped[0]), 7), round(float(mapped[1]), 7)]


def make_handler(study_dir: Path):
    """Create a handler bound to a study directory."""
    reviews_dir = study_dir / "reviews"
    annotations_dir = study_dir / "annotations"

    class Handler(BaseHTTPRequestHandler):
        """Serve study inputs and append-only human review JSON."""

        def _json(self, value: object, status: int = 200) -> None:
            body = json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                body = STATIC.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/study":
                manifest = json.loads((study_dir / "manifest.json").read_text())
                outputs = json.loads(
                    (study_dir / "raw_detector_outputs.json").read_text()
                )
                queue = json.loads(
                    (study_dir / "review_queue_v2.json").read_text(encoding="utf-8")
                )
                self._json(
                    {"manifest": manifest, "outputs": outputs, "review_queue": queue}
                )
            elif path == "/api/summary":
                outputs = json.loads(
                    (study_dir / "raw_detector_outputs.json").read_text()
                )
                self._json(annotation_summary(outputs, annotations_dir))
            elif path == "/api/metrics":
                outputs = json.loads(
                    (study_dir / "raw_detector_outputs.json").read_text()
                )
                self._json(metrics(outputs, reviews_dir))
            elif path.startswith("/frames/"):
                relative = Path(path.removeprefix("/frames/"))
                candidate = (study_dir / "source_frames" / relative.name).resolve()
                if (
                    candidate.parent != (study_dir / "source_frames").resolve()
                    or not candidate.is_file()
                ):
                    self.send_error(404)
                    return
                body = candidate.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/source-video":
                self._video()
            elif path.startswith("/api/reviews/"):
                frame_id = path.rsplit("/", 1)[-1]
                if not re.fullmatch(r"m08_f\d{5}", frame_id):
                    self.send_error(400)
                    return
                self._json(latest_review(reviews_dir, frame_id) or {})
            elif path.startswith("/api/annotations/"):
                frame_id = path.rsplit("/", 1)[-1]
                if not re.fullmatch(r"m08_f\d{5}", frame_id):
                    self.send_error(400)
                    return
                self._json(latest_annotation(annotations_dir, frame_id) or {})
            elif path.startswith("/api/comparison/"):
                frame_id = path.rsplit("/", 1)[-1]
                if not re.fullmatch(r"m08_f\d{5}", frame_id):
                    self.send_error(400)
                    return
                annotation = latest_annotation(annotations_dir, frame_id)
                outputs = json.loads(
                    (study_dir / "raw_detector_outputs.json").read_text()
                )
                frame = next(
                    (item for item in outputs if item["frame_id"] == frame_id), None
                )
                if not frame or not annotation:
                    self._json({"status": "not_available"})
                    return
                self._json(compare_annotations(frame, annotation))
            else:
                self.send_error(404)

        def _video(self) -> None:
            """Stream the pinned source recording with HTTP byte-range support."""
            manifest = json.loads((study_dir / "manifest.json").read_text())
            source = Path(manifest["source_recording"]["path"])
            if not source.is_file():
                self.send_error(404, "source recording unavailable")
                return
            size = source.stat().st_size
            header = self.headers.get("Range")
            start, end, status = 0, size - 1, 200
            if header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
                if not match:
                    self.send_error(416)
                    return
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                elif last:
                    start = max(0, size - int(last))
                if start > end or start >= size:
                    self.send_error(416)
                    return
                status = 206
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", "video/quicktime")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with source.open("rb") as stream:
                stream.seek(start)
                remaining = length
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def do_POST(self) -> None:
            request_path = urlparse(self.path).path
            if request_path == "/api/annotations":
                self._save_annotation()
                return
            if request_path != "/api/reviews":
                self.send_error(404)
                return
            try:
                payload = json.loads(
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                )
                frame_id = payload["frame_id"]
                if not re.fullmatch(r"m08_f\d{5}", frame_id):
                    raise ValueError("invalid frame id")
                outputs = json.loads(
                    (study_dir / "raw_detector_outputs.json").read_text()
                )
                manifest = json.loads((study_dir / "manifest.json").read_text())
                frame = next(item for item in outputs if item["frame_id"] == frame_id)
                expected = {item["candidate_id"] for item in frame["candidates"]}
                got = {
                    item["candidate_id"]
                    for item in payload.get("candidate_decisions", [])
                }
                if got - expected:
                    raise ValueError("review contains unknown detector candidate ids")
                reviews_dir.mkdir(parents=True, exist_ok=True)
                previous = sorted(
                    reviews_dir.glob(f"{frame_id}.r[0-9][0-9][0-9][0-9].json")
                )
                version = len(previous) + 1
                payload["schema_version"] = 1
                payload["review_version"] = version
                payload["source_frame_sha256"] = frame["image_sha256"]
                payload["source_recording_sha256"] = manifest["source_recording"][
                    "sha256"
                ]
                payload["map_coordinate_basis"] = (
                    "reviewed same-recording registration; outside-polygon points omitted"
                )
                for decision in payload.get("candidate_decisions", []):
                    corrected = decision.get("corrected_image_xy")
                    if corrected is not None:
                        decision["corrected_map_xy"] = image_to_map_point(
                            manifest, corrected
                        )
                for marker in payload.get("missed_markers", []):
                    image_xy = marker.get("image_xy")
                    if image_xy is not None:
                        marker["map_xy"] = image_to_map_point(manifest, image_xy)
                payload["reviewer"] = payload.get("reviewer") or "unspecified"
                destination = reviews_dir / f"{frame_id}.r{version:04d}.json"
                with NamedTemporaryFile(
                    "w", encoding="utf-8", dir=reviews_dir, delete=False
                ) as stream:
                    stream.write(json.dumps(payload, indent=2) + "\n")
                    temporary = Path(stream.name)
                temporary.replace(destination)
                self._json({"saved": True, "path": destination.name, "version": version})
            except (KeyError, ValueError, StopIteration, json.JSONDecodeError) as error:
                self._json({"error": str(error)}, 400)

        def _save_annotation(self) -> None:
            """Append one validated human annotation revision."""
            try:
                payload = json.loads(
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                )
                frame_id = payload["frame_id"]
                if not re.fullmatch(r"m08_f\d{5}", frame_id):
                    raise ValueError("invalid frame id")
                outputs = json.loads(
                    (study_dir / "raw_detector_outputs.json").read_text()
                )
                manifest = json.loads((study_dir / "manifest.json").read_text())
                frame = next(item for item in outputs if item["frame_id"] == frame_id)
                valid_teams = {"orange", "purple", "unknown"}
                annotations = payload.get("annotations", [])
                ids = [item.get("id") for item in annotations]
                if len(ids) != len(set(ids)):
                    raise ValueError("annotation IDs must be unique")
                if not isinstance(payload.get("frame_complete"), bool):
                    raise ValueError("frame_complete must be explicitly true or false")
                for item in annotations:
                    xy = item.get("image_xy")
                    if (
                        not re.fullmatch(r"human-\d+", str(item.get("id", "")))
                        or item.get("team") not in valid_teams
                        or not isinstance(xy, list)
                        or len(xy) != 2
                        or not isinstance(item.get("identity_uncertain"), bool)
                        or any(not isinstance(value, (int, float)) for value in xy)
                        or any(value < 0 or value > 1 for value in xy)
                    ):
                        raise ValueError("invalid annotation point or team")
                    item["map_xy"] = image_to_map_point(manifest, xy)
                annotations_dir.mkdir(parents=True, exist_ok=True)
                previous = sorted(
                    annotations_dir.glob(
                        f"{frame_id}.annotation.r[0-9][0-9][0-9][0-9].json"
                    )
                )
                payload["schema_version"] = 1
                payload["annotation_version"] = len(previous) + 1
                payload["source_recording_sha256"] = manifest["source_recording"][
                    "sha256"
                ]
                payload["source_frame_sha256"] = frame["image_sha256"]
                payload["source_frame_index"] = frame["frame_index"]
                payload["source_pts"] = frame["source_pts"]
                payload["source_time_base"] = frame["source_time_base"]
                payload["registration_status"] = manifest["stage"]["registration_status"]
                destination = annotations_dir / (
                    f"{frame_id}.annotation.r{len(previous) + 1:04d}.json"
                )
                with NamedTemporaryFile(
                    "w", encoding="utf-8", dir=annotations_dir, delete=False
                ) as stream:
                    stream.write(json.dumps(payload, indent=2) + "\n")
                    temporary = Path(stream.name)
                temporary.replace(destination)
                self._json(
                    {
                        "saved": True,
                        "revision": len(previous) + 1,
                        "frame_complete": payload.get("frame_complete", False),
                    }
                )
            except (KeyError, ValueError, StopIteration, json.JSONDecodeError) as error:
                self._json({"error": str(error)}, 400)

        def log_message(self, format: str, *args) -> None:
            """Keep routine local review requests quiet."""

    return Handler


def main() -> None:
    """Serve one isolated study on localhost."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--study-dir", type=Path, default=ROOT / "analysis/battlefront_player_markers_b1"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8776)
    args = parser.parse_args()
    server = ThreadingHTTPServer(
        (args.host, args.port), make_handler(args.study_dir.resolve())
    )
    print(f"Player-marker review: http://{args.host}:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
