"""Human review marks for scenarios. They do not change the manifest."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from vmv_site.catalog import run_dir
from vmv_site.settings import PlatformSettings

ReviewStatus = Literal[
    "needs_review",
    "evidence_verified",
    "evidence_problem",
    "coaching_problem",
]


class ReviewMark(BaseModel):
    """One human status on a scenario. Not a model score."""

    scenario_id: str
    status: ReviewStatus


class ScenarioReviewFile(BaseModel):
    """Review file stored beside a run."""

    marks: list[ReviewMark] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_scenarios(self) -> ScenarioReviewFile:
        """One mark per scenario."""
        seen: set[str] = set()
        for mark in self.marks:
            if mark.scenario_id in seen:
                raise ValueError(f"duplicate scenario_id: {mark.scenario_id}")
            seen.add(mark.scenario_id)
        return self


def scenario_review_path(settings: PlatformSettings, run_id: str) -> Path:
    """Path derived only from a validated run id."""
    return run_dir(settings, run_id) / "scenario_review.json"


def known_scenario_ids(settings: PlatformSettings, run_id: str) -> set[str]:
    """Scenario ids present on this run's analysis files."""
    folder = run_dir(settings, run_id)
    found: set[str] = set()
    for name in ("scenarios.json", "scenario_contexts.json"):
        found.update(_ids_in(folder / name))
    return found


def read_scenario_review(settings: PlatformSettings, run_id: str) -> ScenarioReviewFile:
    """Load marks, or an empty file when none have been saved."""
    path = scenario_review_path(settings, run_id)
    if not path.is_file():
        return ScenarioReviewFile()
    return ScenarioReviewFile.model_validate_json(path.read_text(encoding="utf-8"))


def write_scenario_review(
    settings: PlatformSettings, run_id: str, payload: ScenarioReviewFile
) -> ScenarioReviewFile:
    """Validate scenario ids, then replace ``scenario_review.json`` atomically."""
    folder = run_dir(settings, run_id)
    if not (folder / "vision_manifest.json").is_file():
        raise KeyError(run_id)
    known = known_scenario_ids(settings, run_id)
    for mark in payload.marks:
        if mark.scenario_id not in known:
            raise ValueError(f"unknown scenario: {mark.scenario_id}")
    path = folder / "scenario_review.json"
    text = payload.model_dump_json(indent=2) + "\n"
    ScenarioReviewFile.model_validate_json(text)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return payload


def _ids_in(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    if not isinstance(raw, list):
        return set()
    return {
        str(item["scenario_id"])
        for item in raw
        if isinstance(item, dict) and item.get("scenario_id")
    }
