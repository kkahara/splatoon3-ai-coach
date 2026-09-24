"""Load activation-study review-queue entries for VMV (study labeling only)."""

from __future__ import annotations

import json
from pathlib import Path

from vision_manifest_viewer.model import SpecialReviewCandidate

DEFAULT_REVIEW_QUEUE = (
    Path(__file__).resolve().parents[2]
    / "analysis"
    / "special_gauge_survey"
    / "activation_study"
    / "review_queue.json"
)


def resolve_review_queue_path(explicit: Path | None) -> Path | None:
    """Return an existing review-queue path, or ``None`` if unavailable."""
    if explicit is not None:
        path = explicit.expanduser().resolve()
        return path if path.is_file() else None
    if DEFAULT_REVIEW_QUEUE.is_file():
        return DEFAULT_REVIEW_QUEUE.resolve()
    return None


def load_review_queue_for_run(
    queue_path: Path, *, run: str
) -> list[SpecialReviewCandidate]:
    """Entries for ``run`` from a ``review_queue.json`` written by the study tool."""
    payload = json.loads(queue_path.read_text(encoding="utf-8"))
    items = payload.get("items") or []
    out: list[SpecialReviewCandidate] = []
    for item in items:
        if item.get("run") != run:
            continue
        out.append(
            SpecialReviewCandidate(
                run=str(item["run"]),
                special=item.get("special"),
                candidate_index=int(item["candidate_index"]),
                population=item["population"],
                peak_time=float(item["peak_time"]),
                trough_time=float(item["trough_time"]),
                decline=float(item["decline"]),
                span_seconds=float(item["span_seconds"]),
                max_single_step=float(item["max_single_step"]),
                peak_fill=float(item["peak_fill"]),
                trough_fill=float(item["trough_fill"]),
                nearest_death_signed_seconds=item.get("nearest_death_signed_seconds"),
                strip_path=item.get("strip_path"),
            )
        )
    return out
