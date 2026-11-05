from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import cv2
import numpy as np

from tools.diagnostics.battle_frontline.experiment import (
    Case,
    CaseResult,
    ManualRegistration,
    Point,
    _uncertainty_overlay,
    sha256_file,
)
from tools.diagnostics.battle_frontline.review_server import make_handler


def test_uncertainty_is_transparent_outside_registered_region() -> None:
    region = np.zeros((3, 4), dtype=bool)
    region[1, 2] = True

    overlay = _uncertainty_overlay(region)

    assert overlay.shape == (3, 4, 4)
    assert overlay[1, 2, 3] > 0
    assert np.count_nonzero(overlay[:, :, 3]) == 1


def test_review_ui_serves_sources_and_saves_versioned_adjustment(tmp_path: Path) -> None:
    clean = tmp_path / "clean.png"
    overlay = tmp_path / "paint.png"
    uncertainty = tmp_path / "uncertainty.png"
    recording = tmp_path / "recording.mov"
    recording.write_bytes(b"test recording")
    for path in (clean, overlay, uncertainty):
        assert cv2.imwrite(str(path), np.zeros((4, 4, 3), dtype=np.uint8))
    clean_hash = sha256_file(clean)
    case = Case(
        case_id="frontline_test",
        role="frontline",
        clean_image=clean,
        stage_id="test",
        stage_polygon=tmp_path / "unused.yaml",
        registration=ManualRegistration(
            overlay_points=[Point(x=0, y=0)] * 4,
            screenshot_points=[Point(x=0, y=0)] * 4,
            back=Point(x=0.1, y=0.5),
            center=Point(x=0.5, y=0.5),
            front=Point(x=0.9, y=0.5),
            reviewed=True,
        ),
        colors={
            "ally_h": 8,
            "opponent_h": 134,
            "source": "test",
            "reviewed_same_match": True,
        },
        source_recording=str(recording),
        source_recording_sha256=sha256_file(recording),
        expected_clean_sha256=clean_hash,
        video_time=1,
        human_line=[Point(x=0.4, y=0.5), Point(x=0.6, y=0.5)],
    )
    result = CaseResult(
        case_id=case.case_id,
        status="available",
        source_sha256=clean_hash,
        overlay_filename=overlay.name,
        uncertainty_filename=uncertainty.name,
        line=[Point(x=0.5, y=0.4), Point(x=0.5, y=0.6)],
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler([case], [result], tmp_path)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(f"{root}/") as response:
            assert b"Paint-only frontline review" in response.read()
        with urlopen(f"{root}/api/image/{case.case_id}.clean") as response:
            assert response.status == 200
        with urlopen(f"{root}/api/cases") as response:
            assert json.loads(response.read())[0]["id"] == case.case_id
        body = json.dumps(
            {
                "case_id": case.case_id,
                "action": "adjust",
                "adjusted_line": [{"x": 0.3, "y": 0.4}, {"x": 0.6, "y": 0.7}],
                "reference_confirmed": True,
                "note": "test adjustment",
            }
        ).encode()
        request = Request(f"{root}/api/decision", data=body, method="POST")
        with urlopen(request) as response:
            assert json.loads(response.read()) == {"ok": True, "revision": 1}
        saved = tmp_path / "decisions/frontline_test.r001.json"
        decision = json.loads(saved.read_text())
        assert decision["schema_version"] == 2
        assert decision["source_sha256"] == clean_hash
        assert decision["source_recording_sha256"] == sha256_file(recording)
        assert decision["source_recording_hash_verified"] is True
        assert decision["source_image_hash_verified_at_save"] is True
        assert decision["coordinate_space"] == "normalized_screenshot_uv"
        assert decision["stage_id"] == "test"
        assert len(decision["generated_line_segments"][0]) == 2
        assert len(decision["provisional_reference_line"]) == 2
        assert decision["reference_confirmed"] is True
        assert len(decision["adjusted_line"]) == 2
        with urlopen(f"{root}/api/decision/latest/{case.case_id}") as response:
            downloaded = json.loads(response.read())
            assert response.headers["Content-Disposition"].endswith(
                'filename="frontline_test.r001.json"'
            )
        assert downloaded["action"] == "adjust"

        approve = Request(
            f"{root}/api/decision",
            data=json.dumps({"case_id": case.case_id, "action": "approve"}).encode(),
            method="POST",
        )
        with urlopen(approve) as response:
            assert json.loads(response.read()) == {"ok": True, "revision": 2}
        approved = json.loads(
            (tmp_path / "decisions/frontline_test.r002.json").read_text()
        )
        assert approved["action"] == "approve"
        assert approved["generated_line_segments"] == [
            [{"x": 0.5, "y": 0.4}, {"x": 0.5, "y": 0.6}]
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
