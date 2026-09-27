"""One-scenario LLM experiments. They are not official coaching artifacts."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from splatoon3_ai_coach.coach.coach import (
    parse_coaching_assessment,
    serialize_llm_view_user_prompt,
)
from splatoon3_ai_coach.coach.llm_view import CoachLlmView
from splatoon3_ai_coach.coach.prompts import load_system_prompt
from vmv_site.catalog import run_dir
from vmv_site.coach_layers import coach_layers
from vmv_site.settings import PlatformSettings
from vmv_site.store import new_id, utc_now

Complete = Callable[[str, str], str]


class ExperimentRecord(BaseModel):
    """Self-contained record of one manual CoachLlmView call."""

    experiment: bool = True
    experiment_id: str
    run_id: str
    scenario_id: str
    created_at: str
    provider: str
    model: str
    coach_llm_view: dict[str, Any]
    system_prompt: str
    serialized_user_prompt: str
    result: dict[str, Any] | None = None
    raw_response: str = ""
    parse_error: str | None = None


def list_experiments(
    settings: PlatformSettings, run_id: str, scenario_id: str
) -> list[ExperimentRecord]:
    """Every experiment for one scenario, newest first."""
    folder = _experiments_dir(settings, run_id)
    if not folder.is_dir():
        return []
    records: list[ExperimentRecord] = []
    for path in folder.glob("*.json"):
        if path.name.endswith(".tmp"):
            continue
        try:
            record = ExperimentRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if record.scenario_id == scenario_id:
            records.append(record)
    records.sort(key=lambda item: item.created_at, reverse=True)
    return records


def execute_experiment(
    settings: PlatformSettings,
    run_id: str,
    scenario_id: str,
    *,
    provider: str,
    model: str,
    complete: Complete,
) -> ExperimentRecord:
    """Send the saved CoachLlmView and append a new experiment file.

    Uses the same view deserialization, user-prompt serialization, and system
    prompt as coach-prototype. Does not write coach_prototype or coach_inputs.
    """
    layers = coach_layers(settings, run_id, scenario_id)
    view_payload = layers["llm_view"]
    if not isinstance(view_payload, dict):
        raise FileNotFoundError("run coach-inputs first")
    view = CoachLlmView.model_validate(view_payload)
    system_prompt = load_system_prompt()
    user_prompt = serialize_llm_view_user_prompt(view)
    raw = complete(system_prompt, user_prompt)
    result, parse_error = _parse(raw)
    record = ExperimentRecord(
        experiment_id=new_id(),
        run_id=run_id,
        scenario_id=scenario_id,
        created_at=utc_now(),
        provider=provider,
        model=model,
        coach_llm_view=view.model_dump(mode="json"),
        system_prompt=system_prompt,
        serialized_user_prompt=user_prompt,
        result=result,
        raw_response=raw,
        parse_error=parse_error,
    )
    _write(settings, record)
    return record


def _parse(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        assessment = parse_coaching_assessment(raw)
    except (ValueError, TypeError) as exc:
        return None, str(exc)
    return assessment.model_dump(mode="json"), None


def _experiments_dir(settings: PlatformSettings, run_id: str):
    return run_dir(settings, run_id) / "coach_experiments"


def _write(settings: PlatformSettings, record: ExperimentRecord) -> None:
    folder = _experiments_dir(settings, record.run_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{record.experiment_id}.json"
    text = record.model_dump_json(indent=2) + "\n"
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
