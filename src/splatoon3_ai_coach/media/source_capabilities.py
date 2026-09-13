"""Evidence-kind observability by video acquisition source.

Source determination is run-level; these tables are evidence-specific.
``source = REVIEW`` does not mean everything is unreliable — only that each
evidence kind is evaluated under Review characteristics.
"""

from __future__ import annotations

from splatoon3_ai_coach.media.video_source import Observability, VideoSource

MAP_OVERLAY_OBSERVABILITY: dict[VideoSource, Observability] = {
    VideoSource.SCREEN_CAPTURE: Observability.OBSERVABLE,
    VideoSource.REVIEW: Observability.UNOBSERVABLE,
    VideoSource.HAND_CAPTURE: Observability.POTENTIALLY_OBSERVABLE,
}


def map_overlay_observability(source: VideoSource) -> Observability:
    """MAP_OVERLAY / MAP_CHECK observability for a resolved acquisition source."""
    return MAP_OVERLAY_OBSERVABILITY[source]


def absence_is_reliable_negative(observability: Observability) -> bool:
    """Whether missing MAP_OVERLAY may be recorded as ``False``.

    ``observable`` → yes. ``unobservable`` / ``potentially_observable`` → no
    (detector may still attempt; absence is not a reliable negative).
    """
    return observability is Observability.OBSERVABLE
