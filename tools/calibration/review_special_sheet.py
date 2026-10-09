#!/usr/bin/env python3
"""Contact sheets and draft labels for Review event-lane special calibration.

Reads a ``review timeline-import`` / ``timeline-video-import`` output
directory. Discovery here is deliberately permissive: every representation
and the full benchmark scale grid are scored, and cursor-overlapping
candidates are kept and flagged so their effect can be measured. Production
uses only the frozen choices in ``configs/default.yaml``.

Writes into ``--out``:

* ``sheet_NN.png``: one tile per (row, x) cluster: lane context with the row
  and cursor band, the candidate crop, each representation's glyph crop, and
  the scores;
* ``clusters.json``: every observation with all diagnostics;
* ``draft_labels.yaml``: one entry per cluster with ``status: draft``. Copy it
  to ``labels.yaml``, mark each entry ``confirmed`` or ``rejected``, correct
  ``kind`` / ``special_id`` / ``slot`` / ``elapsed``, add missed events, and
  set the top-level ``review_status: reviewed``. Detector output is never
  ground truth; ``review_special_report.py`` refuses unreviewed labels.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import yaml

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.config.paths import resolve_config_path
from splatoon3_ai_coach.review.event_lane import calibrate_axis, select_static_samples
from splatoon3_ai_coach.review.lane_glyphs import (
    DEATH_TEMPLATE_ID,
    REPRESENTATIONS,
    UNKNOWN_ID,
    LaneGlyphCandidate,
    accept_lane_match,
    compute_row_responses,
    cursor_band,
    detect_lane_glyph_candidates,
    glyph_image,
    lane_origin,
    load_lane_templates,
    score_lane_glyph,
)
from splatoon3_ai_coach.review.models import ReviewTimelineDataset, ReviewTimelineSample

BENCHMARK_SCALES = (0.90, 0.95, 1.00, 1.05, 1.10)
DISCOVERY_FLOOR = 0.40
TILE_W, TILE_H = 560, 200
COLUMNS, ROWS_PER_PAGE = 3, 6


@dataclass
class Observation:
    """One permissive glyph observation with every diagnostic."""

    sample_id: str
    row_id: int
    slot_id: str
    left_x: float
    center_x: float
    right_x: float
    x_norm: float
    bbox: list[int]
    cursor_x: float | None
    cursor_overlap: bool
    scores: dict[str, dict[str, object]]
    label: str


@dataclass
class Cluster:
    """Observations at one row and x neighborhood across frames."""

    cluster_id: str
    observations: list[Observation] = field(default_factory=list)

    @property
    def clear(self) -> list[Observation]:
        """Observations not covered by the cursor band."""
        return [o for o in self.observations if not o.cursor_overlap]


def main() -> int:
    """Build sheets, cluster diagnostics and draft labels for one import."""
    args = _parse_args()
    config = load_config(resolve_config_path(args.config)).review
    dataset = ReviewTimelineDataset.model_validate_json(
        (args.import_dir / "review_timeline.json").read_text(encoding="utf-8")
    )
    samples = select_static_samples(dataset.samples, args.frames)
    images = {s.sample_id: cv2.imread(s.image_path) for s in samples}
    axis = diagnostic_axis(dataset, config)
    observations: list[Observation] = []
    for sample in samples:
        image = images[sample.sample_id]
        if image is not None:
            observations.extend(observe_permissive(image, sample, config))
    clusters = cluster(observations, config.special_deduplication_tolerance)
    args.out.mkdir(parents=True, exist_ok=True)
    payload = _clusters_payload(clusters, axis, dataset, samples)
    (args.out / "clusters.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    # Cursor-only clusters are the sweeping cursor line, not lane glyphs.
    paired = sorted(
        ((c, e) for c, e in zip(clusters, payload["clusters"], strict=True) if c.clear),
        key=lambda pair: _review_order(pair[1]),
    )
    clusters = [c for c, _ in paired]
    payload = {**payload, "clusters": [e for _, e in paired]}
    draft = draft_labels(payload, args.recording_id or dataset.source_recording_id)
    (args.out / "draft_labels.yaml").write_text(
        yaml.safe_dump(draft, sort_keys=False), encoding="utf-8"
    )
    pages = render_sheets(clusters, payload, images, samples, config, args.out)
    print(
        f"{len(observations)} observations, {len(clusters)} clusters, "
        f"{pages} sheet page(s) -> {args.out}"
    )
    return 0


def _review_order(entry: dict[str, object]) -> tuple[int, float]:
    """Specials, then deaths, then unknowns (best score first within each)."""
    labels = entry["labels_by_representation"].values()  # type: ignore[union-attr]
    accepted = [label for label in labels if label != UNKNOWN_ID]
    if any(label != DEATH_TEMPLATE_ID for label in accepted):
        group = 0
    elif accepted:
        group = 1
    else:
        group = 2
    best = max(
        float(o["scores"][rep]["score"])  # type: ignore[index]
        for o in entry["observations"]  # type: ignore[union-attr]
        for rep in REPRESENTATIONS
    )
    return group, -best


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("import_dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--frames", type=int, default=6)
    parser.add_argument("--recording-id")
    return parser.parse_args()


def diagnostic_axis(
    dataset: ReviewTimelineDataset, config: ReviewTimelineConfig
) -> dict[str, float] | None:
    """The production axis fit over every clocked sample of the import."""
    axis = calibrate_axis(dataset.samples, config)
    if axis is None:
        return None
    return {
        "slope": axis.slope,
        "intercept": axis.intercept,
        "point_count": axis.point_count,
        "rejected_point_count": axis.rejected_point_count,
        "max_absolute_error_seconds": axis.max_absolute_error_seconds,
    }


def observe_permissive(
    image: np.ndarray, sample: ReviewTimelineSample, config: ReviewTimelineConfig
) -> list[Observation]:
    """Discover without cursor invalidation; score under every representation."""
    discovery = config.model_copy(update={"special_discovery_floor": DISCOVERY_FLOOR})
    responses = {
        rep: compute_row_responses(
            image,
            discovery,
            load_lane_templates(
                config.special_template_dir, image.shape[0], rep, BENCHMARK_SCALES
            ),
            None,
            rep,
        )
        for rep in REPRESENTATIONS
    }
    primary = config.event_glyph_representation
    lane_x = lane_origin(image, config)[0]
    band = cursor_band(sample.cursor.x, lane_x, image.shape[1], config)
    out: list[Observation] = []
    for candidate in _union_candidates(
        image, sample.sample_id, discovery, responses, primary
    ):
        scores = {
            rep: _score_dict(candidate, rows[candidate.row_id], config)
            for rep, rows in responses.items()
        }
        out.append(_observation(candidate, sample, band, lane_x, scores, primary))
    return out


def _union_candidates(
    image: np.ndarray,
    sample_id: str,
    config: ReviewTimelineConfig,
    responses: dict[str, list],
    primary: str,
) -> list[LaneGlyphCandidate]:
    """Candidates found under any representation, primary first, one per x."""
    kept: list[LaneGlyphCandidate] = []
    for rep in (primary, *(r for r in REPRESENTATIONS if r != primary)):
        for candidate in detect_lane_glyph_candidates(
            image, sample_id, config, responses[rep]
        ):
            radius = 0.6 * candidate.bbox[2]
            if not any(
                k.row_id == candidate.row_id
                and abs(k.center_x - candidate.center_x) < radius
                for k in kept
            ):
                kept.append(candidate)
    return kept


def _score_dict(
    candidate: LaneGlyphCandidate, row: object, config: ReviewTimelineConfig
) -> dict[str, object]:
    score = score_lane_glyph(candidate, row)  # type: ignore[arg-type]
    return {
        "best": score.best_id,
        "score": round(score.score, 4),
        "runner_up": score.runner_up_id,
        "runner_up_score": round(score.runner_up_score, 4),
        "margin": round(score.margin, 4),
        "scale": None if score.scale is None else round(float(score.scale), 3),
        "accepted": accept_lane_match(score, config),
    }


def _observation(
    candidate: LaneGlyphCandidate,
    sample: ReviewTimelineSample,
    band: tuple[float, float] | None,
    lane_x: int,
    scores: dict[str, dict[str, object]],
    primary: str,
) -> Observation:
    overlap = False
    if band is not None:
        lo, hi = band[0] + lane_x, band[1] + lane_x
        overlap = candidate.right_x > lo and candidate.left_x <= hi
    return Observation(
        sample_id=sample.sample_id,
        row_id=candidate.row_id,
        slot_id=candidate.slot_id,
        left_x=candidate.left_x,
        center_x=candidate.center_x,
        right_x=candidate.right_x,
        x_norm=candidate.x_norm,
        bbox=list(candidate.bbox),
        cursor_x=sample.cursor.x,
        cursor_overlap=overlap,
        scores=scores,
        label=str(scores[primary]["accepted"]),
    )


def cluster(observations: list[Observation], tolerance: float) -> list[Cluster]:
    """Same row + same x neighborhood across frames (row geometry, not slot)."""
    out: list[Cluster] = []
    for obs in sorted(observations, key=lambda o: (o.row_id, o.x_norm)):
        last = out[-1] if out else None
        if (
            last is not None
            and last.observations[0].row_id == obs.row_id
            and abs(np.mean([o.x_norm for o in last.observations]) - obs.x_norm)
            <= tolerance
        ):
            last.observations.append(obs)
            continue
        out.append(Cluster(f"r{obs.row_id}-x{obs.x_norm:.4f}", [obs]))
    return out


def _clusters_payload(
    clusters: list[Cluster],
    axis: dict[str, float] | None,
    dataset: ReviewTimelineDataset,
    samples: list[ReviewTimelineSample],
) -> dict[str, object]:
    return {
        "axis": axis,
        "selected_sample_ids": [s.sample_id for s in samples],
        "clusters": [_cluster_payload(c, axis, dataset) for c in clusters],
    }


def _cluster_payload(
    item: Cluster, axis: dict[str, float] | None, dataset: ReviewTimelineDataset
) -> dict[str, object]:
    pool = item.clear or item.observations
    best = max(pool, key=lambda o: float(o.scores["whiteness"]["score"]))  # type: ignore[arg-type]
    width = best.center_x / best.x_norm if best.x_norm else 1920.0
    anchors = {"left": best.left_x, "center": best.center_x, "right": best.right_x}
    return {
        "cluster_id": item.cluster_id,
        "row_id": best.row_id,
        "slot": best.slot_id,
        "x_norm": round(best.x_norm, 5),
        "axis_elapsed": {
            name: (
                round(axis["slope"] * (x / width) + axis["intercept"], 2)
                if axis
                else None
            )
            for name, x in anchors.items()
        },
        "clock_at_cursor": {name: _clock_near(dataset, x) for name, x in anchors.items()},
        "clear_sample_ids": sorted({o.sample_id for o in item.clear}),
        "cursor_obscured_sample_ids": sorted(
            {o.sample_id for o in item.observations if o.cursor_overlap}
        ),
        "detector_label": best.label,
        "labels_by_representation": {
            rep: max(pool, key=lambda o, r=rep: float(o.scores[r]["score"])).scores[rep][
                "accepted"
            ]
            for rep in REPRESENTATIONS
        },
        "observations": [o.__dict__ for o in item.observations],
    }


def _clock_near(
    dataset: ReviewTimelineDataset, x: float, tolerance: float = 3.0
) -> int | None:
    """Review clock of the sample whose cursor sits nearest ``x`` (if close)."""
    near = [
        (abs(s.cursor.x - x), s.elapsed_seconds)
        for s in dataset.samples
        if s.cursor.x is not None
        and s.elapsed_seconds is not None
        and abs(s.cursor.x - x) <= tolerance
    ]
    return min(near)[1] if near else None


def draft_labels(
    payload: dict[str, object], recording_id: str | None
) -> dict[str, object]:
    """One draft entry per cluster; ``kind`` mirrors the detector, unconfirmed."""
    events = []
    for item in payload["clusters"]:  # type: ignore[union-attr]
        label = item["detector_label"]
        if label == UNKNOWN_ID:
            others = [
                v for v in item["labels_by_representation"].values() if v != UNKNOWN_ID
            ]
            label = others[0] if others else label
        kind = (
            "death"
            if label == DEATH_TEMPLATE_ID
            else ("unknown" if label == UNKNOWN_ID else "special")
        )
        elapsed = item["clock_at_cursor"]["center"]
        if elapsed is None:
            elapsed = item["axis_elapsed"]["center"]
        events.append(
            {
                "cluster_id": item["cluster_id"],
                "status": "draft",
                "kind": kind,
                "special_id": label if kind == "special" else None,
                "slot": item["slot"],
                "row_id": item["row_id"],
                "x_norm": item["x_norm"],
                "elapsed": elapsed,
                "detector_label": item["detector_label"],
                "labels_by_representation": item["labels_by_representation"],
            }
        )
    return {"recording_id": recording_id, "schema_version": "1", "events": events}


def render_sheets(
    clusters: list[Cluster],
    payload: dict[str, object],
    images: dict[str, np.ndarray],
    samples: list[ReviewTimelineSample],
    config: ReviewTimelineConfig,
    out: Path,
) -> int:
    """Write ``sheet_NN.png`` pages; returns the page count."""
    per_page = COLUMNS * ROWS_PER_PAGE
    entries = payload["clusters"]  # type: ignore[index]
    pages = 0
    for start in range(0, len(clusters), per_page):
        page = np.full((ROWS_PER_PAGE * TILE_H, COLUMNS * TILE_W, 3), 24, np.uint8)
        for offset, (item, entry) in enumerate(
            zip(
                clusters[start : start + per_page],
                entries[start : start + per_page],
                strict=True,
            )
        ):
            tile = _tile(item, entry, images, config)
            r, c = divmod(offset, COLUMNS)
            page[r * TILE_H : (r + 1) * TILE_H, c * TILE_W : (c + 1) * TILE_W] = tile
        pages += 1
        cv2.imwrite(str(out / f"sheet_{pages:02d}.png"), page)
    return pages


def _tile(
    item: Cluster,
    entry: dict[str, object],
    images: dict[str, np.ndarray],
    config: ReviewTimelineConfig,
) -> np.ndarray:
    tile = np.full((TILE_H, TILE_W, 3), 40, np.uint8)
    obs = (item.clear or item.observations)[0]
    image = images[obs.sample_id]
    context = _context(image, obs, config)
    tile[4 : 4 + context.shape[0], 4 : 4 + context.shape[1]] = context
    x = 8 + context.shape[1]
    crop = _crop(image, obs.bbox)
    for panel in [crop, *(_glyph_panel(crop, rep) for rep in REPRESENTATIONS)]:
        big = cv2.resize(panel, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
        h, w = big.shape[:2]
        if x + w < TILE_W and h < 80:
            tile[4 : 4 + h, x : x + w] = big
        x += w + 4
    _text(tile, _lines(entry, obs), 8 + context.shape[1], 92)
    return tile


def _context(
    image: np.ndarray, obs: Observation, config: ReviewTimelineConfig
) -> np.ndarray:
    x1, y1, x2, y2 = lane_origin(image, config)
    left = int(max(x1, obs.center_x - 70))
    right = int(min(x2, obs.center_x + 70))
    view = image[y1:y2, left:right].copy()
    center_y = int(config.event_lane_row_centers[obs.row_id] * (y2 - y1))
    cv2.line(view, (0, center_y), (view.shape[1] - 1, center_y), (0, 255, 0), 1)
    if obs.cursor_x is not None:
        half = config.cursor_exclusion_half_width * image.shape[1]
        lo, hi = int(obs.cursor_x - half - left), int(obs.cursor_x + half - left)
        if hi >= 0 and lo < view.shape[1]:
            cv2.rectangle(view, (lo, 0), (hi, view.shape[0] - 1), (0, 0, 255), 1)
    return cv2.resize(view, (140, 192), interpolation=cv2.INTER_AREA)


def _crop(image: np.ndarray, bbox: list[int]) -> np.ndarray:
    x, y, w, h = bbox
    return image[max(0, y - 2) : y + h + 2, max(0, x - 2) : x + w + 2].copy()


def _glyph_panel(crop: np.ndarray, representation: str) -> np.ndarray:
    glyph = (np.clip(glyph_image(crop, representation), 0, 1) * 255).astype(np.uint8)
    return cv2.cvtColor(glyph, cv2.COLOR_GRAY2BGR)


def _lines(entry: dict[str, object], obs: Observation) -> list[str]:
    white = obs.scores["whiteness"]
    gray = obs.scores["grayscale"]
    hsv = obs.scores["hsv_value"]
    return [
        f"{entry['cluster_id']} {entry['slot']} "
        + "/".join(str(v) for v in entry["labels_by_representation"].values()),
        f"white {white['best']} {white['score']:.2f} ru {white['runner_up']} "
        f"m={white['margin']:.2f} s={white['scale']}",
        f"gray {gray['best']} {gray['score']:.2f} m={gray['margin']:.2f} | "
        f"hsv {hsv['best']} {hsv['score']:.2f} m={hsv['margin']:.2f}",
        f"x={obs.center_x:.0f} clock@cursor {entry['clock_at_cursor']} ",
        f"axis {entry['axis_elapsed']}",
        f"clear={len(entry['clear_sample_ids'])} "
        f"obscured={len(entry['cursor_obscured_sample_ids'])} "
        f"cursor_overlap={obs.cursor_overlap}",
    ]


def _text(tile: np.ndarray, lines: list[str], x: int, y: int) -> None:
    for index, line in enumerate(lines):
        cv2.putText(
            tile,
            line,
            (x, y + index * 17),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.36,
            (235, 235, 235),
            1,
            cv2.LINE_AA,
        )


if __name__ == "__main__":
    raise SystemExit(main())
