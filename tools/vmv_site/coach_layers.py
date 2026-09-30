"""Read saved coaching documents for one scenario.

The site does not rebuild CoachInput or CoachLlmView. ``coach_inputs_meta.json``
is never returned: it contains the server analysis path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from splatoon3_ai_coach.cli.coach_common import safe_filename
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
    row = _index_row(folder, scenario_id)
    safe_id = str(row.get("safe_id")) if row and row.get("safe_id") else safe_filename(scenario_id)
    inputs = folder / "coach_inputs"
    return {
        "scenario_id": scenario_id,
        "coach_input": _named_json(inputs, _row_name(row, "coach_input_json", f"{safe_id}.coach_input.json")),
        "llm_view": _named_json(inputs, _row_name(row, "llm_view_json", f"{safe_id}.llm_view.json")),
        "coaching_unit": _named_json(inputs, _row_name(row, "coaching_json", f"{safe_id}.coaching.json")),
        "system_prompt": load_system_prompt(),
        "prototype_assessments": prototype_assessments(folder, safe_id),
        "llm": llm_label(settings),
    }


def prototype_assessments(folder: Path, safe_id: str) -> list[dict[str, Any]]:
    """Official CoachingAssessment files written by coach-prototype, if any."""
    root = folder / "coach_prototype"
    if not root.is_dir():
        return []
    found: list[dict[str, Any]] = []
    prefix = f"{safe_id}."
    for path in sorted(root.glob(f"{safe_id}.*.output.json")):
        name = path.name
        if not name.startswith(prefix) or not name.endswith(".output.json"):
            continue
        model = name[len(prefix) : -len(".output.json")]
        payload = _read_json(path)
        if isinstance(payload, dict):
            found.append({"model": model, "assessment": payload})
    return found


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


def _index_row(folder: Path, scenario_id: str) -> dict[str, Any] | None:
    path = folder / "coach_inputs" / "coaching_index.json"
    raw = _read_json(path)
    if not isinstance(raw, list):
        return None
    for item in raw:
        if isinstance(item, dict) and item.get("scenario_id") == scenario_id:
            return item
    return None


def _row_name(row: dict[str, Any] | None, key: str, fallback: str) -> str:
    if row and isinstance(row.get(key), str):
        return str(row[key])
    return fallback


def _named_json(folder: Path, name: str) -> Any | None:
    if not name or name != Path(name).name or name in {".", ".."}:
        return None
    return _read_json(folder / name)


def _read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
