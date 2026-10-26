"""Session record export: the files that are committed (sealed).

Keys (token → recording, point_ref → point) are written to separate files so
labels can be committed before the keys, as the sealing order requires.

This is the sealing step of Amendment 4's non-exposure rule: the only place
the session's append-only answer record is read and each opaque answer
(on-screen position + draw id) is resolved to ``A`` / ``B`` / ``cant_tell``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from map_ink_reference.constants import CANT_TELL, STAGE_1, SWATCH_IDS
from map_ink_reference.errors import MalformedInput, ProcedureOrderError
from map_ink_reference.io_utils import read_json, write_json
from map_ink_reference.session import ANSWERS_FILENAME, STATE_FILENAME, TEAM_CHOICES

SWATCHES = "swatches.json"
TOKEN_KEY = "token_key.json"


def team_identity_name(stage: int) -> str:
    """Team-identity record file name for a stage."""
    return f"team_identity_stage{stage}.json"


def labels_name(stage: int) -> str:
    """Labels record file name for a stage."""
    return f"labels_stage{stage}.json"


def queue_name(stage: int) -> str:
    """Planned-queue record file name for a stage."""
    return f"queue_stage{stage}.json"


def queue_key_name(stage: int) -> str:
    """Point-reference key file name for a stage."""
    return f"queue_key_stage{stage}.json"


def _read_entries(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MalformedInput(f"{path.name} line {number} is not JSON") from exc
        if not isinstance(entry, dict) or set(entry) != {
            "token",
            "question",
            "draw_id",
            "position",
        }:
            raise MalformedInput(f"{path.name} line {number} has unexpected content")
        entries.append(entry)
    return entries


def _resolve(entry: Mapping[str, Any], progress: Mapping[str, Any]) -> str:
    if (
        progress.get("answered") is not True
        or progress.get("position_seed") != entry["draw_id"]
    ):
        raise MalformedInput(
            "answer does not match the question's recorded position draw"
        )
    position = entry["position"]
    if position not in TEAM_CHOICES:
        raise MalformedInput(f"invalid recorded position {position!r}")
    if position == CANT_TELL:
        return CANT_TELL
    left = progress["left"]
    return left if position == "left" else SWATCH_IDS[1 - SWATCH_IDS.index(left)]


def resolve_team_answers(
    team_progress: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Exactly one recorded entry per answered question, resolved to A/B/cant_tell."""
    out: dict[str, Any] = {}
    for entry in entries:
        token, question = entry["token"], entry["question"]
        progress = team_progress.get(token, {}).get(question)
        if progress is None:
            raise MalformedInput("answer for a question that was never presented")
        resolved = out.setdefault(token, {})
        if question in resolved:
            raise MalformedInput("more than one answer recorded for a question")
        resolved[question] = {
            "position_seed": progress["position_seed"],
            "left": progress["left"],
            "answer": _resolve(entry, progress),
        }
    for token, questions in team_progress.items():
        for question, progress in questions.items():
            answered = question in out.get(token, {})
            if progress.get("answered") is True and not answered:
                raise MalformedInput("an answered question has no recorded answer")
    return out


def export_session_records(
    state_dir: Path,
    out_dir: Path,
    *,
    tool: Mapping[str, Any],
    allow_incomplete: bool = False,
    scope: str | None = None,
) -> dict[str, str]:
    """Write the stage's session records; return ``{file name: sha256}``."""
    state = read_json(state_dir / STATE_FILENAME)
    if state["phase"] != "complete" and not allow_incomplete:
        raise ProcedureOrderError("session is not complete")
    team = resolve_team_answers(
        state["team"], _read_entries(state_dir / ANSWERS_FILENAME)
    )
    stage = int(state["stage"])
    common = {"stage": stage, "tool": dict(tool)}
    if scope is not None:
        common["scope"] = scope
    queue = state["queue"] or []
    labels = state["labels"]
    written = {
        labels_name(stage): write_json(
            out_dir / labels_name(stage),
            {
                **common,
                "session_phase": state["phase"],
                "point_manifest_sha256": state["point_manifest_sha256"],
                "labels": [
                    {
                        "item_id": item["item_id"],
                        "token": item["token"],
                        "label": labels.get(item["item_id"]),
                    }
                    for item in queue
                ],
            },
        ),
        team_identity_name(stage): write_json(
            out_dir / team_identity_name(stage), {**common, "team": team}
        ),
        queue_name(stage): write_json(
            out_dir / queue_name(stage),
            {
                **common,
                "queue_seed": state["queue_seed"],
                "items": [
                    {k: item[k] for k in ("item_id", "token", "point_ref", "copy")}
                    for item in queue
                ],
            },
        ),
        queue_key_name(stage): write_json(
            out_dir / queue_key_name(stage),
            {**common, "point_refs": state["point_refs"] or {}},
        ),
    }
    if stage == STAGE_1:
        written[SWATCHES] = write_json(
            out_dir / SWATCHES, {**common, "swatches": state["swatches"]}
        )
        written[TOKEN_KEY] = write_json(
            out_dir / TOKEN_KEY, {**common, "tokens": state["tokens"]}
        )
    return written
