"""Sealed experiment prompts versus current production: declared drift only.

Experiments H and I sealed user prompts that were byte-identical to production
at their seal. Production has since changed on purpose; each change is listed
here, and a sealed prompt must equal the current one once those changes are
undone. Anything else is undeclared drift.
"""

from __future__ import annotations

import json
from typing import Any

# claim_basis migration: evidence_paths moved from object paths to leaves.
VIEW_DRIFT = ("claim_basis[*].evidence_paths",)


def split_views(text: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Text around each embedded CoachLlmView, and the views in order."""
    decoder = json.JSONDecoder()
    parts: list[str] = []
    views: list[dict[str, Any]] = []
    copied = scan = 0
    while (start := text.find("{", scan)) != -1:
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            scan = start + 1
            continue
        if isinstance(value, dict) and "schema_version" in value:
            parts.append(text[copied:start])
            views.append(value)
            copied = scan = end
        else:
            scan = start + 1
    parts.append(text[copied:])
    return parts, views


def _undo_view_drift(view: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(view))
    for entry in out.get("claim_basis") or []:
        entry.pop("evidence_paths", None)
    return out


def undeclared_drift(sealed: str, current: str) -> list[str]:
    """Differences between a sealed and a current prompt beyond the declared
    drift (empty when the prompts differ only by declared changes)."""
    sealed_text, sealed_views = split_views(sealed)
    current_text, current_views = split_views(current)
    problems = []
    if sealed_text != current_text:
        problems.append("prompt text outside the views differs")
    if len(sealed_views) != len(current_views):
        problems.append("number of views differs")
    for index, (old, new) in enumerate(zip(sealed_views, current_views, strict=False)):
        if _undo_view_drift(old) != _undo_view_drift(new):
            problems.append(f"view {index} differs beyond {', '.join(VIEW_DRIFT)}")
    return problems
