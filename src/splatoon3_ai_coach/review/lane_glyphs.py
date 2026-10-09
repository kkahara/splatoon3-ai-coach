"""Event-lane glyph discovery, scoring and acceptance on known icon rows.

Three stages stay separate:

* ``detect_lane_glyph_candidates`` finds glyph positions on one row band
  without assigning an identity;
* ``score_lane_glyph`` ranks every template at that position;
* ``accept_lane_match`` applies the special or death acceptance rule.

The cursor exclusion only invalidates response-map windows. The image, the
glyph representation and the templates are never modified.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import ReviewTimelineConfig

TEMPLATE_REFERENCE_HEIGHT = 1080
DEATH_TEMPLATE_ID = "death"
UNKNOWN_ID = "unknown"
REPRESENTATIONS = ("whiteness", "grayscale", "hsv_value")

SPECIAL_TEMPLATE_FILES = {
    "trizooka": "trizooka.png",
    "big_bubbler": "big_bubbler.png",
    "zipcaster": "zipcaster.png",
    "wave_breaker": "wave_breaker.png",
    "ink_vac": "ink_vac.png",
    "killer_wail_5.1": "killer_wail_5.1.png",
    "crab_tank": "crab_tank.png",
    "reefslider": "reefslider.png",
    "triple_inkstrike": "triple_inkstrike.png",
    "tacticooler": "tacticooler.png",
    "kraken_royale": "kraken_royale.png",
    "super_chump": "super_chump.png",
    "triple_splashdown": "triple_splashdown.png",
    "splattercolor_screen": "splattercolor_screen.png",
    "ink_storm": "ink_storm.png",
    "booyah_bomb": "booyah_bomb.png",
    "inkjet": "inkjet.png",
    "ultra_stamp": "ultra_stamp.png",
    "tenta_missiles": "tenta_missiles.png",
    DEATH_TEMPLATE_ID: "death.png",
}

ROW_SLOTS = (
    "top_1",
    "top_2",
    "top_3",
    "top_4",
    "bottom_4",
    "bottom_3",
    "bottom_2",
    "bottom_1",
)


@dataclass(frozen=True)
class LaneTemplate:
    """One template glyph at one scale."""

    template_id: str
    scale: float
    glyph: np.ndarray

    @property
    def width(self) -> int:
        """Template width in pixels."""
        return int(self.glyph.shape[1])

    @property
    def height(self) -> int:
        """Template height in pixels."""
        return int(self.glyph.shape[0])


@dataclass
class TemplateTrack:
    """Best response of one template at each lane-relative center x."""

    score: np.ndarray
    left: np.ndarray
    top: np.ndarray
    width: np.ndarray
    height: np.ndarray
    scale: np.ndarray


@dataclass
class RowResponse:
    """Per-template responses for one icon row of one frame."""

    row_id: int
    lane_x: int
    lane_y: int
    lane_width: int
    frame_width: int
    tracks: dict[str, TemplateTrack] = field(default_factory=dict)
    cursor_band: tuple[float, float] | None = None


@dataclass(frozen=True)
class LaneGlyphCandidate:
    """A glyph position on a known row. Carries no identity."""

    sample_id: str
    row_id: int
    slot_id: str
    team_row: str
    bbox: tuple[int, int, int, int]
    left_x: float
    center_x: float
    right_x: float
    frame_width: int
    lane_center: int
    discovery_score: float
    crop: np.ndarray

    @property
    def x_norm(self) -> float:
        """Center x normalized to the full frame width."""
        return self.center_x / self.frame_width


@dataclass(frozen=True)
class LaneGlyphScore:
    """Template ranking at one candidate position."""

    best_id: str | None
    score: float
    runner_up_id: str | None
    runner_up_score: float
    scale: float | None

    @property
    def margin(self) -> float:
        """Best minus runner-up score, never negative."""
        return max(0.0, self.score - self.runner_up_score)


def glyph_image(image: np.ndarray, representation: str = "whiteness") -> np.ndarray:
    """Tint-independent float32 glyph image in [0, 1]."""
    if representation == "grayscale":
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return gray.astype(np.float32) / 255.0
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32) / 255.0
    saturation, value = hsv[..., 1], hsv[..., 2]
    if representation == "whiteness":
        return value * (1.0 - saturation)
    if representation == "hsv_value":
        return 0.5 * (value + (1.0 - saturation))
    raise ValueError(f"Unknown glyph representation {representation!r}")


def load_lane_templates(
    directory: Path | None,
    frame_height: int,
    representation: str,
    scales: tuple[float, ...],
) -> tuple[LaneTemplate, ...]:
    """Templates resized from their 1080p capture size to this frame."""
    if directory is None:
        return ()
    return _load_cached(str(directory), frame_height, representation, tuple(scales))


@lru_cache(maxsize=16)
def _load_cached(
    directory: str,
    frame_height: int,
    representation: str,
    scales: tuple[float, ...],
) -> tuple[LaneTemplate, ...]:
    out: list[LaneTemplate] = []
    ratio = frame_height / TEMPLATE_REFERENCE_HEIGHT
    for template_id, filename in SPECIAL_TEMPLATE_FILES.items():
        path = Path(directory) / filename
        image = cv2.imread(str(path)) if path.exists() else None
        if image is None:
            continue
        glyph = glyph_image(image, representation)
        for scale in scales:
            factor = ratio * scale
            if abs(factor - 1.0) > 1e-6:
                interpolation = cv2.INTER_AREA if factor < 1 else cv2.INTER_LINEAR
                glyph_scaled = cv2.resize(
                    glyph, None, fx=factor, fy=factor, interpolation=interpolation
                )
            else:
                glyph_scaled = glyph
            out.append(LaneTemplate(template_id, scale, glyph_scaled))
    return tuple(out)


def lane_origin(
    image: np.ndarray, config: ReviewTimelineConfig
) -> tuple[int, int, int, int]:
    """Event-lane ROI as full-frame pixel ``(x1, y1, x2, y2)``."""
    height, width = image.shape[:2]
    x1, y1, x2, y2 = config.event_lane_roi
    return int(x1 * width), int(y1 * height), int(x2 * width), int(y2 * height)


def cursor_band(
    cursor_x: float | None, lane_x: int, frame_width: int, config: ReviewTimelineConfig
) -> tuple[float, float] | None:
    """Cursor exclusion band in lane-relative x, or ``None`` without a cursor."""
    if cursor_x is None:
        return None
    half = config.cursor_exclusion_half_width * frame_width
    center = float(cursor_x) - lane_x
    return center - half, center + half


def compute_row_responses(
    image: np.ndarray,
    config: ReviewTimelineConfig,
    templates: tuple[LaneTemplate, ...],
    cursor_x: float | None = None,
    representation: str | None = None,
) -> list[RowResponse]:
    """Match every template along each row band; invalidate cursor windows."""
    x1, y1, x2, y2 = lane_origin(image, config)
    lane = image[y1:y2, x1:x2]
    glyph = glyph_image(lane, representation or config.event_glyph_representation)
    band = cursor_band(cursor_x, x1, image.shape[1], config)
    rows: list[RowResponse] = []
    for row_id, center in enumerate(config.event_lane_row_centers):
        row = RowResponse(row_id, x1, y1, lane.shape[1], image.shape[1], cursor_band=band)
        for template in templates:
            _match_row(glyph, row, float(center), template, config)
        rows.append(row)
    return rows


def _match_row(
    glyph: np.ndarray,
    row: RowResponse,
    center_norm: float,
    template: LaneTemplate,
    config: ReviewTimelineConfig,
) -> None:
    lane_h, lane_w = glyph.shape[:2]
    center = center_norm * lane_h
    slack = config.event_lane_row_half_height * lane_h
    top = max(0, int(np.floor(center - slack - template.height / 2)))
    bottom = min(lane_h, int(np.ceil(center + slack + template.height / 2)))
    if bottom - top < template.height or lane_w < template.width:
        return
    band = glyph[top:bottom]
    response = cv2.matchTemplate(band, template.glyph, cv2.TM_CCOEFF_NORMED)
    response = np.clip(np.nan_to_num(response, nan=0.0, posinf=0.0, neginf=0.0), -1, 1)
    column = response.max(axis=0)
    best_y = response.argmax(axis=0)
    lefts = np.arange(column.shape[0])
    if row.cursor_band is not None:
        lo, hi = row.cursor_band
        covered = (lefts + template.width > lo) & (lefts <= hi)
        column = np.where(covered, -1.0, column)
    _merge_track(row, template, column, best_y + top, lefts)


def _merge_track(
    row: RowResponse,
    template: LaneTemplate,
    column: np.ndarray,
    tops: np.ndarray,
    lefts: np.ndarray,
) -> None:
    track = row.tracks.get(template.template_id)
    if track is None:
        size = row.lane_width
        track = TemplateTrack(
            score=np.full(size, -1.0, dtype=np.float32),
            left=np.zeros(size, dtype=np.int32),
            top=np.zeros(size, dtype=np.int32),
            width=np.zeros(size, dtype=np.int32),
            height=np.zeros(size, dtype=np.int32),
            scale=np.zeros(size, dtype=np.float32),
        )
        row.tracks[template.template_id] = track
    centers = lefts + template.width // 2
    valid = centers < row.lane_width
    centers, column, tops, lefts = (
        centers[valid],
        column[valid],
        tops[valid],
        lefts[valid],
    )
    better = column > track.score[centers]
    index = centers[better]
    track.score[index] = column[better]
    track.left[index] = lefts[better]
    track.top[index] = tops[better]
    track.width[index] = template.width
    track.height[index] = template.height
    track.scale[index] = template.scale


def detect_lane_glyph_candidates(
    image: np.ndarray,
    sample_id: str,
    config: ReviewTimelineConfig,
    responses: list[RowResponse],
) -> list[LaneGlyphCandidate]:
    """Glyph positions above the discovery floor, one per x neighborhood."""
    x1, y1, x2, y2 = lane_origin(image, config)
    lane = image[y1:y2, x1:x2]
    gray = glyph_image(lane, "grayscale")
    out: list[LaneGlyphCandidate] = []
    for row in responses:
        if not row.tracks:
            continue
        tracks = list(row.tracks.values())
        stacked = np.stack([track.score for track in tracks])
        combined = stacked.max(axis=0)
        owner = stacked.argmax(axis=0)
        widths = [int(t.width[t.width > 0].min()) for t in tracks if (t.width > 0).any()]
        radius = max(3, int(0.6 * min(widths))) if widths else 3
        for center in _peaks(combined, config.special_discovery_floor, radius):
            track = tracks[int(owner[center])]
            if _window_std(gray, track, center) < config.event_glyph_min_std:
                continue
            out.append(
                _candidate(sample_id, row, lane, track, center, float(combined[center]))
            )
    keep = None
    if config.event_echo_keep_accepted_deaths:

        def keep(candidate: LaneGlyphCandidate) -> bool:
            score = score_lane_glyph(candidate, responses[candidate.row_id])
            return accept_lane_match(score, config) == DEATH_TEMPLATE_ID

    return _suppress_neighbor_row_echoes(out, keep=keep)


def _suppress_neighbor_row_echoes(
    candidates: list[LaneGlyphCandidate],
    min_x_overlap: float = 0.2,
    min_score_gap: float = 0.15,
    keep: Callable[[LaneGlyphCandidate], bool] | None = None,
) -> list[LaneGlyphCandidate]:
    """Drop a much weaker window over the same glyph on this or the next row.

    Icons are taller than the row pitch and wider than the NMS radius, so one
    glyph also yields partial windows beside it and on the neighboring row.
    Those echoes score far below the glyph itself; two real icons both score
    high, so the score gap keeps genuine same-x events on adjacent rows.
    Candidates for which ``keep`` returns true are never treated as echoes.
    """
    kept: list[LaneGlyphCandidate] = []
    for candidate in candidates:
        if keep is not None and keep(candidate):
            kept.append(candidate)
            continue
        echo = any(
            abs(other.row_id - candidate.row_id) <= 1
            and other is not candidate
            and other.discovery_score - candidate.discovery_score >= min_score_gap
            and _overlap(candidate.bbox, other.bbox, axis=0) > min_x_overlap
            and _overlap(candidate.bbox, other.bbox, axis=1) > 0
            for other in candidates
        )
        if not echo:
            kept.append(candidate)
    return kept


def _overlap(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int], axis: int
) -> float:
    """Overlap along x (0) or y (1) as a fraction of the shorter extent."""
    start_a, size_a = a[axis], a[axis + 2]
    start_b, size_b = b[axis], b[axis + 2]
    shared = min(start_a + size_a, start_b + size_b) - max(start_a, start_b)
    return max(0.0, shared) / max(1, min(size_a, size_b))


def _window_std(gray: np.ndarray, track: TemplateTrack, center: int) -> float:
    left, top = int(track.left[center]), int(track.top[center])
    width, height = int(track.width[center]), int(track.height[center])
    window = gray[top : top + height, left : left + width]
    return float(window.std()) if window.size else 0.0


def _peaks(values: np.ndarray, floor: float, radius: int) -> list[int]:
    order = [int(i) for i in np.argsort(-values) if values[i] >= floor]
    kept: list[int] = []
    for index in order:
        if all(abs(index - other) >= radius for other in kept):
            kept.append(index)
    return sorted(kept)


def _candidate(
    sample_id: str,
    row: RowResponse,
    lane: np.ndarray,
    track: TemplateTrack,
    center: int,
    score: float,
) -> LaneGlyphCandidate:
    left, top = int(track.left[center]), int(track.top[center])
    width, height = int(track.width[center]), int(track.height[center])
    pad = 2
    crop = lane[
        max(0, top - pad) : min(lane.shape[0], top + height + pad),
        max(0, left - pad) : min(lane.shape[1], left + width + pad),
    ].copy()
    slot = ROW_SLOTS[row.row_id]
    return LaneGlyphCandidate(
        sample_id=sample_id,
        row_id=row.row_id,
        slot_id=slot,
        team_row=slot.split("_", maxsplit=1)[0],
        bbox=(row.lane_x + left, row.lane_y + top, width, height),
        left_x=float(row.lane_x + left),
        center_x=float(row.lane_x + left + width / 2),
        right_x=float(row.lane_x + left + width),
        frame_width=row.frame_width,
        lane_center=center,
        discovery_score=max(0.0, score),
        crop=crop,
    )


def score_lane_glyph(
    candidate: LaneGlyphCandidate, row: RowResponse, window: int = 3
) -> LaneGlyphScore:
    """Best and runner-up template within ``window`` px of the candidate."""
    lo = max(0, candidate.lane_center - window)
    hi = candidate.lane_center + window + 1
    ranked: list[tuple[float, str, float]] = []
    for template_id, track in row.tracks.items():
        segment = track.score[lo:hi]
        if segment.size == 0:
            continue
        offset = int(segment.argmax())
        ranked.append(
            (
                max(0.0, float(segment[offset])),
                template_id,
                float(track.scale[lo + offset]),
            )
        )
    ranked.sort(reverse=True)
    if not ranked:
        return LaneGlyphScore(None, 0.0, None, 0.0, None)
    best = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else (0.0, None, 0.0)
    return LaneGlyphScore(best[1], best[0], runner[1], runner[0], best[2])


def accept_lane_match(score: LaneGlyphScore, config: ReviewTimelineConfig) -> str:
    """Accepted template ID, or ``unknown``. Death and specials use own rules."""
    if score.best_id is None:
        return UNKNOWN_ID
    if score.best_id == DEATH_TEMPLATE_ID:
        floor, margin = config.death_match_floor, config.death_match_margin
    else:
        floor, margin = config.special_match_floor, config.special_match_margin
    if score.score >= floor and score.margin >= margin:
        return score.best_id
    return UNKNOWN_ID
