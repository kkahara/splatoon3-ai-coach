"""Frame population, intervals and deterministic frame selection (blind).

Reads only the vision-manifest fields named by Amendment 3: raw
``map_overlay`` / ``player_count`` readings, fused ``match_phase`` /
``player_lifecycle``, the ``MATCH_END`` event time, frame identifiers and
``video_identity``. Everything else in the manifest is discarded.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from map_ink_reference.constants import CADENCE_STEP, STAGE_FRAME_COUNT
from map_ink_reference.errors import IsolationViolation, MalformedInput

# Keys that must never appear in a vision manifest handed to blind stages.
_FORBIDDEN_MANIFEST_KEYS = frozenset(
    {"team_color_calibration", "map_observations", "observations"}
)


@dataclass(frozen=True)
class CadenceFrame:
    """Reduced per-cadence-frame metadata used for selection."""

    frame_id: str
    timestamp: float
    source_pts: int | None
    tb_num: int | None
    tb_den: int | None
    source_frame_index: int | None
    map_present: bool | None
    roster_visible: bool | None
    match_phase: str | None
    player_lifecycle: str | None

    @property
    def exact_time(self) -> Fraction:
        """Exact presentation time from ``source_pts`` and the time base."""
        if self.source_pts is None or not self.tb_num or not self.tb_den:
            raise MalformedInput(f"frame {self.frame_id} lacks source_pts/time base")
        return Fraction(self.source_pts * self.tb_num, self.tb_den)


@dataclass(frozen=True)
class ManifestView:
    """The only manifest content blind stages may use."""

    video_identity: str
    width: int
    height: int
    frames: tuple[CadenceFrame, ...]
    match_end_time: float | None


@dataclass(frozen=True)
class Interval:
    """Maximal run of eligible frames exactly one cadence step apart."""

    frames: tuple[CadenceFrame, ...]

    @property
    def start(self) -> Fraction:
        """Exact start time."""
        return self.frames[0].exact_time

    @property
    def is_long(self) -> bool:
        """Two or more frames."""
        return len(self.frames) >= 2

    @property
    def middle_frame(self) -> CadenceFrame:
        """Frame at index ``floor((len - 1) / 2)``."""
        return self.frames[(len(self.frames) - 1) // 2]


def _single_reading(detections: Sequence[Any], name: str, frame_id: str) -> dict | None:
    found = [d for d in detections if d.get("detector_name") == name]
    if len(found) > 1:
        raise MalformedInput(f"frame {frame_id}: multiple {name} detections")
    if not found:
        return None
    reading = found[0].get("reading")
    if not isinstance(reading, dict):
        raise MalformedInput(f"frame {frame_id}: {name} reading is not an object")
    return reading


def _optional_bool(reading: dict | None, key: str) -> bool | None:
    if reading is None or key not in reading:
        return None
    value = reading[key]
    if not isinstance(value, bool):
        raise MalformedInput(f"{key} must be boolean, got {value!r}")
    return value


def _snapshot_index(snapshots: Sequence[Any]) -> dict[float, Mapping[str, Any]]:
    index: dict[float, Mapping[str, Any]] = {}
    for snap in snapshots:
        if not isinstance(snap, dict) or "timestamp" not in snap:
            raise MalformedInput("state snapshot without timestamp")
        key = float(snap["timestamp"])
        if key in index:
            raise MalformedInput(f"duplicate state snapshot at {key}")
        index[key] = snap
    return index


def _match_end_time(events: Sequence[Any]) -> float | None:
    ends = [
        e for e in events if isinstance(e, dict) and e.get("event_type") == "MATCH_END"
    ]
    if len(ends) > 1:
        raise MalformedInput("more than one MATCH_END event")
    return None if not ends else float(ends[0]["start_time"])


def _reduce_frame(raw: Mapping[str, Any], snaps: Mapping[float, Mapping]) -> CadenceFrame:
    frame_id = str(raw["frame_id"])
    detections = raw.get("detections") or []
    overlay = _single_reading(detections, "map_overlay", frame_id)
    roster = _single_reading(detections, "player_count", frame_id)
    timestamp = float(raw["timestamp"])
    snap = snaps.get(timestamp)
    map_present = _optional_bool(overlay, "present")
    if map_present and snap is None:
        raise MalformedInput(f"map-present frame {frame_id} has no fused snapshot")
    return CadenceFrame(
        frame_id=frame_id,
        timestamp=timestamp,
        source_pts=raw.get("source_pts"),
        tb_num=raw.get("source_time_base_num"),
        tb_den=raw.get("source_time_base_den"),
        source_frame_index=raw.get("source_frame_index"),
        map_present=map_present,
        roster_visible=_optional_bool(roster, "roster_visible"),
        match_phase=None if snap is None else snap.get("match_phase"),
        player_lifecycle=None if snap is None else snap.get("player_lifecycle"),
    )


def reduce_manifest(manifest: Mapping[str, Any]) -> ManifestView:
    """Reduce a vision manifest to the fields Amendment 3 allows (fail closed)."""
    if not isinstance(manifest, Mapping):
        raise MalformedInput("vision manifest must be an object")
    leaked = _FORBIDDEN_MANIFEST_KEYS.intersection(manifest)
    if leaked:
        raise IsolationViolation(
            f"vision manifest carries forbidden keys {sorted(leaked)}"
        )
    for key in ("video_identity", "frame_results", "state_snapshots", "video"):
        if key not in manifest:
            raise MalformedInput(f"vision manifest missing {key!r}")
    video = manifest["video"]
    snaps = _snapshot_index(manifest["state_snapshots"])
    frames = [_reduce_frame(raw, snaps) for raw in manifest["frame_results"]]
    times = [frame.timestamp for frame in frames]
    if times != sorted(times) or len(set(times)) != len(times):
        raise MalformedInput("frame timestamps must be strictly increasing")
    return ManifestView(
        video_identity=str(manifest["video_identity"]),
        width=int(video["analysis_width"]),
        height=int(video["analysis_height"]),
        frames=tuple(frames),
        match_end_time=_match_end_time(manifest.get("game_events") or []),
    )


def is_eligible(frame: CadenceFrame, match_end_time: float | None) -> bool:
    """Amendment 3 eligibility (all four conditions)."""
    if frame.map_present is not True or frame.roster_visible is not False:
        return False
    if frame.match_phase != "in_match":
        return False
    return match_end_time is None or frame.timestamp < match_end_time


def eligible_intervals(view: ManifestView) -> list[Interval]:
    """Group eligible frames into intervals (exactly one cadence step apart)."""
    eligible = [f for f in view.frames if is_eligible(f, view.match_end_time)]
    runs: list[list[CadenceFrame]] = []
    for frame in eligible:
        if runs and frame.exact_time - runs[-1][-1].exact_time == CADENCE_STEP:
            runs[-1].append(frame)
        else:
            runs.append([frame])
    return [Interval(tuple(run)) for run in runs]


def order_intervals(
    intervals: Sequence[Interval],
) -> tuple[list[Interval], list[Interval]]:
    """Split into (long, single) lists, each sorted by start time."""
    long = sorted((i for i in intervals if i.is_long), key=lambda i: i.start)
    single = sorted((i for i in intervals if not i.is_long), key=lambda i: i.start)
    return long, single


def even_rank_indices(count: int, take: int) -> list[int]:
    """Indices ``floor((j + 0.5) * count / take)`` for ``j = 0 .. take-1``."""
    if take <= 0:
        return []
    if take > count:
        raise MalformedInput(f"cannot take {take} of {count} intervals")
    return [((2 * j + 1) * count) // (2 * take) for j in range(take)]


def select_intervals(
    long: Sequence[Interval], single: Sequence[Interval], k: int
) -> list[Interval]:
    """Two-list even-rank selection: long intervals first, then single-frame."""
    k_long = min(k, len(long))
    chosen = [long[i] for i in even_rank_indices(len(long), k_long)]
    k_single = k - k_long
    if k_single > len(single):
        raise MalformedInput(
            f"need {k_single} single-frame intervals, have {len(single)}"
        )
    chosen += [single[i] for i in even_rank_indices(len(single), k_single)]
    return chosen


def select_stage1(intervals: Sequence[Interval]) -> list[Interval]:
    """Stage 1: K = 8 intervals."""
    long, single = order_intervals(intervals)
    return select_intervals(long, single, STAGE_FRAME_COUNT)


def select_stage2(
    intervals: Sequence[Interval], stage1: Sequence[Interval]
) -> list[Interval]:
    """Stage 2: remaining intervals, K = min(8, remaining), same procedure."""
    taken = {interval.frames[0].frame_id for interval in stage1}
    remaining = [i for i in intervals if i.frames[0].frame_id not in taken]
    long, single = order_intervals(remaining)
    return select_intervals(long, single, min(STAGE_FRAME_COUNT, len(remaining)))


def cue_frame(view: ManifestView, interval: Interval) -> CadenceFrame | None:
    """First later cadence frame after the interval whose lifecycle is alive."""
    end = interval.frames[-1].timestamp
    for frame in view.frames:
        if frame.timestamp > end and frame.player_lifecycle == "alive":
            return frame
    return None
