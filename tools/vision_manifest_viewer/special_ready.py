"""VMV wrapper around shared special-ready onset derivation."""

from __future__ import annotations

from splatoon3_ai_coach.analysis.special_ready_markers import (
    ReadySample,
    derive_special_ready_onsets,
)
from vision_manifest_viewer.model import ObservationView, SpecialReadyMarker


def derive_special_ready_markers(
    observations: list[ObservationView],
) -> list[SpecialReadyMarker]:
    """Emit one VMV marker per observed ``ready`` false→true onset.

    Delegates to the shared analysis helper. Presentation-only — not a
    ``GameEvent`` and not fusion ``SPECIAL_READY``.
    """
    samples: list[ReadySample] = []
    for obs in observations:
        if obs.detector != "special_gauge":
            continue
        ready = obs.reading.get("ready") if isinstance(obs.reading, dict) else None
        if ready is not None and not isinstance(ready, bool):
            ready = None
        samples.append(
            ReadySample(
                video_time=float(obs.timestamp),
                ready=ready,
                observation_id=obs.id,
            )
        )
    return [
        SpecialReadyMarker(
            timestamp=m.video_time,
            kind="special_ready",
            observation_id=m.observation_id,
        )
        for m in derive_special_ready_onsets(samples)
    ]
