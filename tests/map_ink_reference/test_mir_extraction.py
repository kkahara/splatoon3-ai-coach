"""Extraction on a synthetic video written by the test (no corpus media)."""

from __future__ import annotations

import copy
from pathlib import Path

import cv2
import numpy as np
import pytest
from map_ink_reference.errors import MalformedInput, PinMismatch
from map_ink_reference.extraction import extract_recording
from map_ink_reference.io_utils import sha256_array, sha256_file
from mir_helpers import ink_image

from splatoon3_ai_coach.media.video import VideoLoader
from splatoon3_ai_coach.media.video_identity import video_identity

W, H, FPS = 160, 120, 4


@pytest.fixture
def synthetic_video(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (W, H))
    for i in range(12):
        writer.write(ink_image(i, W, H))
    writer.release()
    return path


def _plan(video: Path, picks: list[int]) -> dict:
    with VideoLoader(video) as loader:
        decoded = list(loader.frames())
    frames = [
        {
            "frame_id": f"f{d.frame_index}",
            "timestamp": d.timestamp,
            "source_pts": d.source_pts,
            "source_frame_index": d.frame_index,
            "source_time_base": [d.source_time_base_num, d.source_time_base_den],
        }
        for d in decoded
        if d.frame_index in picks
    ]
    return {
        "recording_id": "synthetic",
        "video_identity": video_identity(video),
        "frame_size": [W, H],
        "frames": frames,
    }


def test_extraction_is_lossless_and_hash_pinned(
    synthetic_video: Path, tmp_path: Path
) -> None:
    plan = _plan(synthetic_video, [1, 5, 9])
    out = tmp_path / "frames"
    provenance = extract_recording(
        plan, synthetic_video, out, expected_video_sha256=sha256_file(synthetic_video)
    )
    assert [f["frame_id"] for f in provenance["frames"]] == ["f1", "f5", "f9"]
    with VideoLoader(synthetic_video) as loader:
        decoded = {d.frame_index: d.image for d in loader.frames()}
    for entry in provenance["frames"]:
        png = out / entry["png"]
        assert sha256_file(png) == entry["png_sha256"]
        image = cv2.imread(str(png))
        assert np.array_equal(image, decoded[entry["source_frame_index"]])
        assert entry["decoded_array_sha256"] == sha256_array(image)
    assert set(provenance) == {
        "recording_id",
        "video_path",
        "video_sha256",
        "video_identity",
        "decoder",
        "frames",
    }


def test_extraction_stops_on_hash_or_identity_mismatch(
    synthetic_video: Path, tmp_path: Path
) -> None:
    plan = _plan(synthetic_video, [1])
    with pytest.raises(PinMismatch):
        extract_recording(plan, synthetic_video, tmp_path, expected_video_sha256="0" * 64)
    wrong = dict(plan, video_identity="other")
    with pytest.raises(PinMismatch):
        extract_recording(
            wrong,
            synthetic_video,
            tmp_path,
            expected_video_sha256=sha256_file(synthetic_video),
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["frames"][0].update(source_pts=10**9),
        lambda p: p["frames"][0].update(source_frame_index=99),
        lambda p: p.update(frame_size=[W * 2, H]),
        lambda p: p["frames"].append(dict(p["frames"][0])),
    ],
)
def test_extraction_fails_closed_on_inconsistent_plan(
    synthetic_video: Path, tmp_path: Path, mutate
) -> None:
    plan = copy.deepcopy(_plan(synthetic_video, [2, 3]))
    mutate(plan)
    with pytest.raises(MalformedInput):
        extract_recording(
            plan,
            synthetic_video,
            tmp_path / "x",
            expected_video_sha256=sha256_file(synthetic_video),
        )
