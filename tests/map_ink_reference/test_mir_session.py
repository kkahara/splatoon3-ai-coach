"""Annotation session: swatches, A/B immutability, positions, blinding, labels."""

from __future__ import annotations

import json
import zlib
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from map_ink_reference.constants import CANT_TELL, LABELS
from map_ink_reference.errors import MalformedInput, ProcedureOrderError
from map_ink_reference.io_utils import read_json, sha256_file, write_json
from map_ink_reference.points import draw_points, duplicate_point_ids
from map_ink_reference.queueing import duplicate_gaps
from map_ink_reference.records import (
    SWATCHES,
    TOKEN_KEY,
    export_session_records,
    labels_name,
    queue_key_name,
    queue_name,
    team_identity_name,
)
from map_ink_reference.session import (
    STATE_FILENAME,
    AnnotationSession,
    FrameAsset,
    RecordingAssets,
    SessionInputs,
    SessionPoint,
    load_sealed_swatches,
)
from mir_helpers import Counter, hsv_pixel, ink_image

RECORDINGS = ("rec-a", "rec-b", "rec-c")
COLOURS = (hsv_pixel(30, 220, 220), hsv_pixel(120, 220, 220))
FORBIDDEN_VIEW_KEYS = {
    "answer",
    "assignment",
    "left",
    "position_seed",
    "draw_seed",
    "mapping",
    "ally",
    "opponent",
    "recording_id",
    "timestamp",
    "point_id",
    "point_ref",
    "pixels",
    "hue",
    "calibration",
}


def _write_frames(root: Path, rid: str, prefix: str, n: int, seed: int) -> tuple:
    assets = []
    for i in range(n):
        path = root / rid / f"{prefix}{i}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(path), ink_image(seed + i))
        assets.append(FrameAsset(f"{prefix}{i}", path, sha256_file(path)))
    return tuple(assets)


def make_inputs(root: Path, recordings=RECORDINGS) -> SessionInputs:
    assets, points = {}, []
    for r, rid in enumerate(recordings):
        selected = _write_frames(root, rid, "s", 8, 100 * r)
        cues = _write_frames(root, rid, "c", 8, 100 * r + 50)
        assets[rid] = RecordingAssets(selected, cues)
        for frame in selected:
            image = cv2.imread(str(frame.png_path))
            domain = np.ones(image.shape[:2], dtype=bool)
            seed = zlib.crc32(f"{rid}|{frame.frame_id}".encode())
            for k, (x, y) in enumerate(draw_points(domain, seed)):
                points.append(
                    SessionPoint(f"{rid}|{frame.frame_id}|{k}", rid, frame.frame_id, x, y)
                )
    ids = [p.point_id for p in points]
    return SessionInputs(
        stage=1,
        recordings=assets,
        points=points,
        duplicate_ids=duplicate_point_ids(ids, "f" * 64),
        point_manifest_sha256="f" * 64,
    )


def find_pixel(path: Path, colour: tuple[int, int, int]) -> tuple[int, int]:
    image = cv2.imread(str(path))
    ys, xs = np.nonzero((image == colour).all(axis=2))
    return int(xs[0]), int(ys[0])


def picks_for(session: AnnotationSession, rid: str) -> list[dict[str, int]]:
    frame = session.inputs.recordings[rid].selected[0]
    return [
        dict(frame_index=0, x=x, y=y)
        for x, y in (find_pixel(frame.png_path, c) for c in COLOURS)
    ]


