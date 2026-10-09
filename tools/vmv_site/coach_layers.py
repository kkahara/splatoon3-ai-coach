"""Read saved coaching documents for one scenario.

The site does not rebuild CoachInput or CoachLlmView. ``coach_inputs_meta.json``
is never returned: it contains the server analysis path. File layout and
model-output selection come from ``coach.artifacts``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from splatoon3_ai_coach.coach.artifacts import (
    CoachingIndexEntry,
    coach_inputs_dir,
    coach_inputs_staleness,
    coach_prototype_dir,
    read_coaching_index_or_none,
    read_document,
    safe_filename,
    unit_assessments,
    unit_file_names,
)
from splatoon3_ai_coach.coach.llm_client import normalize_coach_provider
from splatoon3_ai_coach.coach.prompts import load_system_prompt
from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.settings import CoachSettings
from splatoon3_ai_coach.exceptions import ConfigError
from vmv_site.catalog import run_dir
from vmv_site.settings import PlatformSettings


def coach_layers(settings: PlatformSettings, run_id: str, scenario_id: str) -> dict[str, Any]:
    """Saved CoachInput, CoachLlmView, unit, prototype assessments, and the prompt."""
    folder = run_dir(settings, run_id)
    inputs = coach_inputs_dir(folder)
    entry = _index_entry(inputs, scenario_id)
    safe_id = entry.safe_id if entry else safe_filename(scenario_id)
    names = unit_file_names(safe_id)
    view = read_document(
        inputs, (entry.llm_view_json if entry else None) or names.llm_view
    )
    roster_evidence = isinstance(view, dict) and view.get("roster_evidence") is True
    return {
        "scenario_id": scenario_id,
        "coach_input": read_document(
            inputs, entry.coach_input_json if entry else names.coach_input
        ),
        "llm_view": view,
        "coaching_unit": read_document(
            inputs, entry.coaching_json if entry else names.coaching
        ),
        "system_prompt": load_system_prompt(roster_evidence=roster_evidence),
        "prototype_assessments": prototype_assessments(folder, safe_id),
        "llm": llm_label(settings),
        "staleness": staleness_payload(inputs, folder),
    }


def staleness_payload(inputs: Path, folder: Path) -> dict[str, str | None] | None:
    """``{status, reason, message}`` for saved coach inputs; None without an index."""
    staleness = coach_inputs_staleness(inputs, folder)
    if staleness is None:
        return None
    return {
        "status": staleness.status,
        "reason": staleness.reason.value if staleness.reason else None,
        "message": staleness.message() or None,
    }


def prototype_assessments(folder: Path, safe_id: str) -> list[dict[str, Any]]:
    """CoachingAssessment files written by coach-prototype, primary first."""
    found = unit_assessments(coach_prototype_dir(folder), safe_id)
    return [
        {
            "model": item.model,
            "primary": item.primary,
            "assessment": item.assessment.model_dump(mode="json"),
        }
        for item in found
    ]


def llm_label(settings: PlatformSettings) -> dict[str, str | None]:
    """Provider and model from config and environment. No secrets."""
    try:
        config = load_config(settings.config_path)
    except (ConfigError, OSError):
        return {"provider": None, "model": None}
    try:
        provider = normalize_coach_provider(CoachSettings().llm_provider or config.coach.provider)
    except ValueError:
        return {"provider": None, "model": None}
    model = {
        "nvidia": config.coach.nvidia_model,
        "cursor": config.coach.cursor_model,
    }.get(provider, config.coach.model)
    return {"provider": provider, "model": model}


def _index_entry(inputs: Path, scenario_id: str) -> CoachingIndexEntry | None:
    index = read_coaching_index_or_none(inputs)
    return index.by_scenario_id(scenario_id) if index else None
