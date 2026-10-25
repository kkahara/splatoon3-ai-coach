"""Post-sealing A/B → ally/opponent mapping (unblinded stage only)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from map_ink_reference.constants import CANT_TELL, LABELS, SWATCH_IDS
from map_ink_reference.errors import MalformedInput

ALLY = "ally"
OPPONENT = "opponent"
OTHER = "other"


def team_answers(
    team_records: Sequence[Mapping[str, Any]], token: str
) -> list[str | None]:
    """Answers in order (start, end per stage record); missing → None."""
    answers: list[str | None] = []
    for record in team_records:
        entry = record.get("team", {}).get(token, {})
        for key in ("start", "end"):
            answers.append(entry.get(key, {}).get("answer"))
    return answers


def consistent_own_swatch(answers: Sequence[str | None]) -> str | None:
    """The own-team swatch iff every answer independently picks the same one."""
    if not answers or any(a is None or a == CANT_TELL for a in answers):
        return None
    for answer in answers:
        if answer not in SWATCH_IDS:
            raise MalformedInput(f"invalid team-identity answer {answer!r}")
    return answers[0] if len(set(answers)) == 1 else None


def reference_class(label: str, own_swatch: str) -> str | None:
    """Map a neutral label to ally / opponent / other (cant_tell → None)."""
    if label not in LABELS:
        raise MalformedInput(f"unknown label {label!r}")
    if own_swatch not in SWATCH_IDS:
        raise MalformedInput(f"invalid own swatch {own_swatch!r}")
    if label == CANT_TELL:
        return None
    if label == "not_ink":
        return OTHER
    return ALLY if label == f"ink_{own_swatch}" else OPPONENT
