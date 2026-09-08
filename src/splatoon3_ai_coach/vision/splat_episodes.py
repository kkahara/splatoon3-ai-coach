"""Splat episode tracker: identity and dedup, not player lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field

from splatoon3_ai_coach.config.models import EventFusionConfig
from splatoon3_ai_coach.vision.models import SplatBannerInstance
from splatoon3_ai_coach.vision.splat import fingerprint_distance


@dataclass
class _OpenEpisode:
    """One in-flight splat notification."""

    fingerprint: str
    slot_y: float
    absent: int = 0


@dataclass
class SplatEpisodeStep:
    """Newly opened banner instances for this frame (emit one SPLAT each)."""

    opened: list[SplatBannerInstance] = field(default_factory=list)


class SplatEpisodeFuser:
    """Match this-frame banner instances to open episodes.

    Matching order:

    1. Near fingerprint + nearby stack slot (stable continuation).
    2. Near fingerprint after a vertical stack shift.
    3. Nearby occupied slot alone when the average-hash fingerprint drifts
       (ink, map overlay, animation) while the same banner row stays visible.

    A new GameEvent opens only for unmatched incoming banners. Episodes close
    after ``splat_absent_min_observations`` frames without a match — not via a
    time debounce.
    """

    def __init__(self, config: EventFusionConfig) -> None:
        self.config = config
        self._open: list[_OpenEpisode] = []

    def step(self, instances: list[SplatBannerInstance]) -> SplatEpisodeStep:
        """Assign incoming banners; return instances that start a new episode."""
        incoming = list(instances)
        assigned_open: set[int] = set()
        assigned_in: set[int] = set()
        self._match_nearby_slot(incoming, assigned_open, assigned_in)
        self._match_fingerprint_only(incoming, assigned_open, assigned_in)
        self._match_occupied_slot(incoming, assigned_open, assigned_in)
        opened = self._open_unmatched(incoming, assigned_in)
        self._age_unmatched(assigned_open)
        return SplatEpisodeStep(opened=opened)

    def _match_nearby_slot(
        self,
        incoming: list[SplatBannerInstance],
        assigned_open: set[int],
        assigned_in: set[int],
    ) -> None:
        """Same / near fingerprint at a nearby stack slot."""
        nearby = self.config.splat_slot_nearby
        for i_idx, inst in enumerate(incoming):
            best = self._best_open(
                inst,
                assigned_open,
                require_nearby_slot=True,
                nearby=nearby,
            )
            if best is None:
                continue
            self._claim(best, inst, assigned_open, assigned_in, i_idx)

    def _match_fingerprint_only(
        self,
        incoming: list[SplatBannerInstance],
        assigned_open: set[int],
        assigned_in: set[int],
    ) -> None:
        """Same / near fingerprint after a vertical stack shift."""
        for i_idx, inst in enumerate(incoming):
            if i_idx in assigned_in:
                continue
            best = self._best_open(inst, assigned_open, require_nearby_slot=False)
            if best is None:
                continue
            self._claim(best, inst, assigned_open, assigned_in, i_idx)

    def _match_occupied_slot(
        self,
        incoming: list[SplatBannerInstance],
        assigned_open: set[int],
        assigned_in: set[int],
    ) -> None:
        """Continue an open episode when the same stack row is still occupied.

        Banner aHash fingerprints often jump > ``splat_fingerprint_max_distance``
        across consecutive observations of one persistent UI row. Slot occupancy
        is the identity evidence for that continuation.
        """
        nearby = self.config.splat_slot_nearby
        for i_idx, inst in enumerate(incoming):
            if i_idx in assigned_in:
                continue
            best_idx: int | None = None
            best_delta: float | None = None
            for o_idx, episode in enumerate(self._open):
                if o_idx in assigned_open:
                    continue
                slot_delta = abs(inst.slot_y - episode.slot_y)
                if slot_delta > nearby:
                    continue
                if best_delta is None or slot_delta < best_delta:
                    best_delta = slot_delta
                    best_idx = o_idx
            if best_idx is None:
                continue
            self._claim(best_idx, inst, assigned_open, assigned_in, i_idx)

    def _best_open(
        self,
        inst: SplatBannerInstance,
        assigned_open: set[int],
        *,
        require_nearby_slot: bool,
        nearby: float = 0.0,
    ) -> int | None:
        """Closest unmatched open episode within fingerprint tolerance."""
        max_dist = self.config.splat_fingerprint_max_distance
        best_idx: int | None = None
        best_key: tuple[int, float] | None = None
        for o_idx, episode in enumerate(self._open):
            if o_idx in assigned_open:
                continue
            dist = fingerprint_distance(inst.fingerprint, episode.fingerprint)
            if dist > max_dist:
                continue
            slot_delta = abs(inst.slot_y - episode.slot_y)
            if require_nearby_slot and slot_delta > nearby:
                continue
            key = (dist, slot_delta)
            if best_key is None or key < best_key:
                best_key = key
                best_idx = o_idx
        return best_idx

    def _claim(
        self,
        open_idx: int,
        inst: SplatBannerInstance,
        assigned_open: set[int],
        assigned_in: set[int],
        incoming_idx: int,
    ) -> None:
        """Bind an incoming banner to an open episode and refresh its slot."""
        episode = self._open[open_idx]
        episode.fingerprint = inst.fingerprint
        episode.slot_y = inst.slot_y
        episode.absent = 0
        assigned_open.add(open_idx)
        assigned_in.add(incoming_idx)

    def _open_unmatched(
        self,
        incoming: list[SplatBannerInstance],
        assigned_in: set[int],
    ) -> list[SplatBannerInstance]:
        """Start a new episode for each unmatched incoming banner."""
        opened: list[SplatBannerInstance] = []
        for i_idx, inst in enumerate(incoming):
            if i_idx in assigned_in:
                continue
            self._open.append(
                _OpenEpisode(fingerprint=inst.fingerprint, slot_y=inst.slot_y)
            )
            opened.append(inst)
        return opened

    def _age_unmatched(self, assigned_open: set[int]) -> None:
        """Close episodes that stayed unseen for the configured streak."""
        min_absent = self.config.splat_absent_min_observations
        kept: list[_OpenEpisode] = []
        for o_idx, episode in enumerate(self._open):
            if o_idx in assigned_open:
                kept.append(episode)
                continue
            episode.absent += 1
            if episode.absent < min_absent:
                kept.append(episode)
        self._open = kept
