"""Annotation session state machine (blind).

Phases: Stage 1 ``swatch`` → ``team_start`` (J1) → ``labelling`` →
``team_end`` (J2) → ``complete``. Stage 2 reuses the sealed Stage 1 swatches
and runs ``team_start`` (J3) → ``labelling`` → ``team_end`` (J4).

Views (``current_task``) expose only opaque handles, the token, progress and
the question. They never contain an earlier team-identity answer, the A/B
→ team mapping, timestamps, recording names, hue values or classifier
output. A/B identity is drawn once per recording and never changes; only
on-screen positions are re-drawn for each team-identity question.

Amendment 4 non-exposure: persistent progress state records, per question,
only whether it was answered and its position draw. The answer itself is
appended, in opaque form (chosen on-screen position + draw id), to a separate
answer record that this session only ever opens for appending. It is
resolved to ``A`` / ``B`` / ``cant_tell`` only at the sealing step
(``records.export_session_records``).
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from map_ink_reference.constants import (
    LABELS,
    STAGE_1,
    STAGE_2,
    SWATCH_IDS,
    SWATCH_S_MIN,
    SWATCH_V_MIN,
)
from map_ink_reference.errors import MalformedInput, ProcedureOrderError
from map_ink_reference.io_utils import read_json, write_json
from map_ink_reference.points import load_verified_png
from map_ink_reference.queueing import build_queue
from map_ink_reference.render import (
    DisplaySettings,
    encode_png,
    padded_crop,
    point_view,
    swatch_view,
    zoom_nearest,
)

STATE_FILENAME = "session_state.json"
ANSWERS_FILENAME = "team_answers.jsonl"
TEAM_CHOICES = ("left", "right", "cant_tell")
_SWATCH_RECORD_KEYS = {"status", "pixels", "assignment", "draw_seed"}
_TEAM_PROGRESS_KEYS = {"position_seed", "left", "answered"}


@dataclass(frozen=True)
class FrameAsset:
    """An extracted, hash-pinned frame image."""

    frame_id: str
    png_path: Path
    png_sha256: str


@dataclass(frozen=True)
class RecordingAssets:
    """Frames a session may show for one recording at this stage."""

    selected: tuple[FrameAsset, ...]
    cues: tuple[FrameAsset, ...]
    # Stage 2 only: the Stage 1 frames holding this recording's swatch pixels.
    swatch_frames: tuple[FrameAsset, ...] = ()


@dataclass(frozen=True)
class SessionPoint:
    """One sampled point (from the committed point manifest)."""

    point_id: str
    recording_id: str
    frame_id: str
    x: int
    y: int


@dataclass
class SessionInputs:
    """Everything a session may know. No calibration, mapping or answers."""

    stage: int
    recordings: dict[str, RecordingAssets]
    points: list[SessionPoint]
    duplicate_ids: list[str]
    point_manifest_sha256: str
    tokens: dict[str, str] | None = None
    swatches: dict[str, dict[str, Any]] | None = None
    settings: DisplaySettings = field(default_factory=DisplaySettings)


def default_seed() -> int:
    """64-bit seed from system randomness (recorded wherever used)."""
    return secrets.randbits(64)


def default_id() -> str:
    """Opaque random identifier."""
    return secrets.token_hex(8)


def load_sealed_swatches(path: Path) -> dict[str, dict[str, Any]]:
    """Read the Stage 1 swatch record for Stage 2; reject anything else in it."""
    payload = read_json(path)
    swatches = payload.get("swatches") if isinstance(payload, dict) else None
    extra = (
        set(payload) - {"swatches", "stage", "tool"}
        if isinstance(payload, dict)
        else set()
    )
    if swatches is None or extra:
        raise MalformedInput(
            f"{path} is not a swatch-only record (extra keys {sorted(extra)})"
        )
    for token, record in swatches.items():
        if set(record) != _SWATCH_RECORD_KEYS:
            raise MalformedInput(f"swatch record for {token} has unexpected keys")
    return swatches


def _pixel_passes(image: np.ndarray, x: int, y: int) -> bool:
    hsv = cv2.cvtColor(image[y : y + 1, x : x + 1], cv2.COLOR_BGR2HSV)[0, 0]
    return int(hsv[1]) >= SWATCH_S_MIN and int(hsv[2]) >= SWATCH_V_MIN


class AnnotationSession:
    """Persistent, resumable annotation session for one stage."""

    def __init__(
        self,
        inputs: SessionInputs,
        state_dir: Path,
        *,
        seed_source: Callable[[], int] = default_seed,
        id_source: Callable[[], str] = default_id,
    ) -> None:
        if inputs.stage not in (STAGE_1, STAGE_2):
            raise MalformedInput(f"unknown stage {inputs.stage}")
        if inputs.stage == STAGE_2 and (inputs.swatches is None or inputs.tokens is None):
            raise MalformedInput("Stage 2 requires sealed swatches and tokens")
        self.inputs = inputs
        self.state_path = state_dir / STATE_FILENAME
        self._answers_path = state_dir / ANSWERS_FILENAME
        self._seed = seed_source
        self._new_id = id_source
        self._images: dict[str, Callable[[], bytes]] = {}
        self._points = {p.point_id: p for p in inputs.points}
        self._frames = self._index_frames()
        self.state = (
            read_json(self.state_path) if self.state_path.is_file() else self._new()
        )
        self._check_state()
        self._save()

    # ----- state -------------------------------------------------------
    def _index_frames(self) -> dict[tuple[str, str], FrameAsset]:
        index: dict[tuple[str, str], FrameAsset] = {}
        for rid, assets in self.inputs.recordings.items():
            for asset in (*assets.selected, *assets.cues, *assets.swatch_frames):
                index[(rid, asset.frame_id)] = asset
        return index

    def _new(self) -> dict[str, Any]:
        stage1 = self.inputs.stage == STAGE_1
        tokens = self._fresh_tokens() if stage1 else dict(self.inputs.tokens or {})
        return {
            "stage": self.inputs.stage,
            "point_manifest_sha256": self.inputs.point_manifest_sha256,
            "tokens": tokens,
            "phase": "swatch" if stage1 else "team_start",
            "swatches": {} if stage1 else dict(self.inputs.swatches or {}),
            "team": {},
            "queue": None,
            "queue_seed": None,
            "point_refs": None,
            "labels": {},
        }

    def _fresh_tokens(self) -> dict[str, str]:
        tokens = {rid: self._new_id() for rid in sorted(self.inputs.recordings)}
        if len(set(tokens.values())) != len(tokens):
            raise MalformedInput("recording tokens collided")
        return tokens

    def _check_state(self) -> None:
        if self.state.get("stage") != self.inputs.stage:
            raise MalformedInput("session state belongs to another stage")
        if self.state.get("point_manifest_sha256") != self.inputs.point_manifest_sha256:
            raise MalformedInput("session state belongs to another point manifest")
        unknown = set(self.state["tokens"]) - set(self.inputs.recordings)
        if unknown:
            raise MalformedInput(f"tokens for unknown recordings: {sorted(unknown)}")
        for questions in self.state["team"].values():
            for entry in questions.values():
                if not set(entry) <= _TEAM_PROGRESS_KEYS:
                    raise MalformedInput("team progress state may not hold answers")

    def _save(self) -> None:
        write_json(self.state_path, self.state)

    def _append_answer(self, entry: dict[str, Any]) -> None:
        """Append-only write; this session never reads the answer record."""
        self._answers_path.parent.mkdir(parents=True, exist_ok=True)
        with self._answers_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _token_order(self) -> list[tuple[str, str]]:
        return sorted(((tok, rid) for rid, tok in self.state["tokens"].items()))

    def _valid_tokens(self) -> list[tuple[str, str]]:
        return [
            (tok, rid)
            for tok, rid in self._token_order()
            if self.state["swatches"].get(tok, {}).get("status") == "valid"
        ]

    # ----- images ------------------------------------------------------
    def _handle(self, render: Callable[[], bytes]) -> str:
        handle = secrets.token_urlsafe(9)
        self._images[handle] = render
        return handle

    def image(self, handle: str) -> bytes:
        """PNG bytes for an opaque handle from the current view."""
        render = self._images.get(handle)
        if render is None:
            raise MalformedInput("unknown image handle")
        return render()

    def _load(self, asset: FrameAsset) -> np.ndarray:
        return load_verified_png(asset.png_path, asset.png_sha256)

    def _full_frame(self, asset: FrameAsset) -> str:
        return self._handle(lambda: encode_png(self._load(asset)))

    def _swatch_image(self, rid: str, token: str, swatch_id: str) -> str:
        record = self.state["swatches"][token]
        pixel = record["pixels"][record["assignment"][swatch_id]]
        asset = self._frames[(rid, pixel["frame_id"])]
        zoom = self.inputs.settings.zoom
        return self._handle(
            lambda: encode_png(
                swatch_view(self._load(asset), pixel["x"], pixel["y"], zoom)
            )
        )

    # ----- views -------------------------------------------------------
    def current_task(self) -> dict[str, Any]:
        """The only information the annotator sees for the next step."""
        self._images.clear()
        phase = self.state["phase"]
        if phase == "swatch":
            return self._swatch_task()
        if phase in ("team_start", "team_end"):
            return self._team_task(phase)
        if phase == "labelling":
            return self._label_task()
        return {"kind": "complete"}

    def _pending_swatch(self) -> tuple[str, str] | None:
        for tok, rid in self._token_order():
            if tok not in self.state["swatches"]:
                return tok, rid
        return None

    def _swatch_task(self) -> dict[str, Any]:
        tok, rid = self._pending_swatch() or (None, None)
        if tok is None:
            raise ProcedureOrderError("no pending swatch task")
        frames = self.inputs.recordings[rid].selected
        return {
            "kind": "swatch",
            "token": tok,
            "frames": [
                {"index": i, "image": self._full_frame(a)} for i, a in enumerate(frames)
            ],
        }

    def _pending_team(self, phase: str) -> tuple[str, str] | None:
        key = "start" if phase == "team_start" else "end"
        for tok, rid in self._valid_tokens():
            if self.state["team"].get(tok, {}).get(key, {}).get("answered") is not True:
                return tok, rid
        return None

    def _team_task(self, phase: str) -> dict[str, Any]:
        tok, rid = self._pending_team(phase) or (None, None)
        if tok is None:
            raise ProcedureOrderError("no pending team-identity question")
        key = "start" if phase == "team_start" else "end"
        entry = self.state["team"].setdefault(tok, {}).setdefault(key, {})
        if "left" not in entry:
            seed = self._seed()
            draw = int(np.random.Generator(np.random.PCG64(seed)).integers(2))
            entry.update({"position_seed": seed, "left": SWATCH_IDS[draw]})
            self._save()
        left = entry["left"]
        right = SWATCH_IDS[1 - SWATCH_IDS.index(left)]
        assets = self.inputs.recordings[rid]
        return {
            "kind": "team",
            "token": tok,
            "question": "Which swatch is my team's colour?",
            "map_frames": [self._full_frame(a) for a in assets.selected],
            "gameplay_frames": [self._full_frame(a) for a in assets.cues],
            "swatches": [
                self._swatch_image(rid, tok, left),
                self._swatch_image(rid, tok, right),
            ],
            "choices": ["left", "right", "cant_tell"],
        }

    def _label_task(self) -> dict[str, Any]:
        queue = self.state["queue"]
        index = len(self.state["labels"])
        item = queue[index]
        point = self._points[self.state["point_refs"][item["point_ref"]]]
        token = item["token"]
        rid = point.recording_id
        asset = self._frames[(rid, point.frame_id)]
        settings = self.inputs.settings
        return {
            "kind": "label",
            "item_id": item["item_id"],
            "token": token,
            "crop": self._handle(
                lambda: encode_png(
                    point_view(self._load(asset), point.x, point.y, settings)
                )
            ),
            "swatch_A": self._swatch_image(rid, token, "A"),
            "swatch_B": self._swatch_image(rid, token, "B"),
            "labels": list(LABELS),
            "progress": [index, len(queue)],
        }

    def loupe(self, frame_index: int, x: int, y: int) -> dict[str, Any]:
        """Zoomed, unmarked crop of a swatch-task frame for precise pixel picking."""
        pending = self._pending_swatch()
        if self.state["phase"] != "swatch" or pending is None:
            raise ProcedureOrderError("loupe is only available while picking swatches")
        asset = self.inputs.recordings[pending[1]].selected[int(frame_index)]
        size, zoom = self.inputs.settings.crop_size, self.inputs.settings.zoom
        handle = self._handle(
            lambda: encode_png(
                zoom_nearest(padded_crop(self._load(asset), x, y, size), zoom)
            )
        )
        return {"image": handle, "origin": [x - size // 2, y - size // 2], "zoom": zoom}

    # ----- submissions -------------------------------------------------
    def submit_swatch(
        self, token: str, picks: Sequence[Mapping[str, int]] | None
    ) -> None:
        """Record two pixels (frame index, x, y) or ``None`` for cant_tell."""
        pending = self._pending_swatch()
        if self.state["phase"] != "swatch" or pending is None or pending[0] != token:
            raise ProcedureOrderError("swatch submission out of order")
        rid = pending[1]
        if picks is None:
            record = {
                "status": "cant_tell",
                "pixels": [],
                "assignment": {},
                "draw_seed": None,
            }
        else:
            record = self._valid_swatch_record(rid, picks)
        self.state["swatches"][token] = record
        if self._pending_swatch() is None:
            self.state["phase"] = "team_start"
            if not self._valid_tokens():
                self._enter_labelling()
        self._save()

    def _valid_swatch_record(self, rid: str, picks: Sequence[Mapping[str, int]]) -> dict:
        if len(picks) != 2:
            raise MalformedInput("exactly two swatch pixels are required")
        frames = self.inputs.recordings[rid].selected
        pixels = []
        for pick in picks:
            asset = frames[int(pick["frame_index"])]
            x, y = int(pick["x"]), int(pick["y"])
            image = self._load(asset)
            if not (0 <= x < image.shape[1] and 0 <= y < image.shape[0]):
                raise MalformedInput("swatch pixel outside the frame")
            if not _pixel_passes(image, x, y):
                raise MalformedInput("swatch pixel fails the swatch-quality requirement")
            pixels.append({"frame_id": asset.frame_id, "x": x, "y": y})
        seed = self._seed()
        draw = int(np.random.Generator(np.random.PCG64(seed)).integers(2))
        assignment = {"A": draw, "B": 1 - draw}
        return {
            "status": "valid",
            "pixels": pixels,
            "assignment": assignment,
            "draw_seed": seed,
        }

    def submit_team(self, token: str, choice: str) -> None:
        """Record a team-identity choice (on-screen position) in the answer record."""
        phase = self.state["phase"]
        pending = (
            self._pending_team(phase) if phase in ("team_start", "team_end") else None
        )
        if pending is None or pending[0] != token:
            raise ProcedureOrderError("team-identity submission out of order")
        key = "start" if phase == "team_start" else "end"
        entry = self.state["team"].get(token, {}).get(key, {})
        if "left" not in entry:
            raise ProcedureOrderError("question was not presented")
        if choice not in TEAM_CHOICES:
            raise MalformedInput(f"invalid team-identity choice {choice!r}")
        self._append_answer(
            {
                "token": token,
                "question": key,
                "draw_id": entry["position_seed"],
                "position": choice,
            }
        )
        entry["answered"] = True
        if self._pending_team(phase) is None:
            self._advance_after_team(phase)
        self._save()

    def _advance_after_team(self, phase: str) -> None:
        if phase == "team_start":
            self._enter_labelling()
        else:
            self.state["phase"] = "complete"

    def _enter_labelling(self) -> None:
        valid = {tok for tok, _ in self._valid_tokens()}
        token_of = self.state["tokens"]
        point_ids = [
            p.point_id for p in self.inputs.points if token_of[p.recording_id] in valid
        ]
        seed = self._seed()
        queue, key = build_queue(point_ids, self.inputs.duplicate_ids, seed, self._new_id)
        for item in queue:
            item["token"] = token_of[self._points[key[item["point_ref"]]].recording_id]
        self.state.update(
            {"queue": queue, "queue_seed": seed, "point_refs": key, "phase": "labelling"}
        )
        if not queue:
            self.state["phase"] = "team_end" if valid else "complete"

    def submit_label(self, item_id: str, label: str) -> None:
        """Record the label of the current item (strictly sequential, final)."""
        if self.state["phase"] != "labelling":
            raise ProcedureOrderError("not labelling")
        if label not in LABELS:
            raise MalformedInput(f"label must be one of {LABELS}")
        queue = self.state["queue"]
        index = len(self.state["labels"])
        if queue[index]["item_id"] != item_id:
            raise ProcedureOrderError("label submitted for a non-current item")
        self.state["labels"][item_id] = label
        if len(self.state["labels"]) == len(queue):
            self.state["phase"] = "team_end"
        self._save()
