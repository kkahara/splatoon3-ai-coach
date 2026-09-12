"""Presentation-only special-ready onset markers from persisted ready flags.

These markers describe presentation of the special-ready state.
They are NOT GameEvents and must never be promoted to SPECIAL_READY
or SPECIAL_USED GameEvents.

Shared by ScenarioContext enrichment and the Vision Manifest Viewer.
Deterministic; no OpenCV; no detector imports; no fusion.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field


class ReadySample(BaseModel):
    """Minimal persisted ready observation for onset derivation."""

    video_time: float = Field(ge=0)
    ready: bool | None = None
    observation_id: str | None = None


class SpecialReadyOnsetMarker(BaseModel):
    """One observed ``ready`` false→true (or first-true) onset.

    Presentation evidence only — not a ``GameEvent``.
    """

    video_time: float = Field(ge=0)
    observation_id: str | None = None


def derive_special_ready_onsets(
    samples: Sequence[ReadySample],
) -> list[SpecialReadyOnsetMarker]:
    """Emit one marker per observed ``ready`` false→true onset.

    Only samples with a boolean ``ready`` participate. Missing/``None`` ready
    is skipped (never treated as False). Gaps between sparse samples do not
    invent state. First observation already ``True`` counts as an onset.
    """
    ordered = sorted(samples, key=lambda s: s.video_time)
    prev_ready: bool | None = None
    markers: list[SpecialReadyOnsetMarker] = []
    for sample in ordered:
        ready = sample.ready
        if ready is None:
            continue
        if not isinstance(ready, bool):
            continue
        if ready and (prev_ready is None or prev_ready is False):
            markers.append(
                SpecialReadyOnsetMarker(
                    video_time=float(sample.video_time),
                    observation_id=sample.observation_id,
                )
            )
        prev_ready = ready
    return markers
