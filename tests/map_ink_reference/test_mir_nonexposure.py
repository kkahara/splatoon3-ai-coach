"""Amendment 4 annotation-state non-exposure (synthetic sessions only).

Team-identity answers and the A/B mapping never reach a route response,
the browser or a resumed session. Progress state holds only whether a
question was answered and its position draw; answers go to a separate
append-only record that is read only at the sealing step.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import secrets
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from typing import Any

import pytest
from map_ink_reference.constants import CANT_TELL
from map_ink_reference.errors import MalformedInput
from map_ink_reference.io_utils import read_json, write_json
from map_ink_reference.records import (
    export_session_records,
    resolve_team_answers,
    team_identity_name,
)
from map_ink_reference.server import make_handler
from map_ink_reference.session import (
    ANSWERS_FILENAME,
    STATE_FILENAME,
    AnnotationSession,
)
from mir_helpers import Counter
from test_mir_session import (
    FORBIDDEN_VIEW_KEYS,
    make_inputs,
    new_session,
    picks_for,
    rid_of,
    walk_keys,
)

RESPONSE_FORBIDDEN_KEYS = FORBIDDEN_VIEW_KEYS | {
    "answered",
    "draw_id",
    "position",
    "team",
    "own",
    "A_is",
}
PACKAGE_DIR = Path(make_handler.__code__.co_filename).parent


class Browser:
    """Drives one session over HTTP and records every response body."""

    def __init__(self, session: AnnotationSession) -> None:
        self.session = session
        self.server = HTTPServer(("127.0.0.1", 0), make_handler(session))
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.transcript: list[bytes] = []

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def get(self, path: str) -> bytes:
        with urllib.request.urlopen(self.base + path) as response:
            body = response.read()
        self.transcript.append(body)
        return body

    def post(self, path: str, payload: Any) -> dict:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as response:
            body = response.read()
        self.transcript.append(body)
        return json.loads(body)

    def task(self) -> dict:
        task = json.loads(self.get("/api/task"))
        for handle in _handles(task):
            self.get(f"/api/image/{handle}")
        return task

    def step(self, choice: str) -> str:
        """Answer the current task; return its kind."""
        task = self.task()
        kind = task["kind"]
        if kind == "swatch":
            rid = rid_of(self.session, task["token"])
            body = {"token": task["token"], "picks": picks_for(self.session, rid)}
            assert self.post("/api/swatch", body) == {"ok": True}
        elif kind == "team":
            assert self.post("/api/team", {"token": task["token"], "choice": choice}) == {
                "ok": True
            }
        elif kind == "label":
            body = {"item_id": task["item_id"], "label": "ink_A"}
            assert self.post("/api/label", body) == {"ok": True}
        return kind


def _handles(task: dict) -> list[str]:
    out = []
    for key in ("crop", "swatch_A", "swatch_B"):
        if key in task:
            out.append(task[key])
    for key in ("map_frames", "gameplay_frames", "swatches"):
        out += task.get(key, [])
    out += [f["image"] for f in task.get("frames", [])]
    return out


def _deterministic_handles(monkeypatch) -> None:
    counter = itertools.count()
    monkeypatch.setattr(secrets, "token_urlsafe", lambda n=None: f"h{next(counter):06d}")


def _run_over_http(root: Path, choice: str, monkeypatch, *, resume: bool) -> dict:
    """A full Stage 1 session; optionally resumed (new process state) mid-way."""
    _deterministic_handles(monkeypatch)
    inputs = make_inputs(root / "frames")
    counter = Counter()

    def session() -> AnnotationSession:
        return AnnotationSession(
            inputs, root / "state", seed_source=counter.seed, id_source=counter.new_id
        )

    browser = Browser(session())
    transcript: list[bytes] = []
    kinds: list[str] = []
    while (kind := browser.step(choice)) != "complete":
        kinds.append(kind)
        if resume and kind == "team" and kinds.count("team") == 1:
            transcript += browser.transcript
            browser.close()
            browser = Browser(session())
    transcript += browser.transcript
    browser.close()
    return {
        "transcript": transcript,
        "state": (root / "state" / STATE_FILENAME).read_bytes(),
        "answers": (root / "state" / ANSWERS_FILENAME).read_text().splitlines(),
        "kinds": kinds,
    }


def _json_bodies(transcript: list[bytes]) -> list[Any]:
    out = []
    for body in transcript:
        if body[:4] != b"\x89PNG":
            out.append(json.loads(body))
    return out


@pytest.mark.parametrize("resume", [False, True])
def test_route_responses_never_depend_on_or_contain_answers(
    tmp_path: Path, monkeypatch, resume: bool
) -> None:
    runs = {
        choice: _run_over_http(tmp_path / choice, choice, monkeypatch, resume=resume)
        for choice in ("left", "right", CANT_TELL)
    }
    first = runs["left"]
    assert "team" in first["kinds"] and "label" in first["kinds"]
    for body in _json_bodies(first["transcript"]):
        assert not walk_keys(body) & RESPONSE_FORBIDDEN_KEYS, body
    digests = {
        choice: [hashlib.sha256(b).hexdigest() for b in run["transcript"]]
        for choice, run in runs.items()
    }
    assert digests["left"] == digests["right"] == digests[CANT_TELL]
    assert runs["left"]["state"] == runs["right"]["state"] == runs[CANT_TELL]["state"]
    assert runs["left"]["answers"] != runs["right"]["answers"]


def test_progress_state_holds_no_answers(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    while (task := session.current_task())["kind"] == "swatch":
        session.submit_swatch(
            task["token"], picks_for(session, rid_of(session, task["token"]))
        )
    task = session.current_task()
    session.submit_team(task["token"], "right")
    entry = session.state["team"][task["token"]]["start"]
    assert set(entry) == {"position_seed", "left", "answered"}
    assert "right" not in json.dumps(session.state["team"])
    lines = (tmp_path / "state" / ANSWERS_FILENAME).read_text().splitlines()
    assert [json.loads(line) for line in lines] == [
        {
            "token": task["token"],
            "question": "start",
            "draw_id": entry["position_seed"],
            "position": "right",
        }
    ]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_answer_record_is_never_read_by_the_session(tmp_path: Path) -> None:
    inputs = make_inputs(tmp_path / "frames")
    counter = Counter()
    session = new_session(tmp_path, inputs, counter)
    while (task := session.current_task())["kind"] == "swatch":
        session.submit_swatch(
            task["token"], picks_for(session, rid_of(session, task["token"]))
        )
    task = session.current_task()
    session.submit_team(task["token"], "left")
    answers = tmp_path / "state" / ANSWERS_FILENAME
    answers.chmod(0o200)  # write-only: any read would raise
    try:
        resumed = AnnotationSession(
            inputs, tmp_path / "state", seed_source=counter.seed, id_source=counter.new_id
        )
        while (task := resumed.current_task())["kind"] != "complete":
            if task["kind"] == "team":
                resumed.submit_team(task["token"], "left")
            else:
                resumed.submit_label(task["item_id"], "ink_A")
        with pytest.raises(PermissionError):
            answers.read_text()
    finally:
        answers.chmod(0o600)
    written = export_session_records(tmp_path / "state", tmp_path / "records", tool={})
    assert team_identity_name(1) in written


def test_legacy_state_with_answers_is_rejected(tmp_path: Path) -> None:
    inputs = make_inputs(tmp_path / "frames")
    session = new_session(tmp_path, inputs)
    state = json.loads(json.dumps(session.state))
    state["team"] = {"tok": {"start": {"position_seed": 1, "left": "A", "answer": "A"}}}
    write_json(tmp_path / "state" / STATE_FILENAME, state)
    with pytest.raises(MalformedInput, match="may not hold answers"):
        AnnotationSession(inputs, tmp_path / "state")


def test_server_and_page_never_reference_the_answer_record() -> None:
    for name in ("server.py", "annotate.html"):
        text = (PACKAGE_DIR / name).read_text()
        assert ANSWERS_FILENAME not in text and STATE_FILENAME not in text
    assert "session.state" not in (PACKAGE_DIR / "server.py").read_text()


# ----- sealing-step resolution -------------------------------------------
PROGRESS = {
    "t1": {
        "start": {"position_seed": 11, "left": "B", "answered": True},
        "end": {"position_seed": 12, "left": "A", "answered": True},
    },
    "t2": {"start": {"position_seed": 21, "left": "A", "answered": True}},
}


def _entry(token: str, question: str, draw: int, position: str) -> dict:
    return {"token": token, "question": question, "draw_id": draw, "position": position}


def test_resolution_maps_positions_to_ab_only_at_sealing() -> None:
    resolved = resolve_team_answers(
        PROGRESS,
        [
            _entry("t1", "start", 11, "left"),
            _entry("t1", "end", 12, "left"),
            _entry("t2", "start", 21, CANT_TELL),
        ],
    )
    assert resolved["t1"]["start"]["answer"] == "B"
    assert resolved["t1"]["end"]["answer"] == "A"
    assert resolved["t2"]["start"]["answer"] == CANT_TELL
    flipped = resolve_team_answers(
        {"t1": {"start": PROGRESS["t1"]["start"]}}, [_entry("t1", "start", 11, "right")]
    )
    assert flipped["t1"]["start"]["answer"] == "A"


@pytest.mark.parametrize(
    "entries",
    [
        [
            _entry("t1", "start", 11, "left"),
            _entry("t1", "end", 12, "left"),
        ],  # t2 missing
        [
            _entry("t1", "start", 11, "left"),
            _entry("t1", "start", 11, "right"),
            _entry("t1", "end", 12, "left"),
            _entry("t2", "start", 21, "left"),
        ],
        [
            _entry("t1", "start", 99, "left"),
            _entry("t1", "end", 12, "left"),
            _entry("t2", "start", 21, "left"),
        ],
        [
            _entry("t1", "start", 11, "up"),
            _entry("t1", "end", 12, "left"),
            _entry("t2", "start", 21, "left"),
        ],
        [
            _entry("t1", "start", 11, "left"),
            _entry("t1", "end", 12, "left"),
            _entry("t2", "start", 21, "left"),
            _entry("t3", "start", 31, "left"),
        ],
    ],
)
def test_resolution_fails_closed(entries) -> None:
    with pytest.raises(MalformedInput):
        resolve_team_answers(PROGRESS, entries)


def test_export_rejects_a_malformed_answer_record(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    while (task := session.current_task())["kind"] == "swatch":
        session.submit_swatch(
            task["token"], picks_for(session, rid_of(session, task["token"]))
        )
    task = session.current_task()
    session.submit_team(task["token"], "left")
    answers = tmp_path / "state" / ANSWERS_FILENAME
    answers.write_text(answers.read_text() + "{not json\n")
    with pytest.raises(MalformedInput):
        export_session_records(
            tmp_path / "state", tmp_path / "records", tool={}, allow_incomplete=True
        )
    assert read_json(tmp_path / "state" / STATE_FILENAME)["phase"] == "team_start"
