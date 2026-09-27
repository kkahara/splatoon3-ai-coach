"""Ground-truth review annotations. They do not change the manifest."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from vmv_site.catalog import run_dir
from vmv_site.settings import PlatformSettings

ALLOWED_LABELS = frozenset(
    {
        "unknown",
        "real_death",
        "not_a_death",
        "damage_recovery",
        "results_screen",
        "lobby",
        "real_splat",
        "not_a_splat",
        "real_active_gameplay",
        "not_active_gameplay",
        "real_respawn",
        "not_a_respawn",
        "real_map_overlay",
        "not_a_map_overlay",
        "real_timer",
        "not_a_timer",
        "special_used",
        "not_a_special_used",
        "uncertain",
        "intro",
        "opening_countdown",
        "in_match",
        "post_match",
        "results_lobby",
    }
)


class GroundTruthInterval(BaseModel):
    """One human mark on a cadence time."""

    t0: float
    t1: float
    detector: str
    label: str

    @model_validator(mode="after")
    def _check_interval(self) -> GroundTruthInterval:
        """Reject labels outside the existing viewer vocabulary."""
        if self.label not in ALLOWED_LABELS:
            raise ValueError(f"unknown ground-truth label: {self.label}")
        if self.t1 < self.t0:
            raise ValueError("t1 is before t0")
        return self


class GroundTruthFile(BaseModel):
    """Review file stored beside a run. Detector output stays in the manifest."""

    intervals: list[GroundTruthInterval] = Field(default_factory=list)
    video_label: str | None = None
    exported_at: str | None = None


def ground_truth_path(settings: PlatformSettings, run_id: str) -> Path:
    """Path derived only from a validated run id."""
    return run_dir(settings, run_id) / "ground_truth.json"


def read_ground_truth(settings: PlatformSettings, run_id: str) -> GroundTruthFile:
    """Load annotations, or an empty file when none have been saved."""
    path = ground_truth_path(settings, run_id)
    if not path.is_file():
        return GroundTruthFile(video_label=run_id)
    return GroundTruthFile.model_validate_json(path.read_text(encoding="utf-8"))


def write_ground_truth(
    settings: PlatformSettings, run_id: str, payload: GroundTruthFile
) -> GroundTruthFile:
    """Validate, then replace ``ground_truth.json`` atomically."""
    folder = run_dir(settings, run_id)
    if not (folder / "vision_manifest.json").is_file():
        raise KeyError(run_id)
    path = folder / "ground_truth.json"
    text = payload.model_dump_json(indent=2) + "\n"
    GroundTruthFile.model_validate_json(text)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return payload
