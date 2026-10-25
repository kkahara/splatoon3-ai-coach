"""Seal checks, local server and report statistics."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import map_ink_reference.seal as seal
import pytest
from map_ink_reference.errors import (
    PinMismatch,
    ProcedureOrderError,
    SealViolation,
)
from map_ink_reference.io_utils import sha256_file
from map_ink_reference.pins import verify_pin
from map_ink_reference.report import accuracy, inversion_rate, recording_summary
from map_ink_reference.server import make_handler
from mir_helpers import commit, init_repo
from test_mir_session import RECORDINGS, assert_blind, new_session


def test_require_sealed_and_ordering(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    a, b = repo / "a.json", repo / "b.json"
    a.write_text("1")
    with pytest.raises(SealViolation):
        seal.require_sealed(repo, a)
    first = commit(repo, [a], "a")
    assert seal.require_sealed(repo, a) == first
    a.write_text("2")
    with pytest.raises(SealViolation):
        seal.require_sealed(repo, a)
    a.write_text("1")
    b.write_text("x")
    commit(repo, [b], "b")
    seal.require_strictly_after(repo, a, b)
    with pytest.raises(ProcedureOrderError):
        seal.require_strictly_after(repo, b, a)
    c, d = repo / "c.json", repo / "d.json"
    c.write_text("c")
    d.write_text("d")
    commit(repo, [c, d], "together")
    with pytest.raises(ProcedureOrderError):
        seal.require_strictly_after(repo, c, d)


def test_tool_must_be_clean(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    tool = repo / "tool"
    tool.mkdir()
    (tool / "x.py").write_text("x = 1\n")
    commit(repo, [tool / "x.py"], "tool")
    assert len(seal.require_tool_clean(repo, tool)) == 40
    (tool / "x.py").write_text("x = 2\n")
    with pytest.raises(SealViolation):
        seal.require_tool_clean(repo, tool)


def test_preconditions_need_amendment_and_committed_a1(
    tmp_path: Path, monkeypatch
) -> None:
    repo = init_repo(tmp_path / "repo")
    prereg = repo / "prereg.md"
    prereg.write_text("sealed\n")
    a1 = repo / "a1.py"
    a1.write_text("code\n")
    digest = sha256_file(prereg)
    monkeypatch.setattr(seal, "PREREGISTRATION_PATH", "prereg.md")
    monkeypatch.setattr(
        seal, "PREREGISTRATION_CHAIN", (("amendment4", "f" * 40, digest),)
    )
    pins = {"a1.py": sha256_file(a1)}
    with pytest.raises(SealViolation):
        seal.verify_preconditions(repo, pins)
    amendment = commit(repo, [prereg], "amendment")
    with pytest.raises(SealViolation):  # the chain commit is not in history
        seal.verify_preconditions(repo, {})
    monkeypatch.setattr(
        seal, "PREREGISTRATION_CHAIN", (("amendment4", amendment, digest),)
    )
    with pytest.raises(SealViolation):
        seal.verify_preconditions(repo, pins)
    commit(repo, [a1], "a1")
    seal.verify_preconditions(repo, pins)
    with pytest.raises(PinMismatch):
        seal.verify_preconditions(repo, {"a1.py": "0" * 64})


def test_require_in_order_checks_each_consecutive_pair(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    paths = [repo / f"{name}.json" for name in "abc"]
    for path in paths:
        path.write_text(path.name)
    commit(repo, paths[:1], "a")
    with pytest.raises(SealViolation):
        seal.require_in_order(repo, paths)
    commit(repo, paths[1:], "b and c together")
    with pytest.raises(ProcedureOrderError):
        seal.require_in_order(repo, paths)
    seal.require_in_order(repo, paths[:2])


def test_verify_pin_stops_on_mismatch_or_missing(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_text("x")
    assert verify_pin(path, sha256_file(path)) == sha256_file(path)
    with pytest.raises(PinMismatch):
        verify_pin(path, "0" * 64)
    with pytest.raises(PinMismatch):
        verify_pin(tmp_path / "missing", "0" * 64)


@pytest.fixture
def served(tmp_path: Path):
    session = new_session(tmp_path)
    server = HTTPServer(("127.0.0.1", 0), make_handler(session))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", session
    server.shutdown()
    server.server_close()


def _get(url: str):
    with urllib.request.urlopen(url) as response:
        return response.status, response.read()


def _post(url: str, payload) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_server_serves_only_blind_views(served) -> None:
    base, session = served
    status, page = _get(base + "/")
    assert status == 200 and b"Map ink reference" in page
    status, body = _get(base + "/api/task")
    task = json.loads(body)
    assert task["kind"] == "swatch"
    assert_blind(task)
    status, png = _get(base + "/api/image/" + task["frames"][0]["image"])
    assert png[:4] == b"\x89PNG"
    status, loupe = _get(base + "/api/loupe?frame=0&x=5&y=5")
    assert json.loads(loupe)["zoom"] == 4
    code, error = _post(base + "/api/label", {"item_id": "x", "label": "ink_A"})
    assert code == 400 and "not labelling" in error["error"]
    code, error = _post(base + "/api/swatch", {"token": "wrong", "picks": None})
    assert code == 400
    code, _ = _post(base + "/api/swatch", {"token": task["token"], "picks": None})
    assert code == 200
    code, _ = _post(base + "/api/unknown", {})
    assert code == 404
    with pytest.raises(urllib.error.HTTPError):
        _get(base + "/api/image/not-a-handle")
    assert session.state["swatches"][task["token"]]["status"] == "cant_tell"
    assert len(RECORDINGS) == 3


def test_report_descriptive_statistics() -> None:
    points = [
        {"reference": "ally", "a1": "ally", "v3": "opponent"},
        {"reference": "opponent", "a1": "ally", "v3": "opponent"},
        {"reference": "ally", "a1": "other", "v3": "ally"},
        {"reference": "other", "a1": "other", "v3": "other"},
    ]
    assert accuracy(points, "a1") == 0.5 and accuracy(points, "v3") == 0.75
    assert inversion_rate(points, "a1") == 0.5
    assert inversion_rate(points, "v3") == pytest.approx(1 / 3)
    assert accuracy([], "a1") is None and inversion_rate(points[3:], "a1") is None
    record = {
        "recording_id": "r",
        "verdict": "PRESERVED",
        "identity": {"q_all": 0.0},
        "test": None,
        "frames": {
            "stage1": [
                {"judgeable": True, "points": points},
                {"judgeable": False, "points": points},
            ]
        },
    }
    summary = recording_summary(record)
    assert summary["judgeable_frames"] == 1 and summary["judgeable_points"] == 4
