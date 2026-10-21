"""Current-validator rechecks of stored coach-prototype outputs (no LLM call).

Each recorded output gets ``{safe_id}.{model_tag}.revalidation.json``. The
generation-time ``*.flags.json`` holds the original status; it is neither
read nor written here. Writing is all-or-nothing: an existing revalidation
with a different result is a conflict, and nothing is written unless the
operator asks to replace revalidations.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from splatoon3_ai_coach.coach.artifacts import (
    ArtifactError,
    PrototypeOutput,
    Revalidation,
    load_assessment,
    prototype_outputs,
    read_prototype_index,
    read_revalidation,
    write_revalidation,
)
from splatoon3_ai_coach.coach.coach import annotate_claim_flags
from splatoon3_ai_coach.coach.llm_runs import utc_timestamp

_HEADER_KEYS = frozenset({"validation_status", "validation_contract_version"})


class RevalidationOutcome(StrEnum):
    """What applying a plan does to one output's revalidation file."""

    NEW = "new"
    UNCHANGED = "unchanged"
    CONFLICT = "conflict"
    REPLACE = "replace"


@dataclass(frozen=True)
class PlannedRevalidation:
    """Fresh revalidation of one output and how it relates to the stored one."""

    output: PrototypeOutput
    revalidation: Revalidation
    outcome: RevalidationOutcome
    differing: tuple[str, ...] = ()


@dataclass(frozen=True)
class RevalidationPlan:
    """Every output's planned revalidation plus outputs that could not be checked."""

    items: list[PlannedRevalidation]
    errors: list[tuple[str, str]]

    @property
    def conflicts(self) -> list[PlannedRevalidation]:
        """Items whose stored revalidation differs and may not be replaced."""
        return [i for i in self.items if i.outcome is RevalidationOutcome.CONFLICT]

    @property
    def writable(self) -> bool:
        """True when applying cannot overwrite anything unasked or skip an output."""
        return not self.errors and not self.conflicts


def revalidate_output(
    prototype_dir: Path,
    output: PrototypeOutput,
    llm_view_json: str | None,
    revalidated_at: str,
) -> Revalidation:
    """Run today's validator over one stored output and its saved view."""
    output_path = prototype_dir / output.output_json
    output_bytes = output_path.read_bytes()
    assessment = load_assessment(output_path)
    view, view_sha = _saved_view(prototype_dir, llm_view_json)
    flags = annotate_claim_flags(assessment, view)
    details = json.loads(
        json.dumps({k: v for k, v in flags.items() if k not in _HEADER_KEYS})
    )
    return Revalidation(
        validation_contract_version=flags["validation_contract_version"],
        validation_status=flags["validation_status"],
        details=details,
        output_json=output.output_json,
        output_sha256=hashlib.sha256(output_bytes).hexdigest(),
        llm_view_json=llm_view_json if view is not None else None,
        llm_view_sha256=view_sha,
        revalidated_at=revalidated_at,
    )


def plan_revalidations(
    prototype_dir: Path,
    *,
    replace: bool = False,
    revalidated_at: str | None = None,
) -> RevalidationPlan:
    """Revalidate every recorded output in memory; nothing is written.

    Raises:
        ArtifactError: The directory has no coach-prototype index.
    """
    outputs = prototype_outputs(prototype_dir)
    views = _view_names(prototype_dir)
    at = revalidated_at or utc_timestamp()
    items: list[PlannedRevalidation] = []
    errors: list[tuple[str, str]] = []
    for output in outputs:
        try:
            fresh = revalidate_output(
                prototype_dir, output, views.get(output.output_json), at
            )
        except (ArtifactError, OSError, ValueError, ValidationError) as exc:
            errors.append((output.output_json, str(exc)))
            continue
        items.append(_classify(prototype_dir, output, fresh, replace))
    return RevalidationPlan(items=items, errors=errors)


def apply_revalidations(prototype_dir: Path, plan: RevalidationPlan) -> list[Path]:
    """Write new and replacing revalidations; unchanged files are left as they are.

    Raises:
        ArtifactError: The plan has errors or unresolved conflicts.
    """
    if not plan.writable:
        raise ArtifactError(
            f"{prototype_dir}: revalidation not written "
            f"({len(plan.errors)} error(s), {len(plan.conflicts)} conflict(s))"
        )
    write = {RevalidationOutcome.NEW, RevalidationOutcome.REPLACE}
    return [
        write_revalidation(prototype_dir, item.revalidation)
        for item in plan.items
        if item.outcome in write
    ]


def _classify(
    prototype_dir: Path,
    output: PrototypeOutput,
    fresh: Revalidation,
    replace: bool,
) -> PlannedRevalidation:
    try:
        stored = read_revalidation(prototype_dir, output.output_json)
    except ArtifactError:
        differing: tuple[str, ...] = ("unreadable",)
    else:
        if stored is None:
            return PlannedRevalidation(output, fresh, RevalidationOutcome.NEW)
        if stored.same_result(fresh):
            return PlannedRevalidation(output, stored, RevalidationOutcome.UNCHANGED)
        differing = _differing_fields(stored, fresh)
    outcome = RevalidationOutcome.REPLACE if replace else RevalidationOutcome.CONFLICT
    return PlannedRevalidation(output, fresh, outcome, differing)


def _differing_fields(stored: Revalidation, fresh: Revalidation) -> tuple[str, ...]:
    old = stored.model_dump(mode="json", exclude={"revalidated_at"})
    new = fresh.model_dump(mode="json", exclude={"revalidated_at"})
    return tuple(key for key in new if old.get(key) != new[key])


def _saved_view(
    prototype_dir: Path, name: str | None
) -> tuple[dict[str, Any] | None, str | None]:
    path = prototype_dir / name if name else None
    if path is None or not path.is_file():
        return None, None
    data = path.read_bytes()
    try:
        view = json.loads(data)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(view, dict):
        return None, None
    return view, hashlib.sha256(data).hexdigest()


def _view_names(prototype_dir: Path) -> dict[str, str]:
    """The unit's saved ``llm_view.json`` name for each output file name."""
    index = read_prototype_index(prototype_dir)
    names: dict[str, str] = {}
    for entry in index.entries if index is not None else []:
        if entry.llm_view_json is None:
            continue
        for output in entry.outputs:
            names[output.output_json] = entry.llm_view_json
    return names