def walk_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(walk_keys(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(walk_keys(v) for v in value)) if value else set()
    return set()


def assert_blind(view: dict[str, Any]) -> None:
    assert not walk_keys(view) & FORBIDDEN_VIEW_KEYS
    text = json.dumps(view)
    assert not any(rid in text for rid in RECORDINGS)


def rid_of(session: AnnotationSession, token: str) -> str:
    return {t: r for r, t in session.state["tokens"].items()}[token]


def new_session(
    root: Path, inputs: SessionInputs | None = None, counter: Counter | None = None
) -> AnnotationSession:
    counter = counter or Counter()
    return AnnotationSession(
        inputs or make_inputs(root / "frames"),
        root / "state",
        seed_source=counter.seed,
        id_source=counter.new_id,
    )


def do_swatches(session: AnnotationSession, skip: set[str] = frozenset()) -> None:
    while (task := session.current_task())["kind"] == "swatch":
        assert_blind(task)
        rid = rid_of(session, task["token"])
        session.submit_swatch(
            task["token"], None if rid in skip else picks_for(session, rid)
        )


def answer_team(session: AnnotationSession, choose) -> list[dict[str, Any]]:
    views = []
    while (task := session.current_task())["kind"] == "team":
        assert_blind(task)
        views.append(task)
        session.submit_team(task["token"], choose(task))
    return views


def do_labels(session: AnnotationSession, label=lambda task: "ink_A") -> None:
    while (task := session.current_task())["kind"] == "label":
        assert_blind(task)
        assert task["labels"] == list(LABELS)
        session.submit_label(task["item_id"], label(task))


def test_swatch_quality_requirement_and_ab_draw(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    task = session.current_task()
    rid = rid_of(session, task["token"])
    dark = find_pixel(session.inputs.recordings[rid].selected[0].png_path, (20, 20, 20))
    with pytest.raises(MalformedInput):
        session.submit_swatch(
            task["token"],
            [dict(frame_index=0, x=dark[0], y=dark[1]), picks_for(session, rid)[0]],
        )
    with pytest.raises(MalformedInput):
        session.submit_swatch(task["token"], picks_for(session, rid)[:1])
    session.submit_swatch(task["token"], picks_for(session, rid))
    record = session.state["swatches"][task["token"]]
    assert record["status"] == "valid" and record["draw_seed"] is not None
    assert sorted(record["assignment"].values()) == [0, 1]


def test_full_stage1_flow_blinding_and_immutability(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    do_swatches(session, skip={"rec-c"})
    assignments = {
        t: dict(r["assignment"])
        for t, r in session.state["swatches"].items()
        if r["status"] == "valid"
    }
    start_views = answer_team(session, lambda task: "left")
    assert len(start_views) == 2  # rec-c has no valid swatch pair
    queue = session.state["queue"]
    tokens = {item["token"] for item in queue}
    assert session.state["tokens"]["rec-c"] not in tokens
    gaps = duplicate_gaps(queue)
    assert gaps
    for ref, gap in gaps.items():
        repeat = next(
            i for i, it in enumerate(queue) if it["point_ref"] == ref and it["copy"] == 1
        )
        assert gap >= 50 or all(it["copy"] == 1 for it in queue[repeat:])
    do_labels(session)
    end_views = answer_team(session, lambda task: "right")
    assert session.current_task() == {"kind": "complete"}
    export_session_records(tmp_path / "state", tmp_path / "records", tool={})
    team = read_json(tmp_path / "records" / team_identity_name(1))["team"]
    for token, assignment in assignments.items():
        assert session.state["swatches"][token]["assignment"] == assignment
        entry = session.state["team"][token]
        assert set(entry["start"]) == {"position_seed", "left", "answered"}
        assert team[token]["start"]["answer"] == entry["start"]["left"]
        assert team[token]["end"]["answer"] != entry["end"]["left"]
        assert entry["start"]["position_seed"] != entry["end"]["position_seed"]
    assert {v["question"] for v in start_views + end_views} == {
        "Which swatch is my team's colour?"
    }


def test_end_question_does_not_depend_on_start_answer(tmp_path: Path) -> None:
    images = []
    for answer in ("left", "cant_tell"):
        root = tmp_path / answer
        inputs = make_inputs(tmp_path / "frames")
        session = new_session(root, inputs, Counter())
        do_swatches(session)
        answer_team(session, lambda task, a=answer: a)
        do_labels(session)
        task = session.current_task()
        assert task["kind"] == "team"
        images.append(
            [session.image(h) for h in task["swatches"]]
            + [session.image(h) for h in task["map_frames"]]
        )
        assert set(task) == {
            "kind",
            "token",
            "question",
            "map_frames",
            "gameplay_frames",
            "swatches",
            "choices",
        }
    assert images[0] == images[1]


def test_label_view_shows_a_left_b_right_and_fixed_swatches(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    do_swatches(session)
    answer_team(session, lambda task: "left")
    task = session.current_task()
    token = task["token"]
    rid = rid_of(session, token)
    record = session.state["swatches"][token]
    frame = session.inputs.recordings[rid].selected[0]
    a_pixel = record["pixels"][record["assignment"]["A"]]
    image = cv2.imread(str(frame.png_path))
    shown = cv2.imdecode(np.frombuffer(session.image(task["swatch_A"]), np.uint8), 1)
    assert (shown[16:20, 16:20] == image[a_pixel["y"], a_pixel["x"]]).all()
    assert set(task) == {
        "kind",
        "item_id",
        "token",
        "crop",
        "swatch_A",
        "swatch_B",
        "labels",
        "progress",
    }


def test_labels_are_sequential_final_and_in_vocabulary(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    do_swatches(session)
    answer_team(session, lambda task: "left")
    task = session.current_task()
    with pytest.raises(MalformedInput):
        session.submit_label(task["item_id"], "ally")
    session.submit_label(task["item_id"], CANT_TELL)
    with pytest.raises(ProcedureOrderError):
        session.submit_label(task["item_id"], "ink_B")
    with pytest.raises(ProcedureOrderError):
        session.submit_team(task["token"], "left")


def test_session_resumes_from_persisted_state(tmp_path: Path) -> None:
    inputs = make_inputs(tmp_path / "frames")
    session = new_session(tmp_path, inputs)
    do_swatches(session)
    first = answer_team(session, lambda task: "left")
    state = json.loads(json.dumps(session.state))
    resumed = AnnotationSession(inputs, tmp_path / "state")
    assert resumed.state == state
    assert resumed.current_task()["kind"] == "label"
    assert first


def test_state_from_another_manifest_is_rejected(tmp_path: Path) -> None:
    inputs = make_inputs(tmp_path / "frames")
    new_session(tmp_path, inputs)
    inputs.point_manifest_sha256 = "0" * 64
    with pytest.raises(MalformedInput):
        AnnotationSession(inputs, tmp_path / "state")


def test_loupe_only_during_swatch_phase(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    session.current_task()
    loupe = session.loupe(0, 10, 10)
    assert loupe["origin"] == [10 - 48, 10 - 48]
    assert session.image(loupe["image"])[:4] == b"\x89PNG"
    do_swatches(session)
    with pytest.raises(ProcedureOrderError):
        session.loupe(0, 10, 10)


def _complete_stage1(tmp_path: Path) -> AnnotationSession:
    session = new_session(tmp_path)
    do_swatches(session, skip={"rec-c"})
    answer_team(session, lambda task: "left")
    do_labels(session)
    answer_team(session, lambda task: "left")
    return session


def test_export_separates_labels_from_keys(tmp_path: Path) -> None:
    session = _complete_stage1(tmp_path)
    out = tmp_path / "records"
    written = export_session_records(tmp_path / "state", out, tool={"tool_commit": "x"})
    assert set(written) == {
        labels_name(1),
        team_identity_name(1),
        queue_name(1),
        queue_key_name(1),
        SWATCHES,
        TOKEN_KEY,
    }
    labels_text = (out / labels_name(1)).read_text()
    assert not any(rid in labels_text for rid in RECORDINGS)
    assert "|" not in "".join(
        e["item_id"] for e in read_json(out / labels_name(1))["labels"]
    )
    swatches = load_sealed_swatches(out / SWATCHES)
    assert swatches["" + session.state["tokens"]["rec-c"]]["status"] == "cant_tell"
    unfinished = tmp_path / "unfinished"
    write_json(unfinished / STATE_FILENAME, dict(session.state, phase="labelling"))
    with pytest.raises(ProcedureOrderError):
        export_session_records(unfinished, out, tool={})


def test_stage2_reuses_sealed_swatches_and_tokens(tmp_path: Path) -> None:
    stage1 = _complete_stage1(tmp_path / "s1")
    out = tmp_path / "records"
    export_session_records(tmp_path / "s1" / "state", out, tool={})
    swatches = load_sealed_swatches(out / SWATCHES)
    tokens = read_json(out / TOKEN_KEY)["tokens"]
    s1_inputs = stage1.inputs
    rid = "rec-a"
    stage2_frames = _write_frames(tmp_path / "frames2", rid, "t", 2, 900)
    points = [
        SessionPoint(f"{rid}|t{i}|{k}", rid, f"t{i}", 20 + 40 * k, 30)
        for i in range(2)
        for k in range(6)
    ]
    inputs = SessionInputs(
        stage=2,
        recordings={
            rid: RecordingAssets(
                stage2_frames, (), s1_inputs.recordings[rid].selected[:1]
            )
        },
        points=points,
        duplicate_ids=[points[0].point_id],
        point_manifest_sha256="e" * 64,
        tokens={rid: tokens[rid]},
        swatches={tokens[rid]: swatches[tokens[rid]]},
    )
    counter = Counter(500)
    session = AnnotationSession(
        inputs, tmp_path / "s2", seed_source=counter.seed, id_source=counter.new_id
    )
    task = session.current_task()
    assert task["kind"] == "team" and task["token"] == tokens[rid]
    assert_blind(task)
    answer_team(session, lambda task: "right")
    do_labels(session)
    answer_team(session, lambda task: "left")
    assert session.state["swatches"][tokens[rid]] == swatches[tokens[rid]]
    written = export_session_records(tmp_path / "s2", out, tool={})
    assert SWATCHES not in written and TOKEN_KEY not in written


def test_stage2_without_sealed_swatches_fails_closed(tmp_path: Path) -> None:
    inputs = make_inputs(tmp_path / "frames")
    inputs.stage = 2
    with pytest.raises(MalformedInput):
        AnnotationSession(inputs, tmp_path / "state")


def test_load_sealed_swatches_rejects_extra_content(tmp_path: Path) -> None:
    path = tmp_path / "swatches.json"
    path.write_text(json.dumps({"swatches": {}, "team": {}}))
    with pytest.raises(MalformedInput):
        load_sealed_swatches(path)
    path.write_text(
        json.dumps(
            {
                "swatches": {
                    "t": {
                        "status": "valid",
                        "pixels": [],
                        "assignment": {},
                        "draw_seed": 1,
                        "own": "A",
                    }
                }
            }
        )
    )
    with pytest.raises(MalformedInput):
        load_sealed_swatches(path)
