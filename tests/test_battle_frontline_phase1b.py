from __future__ import annotations

import hashlib
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import cv2
import numpy as np
import pytest

from tools.diagnostics.battle_frontline.build_phase1b_queue import selected_times
from tools.diagnostics.battle_frontline.phase1b_review import (
    QueueCase,
    _load_study_cases,
    make_handler,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _post_json(url: str, value: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(value).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request) as response:
        return json.loads(response.read())


def test_queue_has_all_matches_and_temporal_pairs() -> None:
    middle = selected_times(1, 300)
    paired = selected_times(3, 300)

    assert middle == [("middle", 150)]
    assert paired == [("early", 75), ("late", 225)]


def test_m01_pilot_study_accepts_three_frames(tmp_path: Path) -> None:
    frame = tmp_path / "frame.png"
    recording = tmp_path / "recording.mov"
    recording.write_bytes(b"pilot recording")
    assert cv2.imwrite(str(frame), np.zeros((12, 20, 3), dtype=np.uint8))
    base = {
        "recording_index": 1,
        "recording_path": str(recording),
        "recording_filename": recording.name,
        "recording_sha256": _sha(recording),
        "stage_identity_status": "unverified",
        "frame_path": str(frame),
        "frame_sha256": _sha(frame),
    }
    study = tmp_path / "pilot.json"
    study.write_text(
        json.dumps(
            {
                "workflow": "human_boundary_phase1b_batch",
                "cases": [
                    {
                        **base,
                        "case_id": f"m01_{slot}",
                        "video_time": time,
                        "selection_slot": slot,
                    }
                    for slot, time in (("early", 75), ("middle", 150), ("late", 225))
                ],
            }
        )
    )
    cases = _load_study_cases(study)
    assert [case.selection_slot for case in cases] == ["early", "middle", "late"]


def test_blind_annotation_reveal_and_post_reveal_history(tmp_path: Path) -> None:
    frame = tmp_path / "frame.png"
    recording = tmp_path / "recording.mov"
    recording.write_bytes(b"test top-down recording")
    assert cv2.imwrite(str(frame), np.zeros((12, 20, 3), dtype=np.uint8))
    frame_hash = _sha(frame)
    recording_hash = _sha(recording)
    case = QueueCase(
        case_id="m08_test",
        recording_index=8,
        recording_path=str(recording),
        recording_filename=recording.name,
        recording_sha256=recording_hash,
        video_time=172,
        selection_slot="review",
        frame_path=str(frame),
        frame_sha256=frame_hash,
        comparator_key="prior_case",
    )
    comparison_study = tmp_path / "phase1a_study.json"
    comparison_study.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "prior_case",
                        "clean_image": str(frame),
                        "expected_clean_sha256": frame_hash,
                        "human_line": [
                            {"x": 0.4, "y": 0.4},
                            {"x": 0.6, "y": 0.6},
                        ],
                    }
                ]
            }
        )
    )
    comparison_results = tmp_path / "phase1a_generated"
    comparison_results.mkdir()
    paint = comparison_results / "paint.png"
    uncertainty = comparison_results / "uncertainty.png"
    for path in (paint, uncertainty):
        assert cv2.imwrite(str(path), np.zeros((12, 20, 3), dtype=np.uint8))
    (comparison_results / "results.json").write_text(
        json.dumps(
            [
                {
                    "case_id": "prior_case",
                    "status": "available",
                    "source_sha256": frame_hash,
                    "line": [
                        {"x": 0.5, "y": 0.4},
                        {"x": 0.5, "y": 0.6},
                    ],
                    "line_segments": [
                        [
                            {"x": 0.5, "y": 0.4},
                            {"x": 0.5, "y": 0.6},
                        ]
                    ],
                    "overlay_filename": paint.name,
                    "uncertainty_filename": uncertainty.name,
                }
            ]
        )
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler([case], tmp_path, comparison_study, comparison_results),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = "http://127.0.0.1:" + str(server.server_port)
    try:
        with urlopen(root + "/api/cases") as response:
            cases = json.loads(response.read())
        assert "generated" not in cases[0]
        assert "comparator_key" not in cases[0]
        with pytest.raises(HTTPError) as blocked:
            urlopen(root + "/api/comparison/m08_test")
        assert blocked.value.code == 403
        with pytest.raises(HTTPError) as blocked_image:
            urlopen(root + "/api/revealed-image/m08_test/paint")
        assert blocked_image.value.code == 403

        baseline = _post_json(
            root + "/api/decision",
            {
                "case_id": case.case_id,
                "phase": "independent",
                "judgment": "boundary",
                "reasons": ["player_positions", "recent_movement"],
                "shapes": [
                    {
                        "kind": "boundary",
                        "points": [
                            {"x": 0.3, "y": 0.4},
                            {"x": 0.6, "y": 0.6},
                        ],
                    }
                ],
                "explanation": "My line follows the contested area.",
            },
        )
        assert baseline["filename"] == "independent.r001.json"
        baseline_path = tmp_path / "annotations/m08_test/independent.r001.json"
        baseline_bytes = baseline_path.read_bytes()
        assert json.loads(baseline_bytes)["reasons"] == [
            "player_positions",
            "recent_movement",
        ]

        revealed = _post_json(root + "/api/reveal", {"case_id": case.case_id})
        assert revealed["comparison"]["case_id"] == case.case_id
        assert revealed["comparison"]["source_frame_sha256"] == frame_hash
        with urlopen(root + "/api/comparison/m08_test") as response:
            assert json.loads(response.read())["available"] is True
        with urlopen(root + "/api/revealed-image/m08_test/paint") as response:
            assert response.status == 200

        with pytest.raises(HTTPError) as missing_assessment:
            _post_json(
                root + "/api/decision",
                {
                    "case_id": case.case_id,
                    "phase": "post_reveal",
                    "judgment": "no_meaningful_line",
                    "shapes": [],
                },
            )
        assert missing_assessment.value.code == 400
        post = _post_json(
            root + "/api/decision",
            {
                "case_id": case.case_id,
                "phase": "post_reveal",
                "judgment": "no_meaningful_line",
                "shapes": [],
                "explanation": "After reveal I still do not see one line.",
                "comparison_assessment": {
                    "provisional_sketch": "reject",
                    "paint_prediction": "reject",
                },
                "reasons": ["player_positions", "ink_coverage"],
            },
        )
        assert post["filename"] == "post_reveal.r001.json"
        post_path = tmp_path / "annotations/m08_test/post_reveal.r001.json"
        post_decision = json.loads(post_path.read_text())
        assert post_decision["independent_baseline_filename"] == baseline_path.name
        assert post_decision["comparison_assessment"] == {
            "provisional_sketch": "reject",
            "paint_prediction": "reject",
        }
        assert post_decision["reasons"] == ["player_positions", "ink_coverage"]
        assert baseline_path.read_bytes() == baseline_bytes
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
