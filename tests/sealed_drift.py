"""Sealed experiment prompts versus current production: declared drift only.

Experiments H and I sealed user prompts that were byte-identical to production
at their seal. Production has since changed on purpose; each change is listed
here, and a sealed prompt must equal the current one once those changes are
undone. Anything else is undeclared drift.
"""

from __future__ import annotations

import json
import subprocess
from functools import cache
from typing import Any

from splatoon3_ai_coach.coach.prompts import load_system_prompt
from splatoon3_ai_coach.config.paths import PROJECT_ROOT

# claim_basis migration: evidence_paths moved from object paths to leaves.
VIEW_DRIFT = ("claim_basis[*].evidence_paths",)
# Fix 3 (N2-only citation instructions): the user preamble sentence and the
# system prompt. PRE_FIX3 is the last commit with the earlier prompt.
PRE_FIX3 = "7008a4c"
PREAMBLE_DRIFT = (
    "In evidence_used, cite each fact as its exact CoachLlmView path = value. ",
    "Cite source_path audit references when listing evidence_used. ",
)
ROSTER_DRIFT = (
    "When describing roster.player_count_context, prefer:",
    "When citing player_count_context, prefer:",
)
_SYSTEM_PROMPT_FILE = "src/splatoon3_ai_coach/coach/prompts/coach_system.txt"


@cache
def pre_fix3_system_prompt() -> str:
    """The production system prompt before Fix 3, from git."""
    return subprocess.run(
        ["git", "show", f"{PRE_FIX3}:{_SYSTEM_PROMPT_FILE}"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def undeclared_system_drift(sealed: str, current: str) -> list[str]:
    """A sealed arm system prompt must equal the current one with the
    production base prompt replaced by its pre-Fix-3 text."""
    base = load_system_prompt().rstrip()
    if base not in current:
        return ["current system prompt does not contain the production prompt"]
    undone = current.replace(base, pre_fix3_system_prompt().rstrip())
    undone = undone.replace(*ROSTER_DRIFT)
    return [] if undone == sealed else ["system prompt differs beyond Fix 3"]


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
    current_text, current_views = split_views(current.replace(*PREAMBLE_DRIFT))
    problems = []
    if sealed_text != current_text:
        problems.append("prompt text outside the views differs")
    if len(sealed_views) != len(current_views):
        problems.append("number of views differs")
    for index, (old, new) in enumerate(zip(sealed_views, current_views, strict=False)):
        if _undo_view_drift(old) != _undo_view_drift(new):
            problems.append(f"view {index} differs beyond {', '.join(VIEW_DRIFT)}")
    return problems
