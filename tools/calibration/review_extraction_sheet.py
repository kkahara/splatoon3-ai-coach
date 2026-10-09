#!/usr/bin/env python3
"""Detector-independent event-lane label sheets for Review imports.

Selects lane windows (all eight rows, a fixed x span) from roster-lamp
transitions in each ``review_timeline.json`` and renders them with no detector
output drawn. Categories:

* ``isolated``: a lamp alive->dead transition with no other transition within
  ``--isolation-seconds`` (one top-row and one bottom-row per recording);
* ``unmarked_top`` / ``unmarked_bottom``: a transition with no same-slot death
  marker within ``--marker-seconds`` (detector output is used *only* to pick
  these windows; it is never drawn or drafted);
* ``named``: windows given on the command line (``rid:clock_lo:clock_hi:name``).

Writes ``window_NN.png`` (lane crop with row guides, a pixel/clock ruler and
roster crops at each lamp transition for validation) and ``labels_template.yaml``
(``review_status: draft``, empty ``glyphs`` lists). Lamps select windows and
validate labels; they never decide which glyph belongs to which player.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.review.event_lane import select_static_samples
from splatoon3_ai_coach.review.lane_glyphs import ROW_SLOTS, lane_origin
from splatoon3_ai_coach.review.models import ReviewTimelineDataset, ReviewTimelineSample

WINDOW_PX = 64
# Sweeps step 5-14 px between distinct cursor positions.
MAX_FRAME_GAP_PX = 20
LANE_SCALE = 5
ROSTER_SCALE = 1.4
STATUSES = ("confirmed_death", "ambiguous", "not_death_glyph")


@dataclass
class Transition:
    """One alive->dead lamp change of a slot (frame level, cursor x)."""

    slot: str
    last_alive_x: float
    first_dead_x: float
    alive_sample: ReviewTimelineSample
    dead_sample: ReviewTimelineSample

    @property
    def x(self) -> float:
        """Midpoint of the change in screen pixels."""
        return (self.last_alive_x + self.first_dead_x) / 2


@dataclass
class Window:
    """One lane span to label exhaustively."""

    recording: str
    category: str
    x_lo: int
    x_hi: int
    reason: str
    transitions: list[Transition] = field(default_factory=list)


def main() -> int:
    """Select windows, render sheets and write the label template."""
    args = _parse_args()
    config = load_config(default_config_path()).review
    args.out.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for import_dir in args.imports:
        dataset = ReviewTimelineDataset.model_validate_json(
            (import_dir / "review_timeline.json").read_text("utf-8")
        )
        rid = dataset.source_recording_id or import_dir.name
        windows = select_windows(dataset, rid, args)
        for window in windows:
            number = len(entries) + args.first_number
            sheet, sample = render_window(dataset, window, config)
            cv2.imwrite(str(args.out / f"window_{number:02d}.png"), sheet)
            entries.append(_entry(number, window, dataset, sample, import_dir))
    template = {"schema_version": "1", "review_status": "draft", "windows": entries}
    (args.out / "labels_template.yaml").write_text(
        yaml.safe_dump(template, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    print(f"{len(entries)} windows -> {args.out}")
    return 0


def frame_transitions(dataset: ReviewTimelineDataset) -> list[Transition]:
    """Alive->dead lamp changes from individual frames, ordered by x."""
    frames = sorted(
        (
            s
            for s in dataset.samples
            if s.cursor.x is not None and s.clock_status != "implausible"
        ),
        key=lambda s: float(s.cursor.x or 0),
    )
    out: list[Transition] = []
    for slot in ROW_SLOTS:
        known = [
            (s, state)
            for s in frames
            for state in [
                next((o.state.value for o in s.slots if o.slot_id == slot), None)
            ]
            if state in ("alive", "dead")
        ]
        for (a, sa), (b, sb) in zip(known, known[1:], strict=False):
            gap = float(b.cursor.x or 0) - float(a.cursor.x or 0)
            if sa == "alive" and sb == "dead" and 0 < gap <= MAX_FRAME_GAP_PX:
                out.append(
                    Transition(slot, float(a.cursor.x or 0), float(b.cursor.x or 0), a, b)
                )
    return sorted(out, key=lambda t: t.x)


def select_windows(
    dataset: ReviewTimelineDataset, rid: str, args: argparse.Namespace
) -> list[Window]:
    """Deterministic windows per category for one recording."""
    transitions = frame_transitions(dataset)
    axis = dataset.axis_calibration
    px_per_s = 1920 / abs(axis.slope) if axis else 4.8
    windows = [
        _named(rid, spec, dataset) for spec in args.named if spec.split(":")[0] == rid
    ]
    if not args.named_only:
        windows += _automatic_windows(dataset, rid, transitions, args, px_per_s)
    windows = _merge(windows)
    for window in windows:
        window.transitions = [t for t in transitions if window.x_lo <= t.x <= window.x_hi]
    return windows


def _automatic_windows(
    dataset: ReviewTimelineDataset,
    rid: str,
    transitions: list[Transition],
    args: argparse.Namespace,
    px_per_s: float,
) -> list[Window]:
    windows: list[Window] = []
    isolated = [
        t
        for t in transitions
        if all(
            o is t or abs(o.x - t.x) > args.isolation_seconds * px_per_s
            for o in transitions
        )
    ]
    for row in ("top", "bottom"):
        windows += [
            _around(rid, "isolated", t, "no other lamp change nearby")
            for t in _spread([t for t in isolated if t.slot.startswith(row)], 1)
        ]
    unmarked = [t for t in transitions if not _has_marker(dataset, t, args, px_per_s)]
    for row in ("top", "bottom"):
        windows += [
            _around(rid, f"unmarked_{row}", t, "no same-slot death marker nearby")
            for t in _spread(
                [t for t in unmarked if t.slot.startswith(row)], args.unmarked
            )
        ]
    return windows


def _has_marker(
    dataset: ReviewTimelineDataset,
    transition: Transition,
    args: argparse.Namespace,
    px_per_s: float,
) -> bool:
    return any(
        m.kind == "death"
        and m.player_slot_id == transition.slot
        and abs(m.x_norm * 1920 - transition.x) <= args.marker_seconds * px_per_s
        for m in dataset.match_history_events
    )


def _named(rid: str, spec: str, dataset: ReviewTimelineDataset) -> Window:
    _, lo, hi, name = spec.split(":", maxsplit=3)
    axis = dataset.axis_calibration
    assert axis is not None, "named windows need an axis"
    xs = sorted((float(c) - axis.intercept) / axis.slope * 1920 for c in (lo, hi))
    return Window(rid, name, int(xs[0]), int(np.ceil(xs[1])), f"named clock {lo}..{hi}")


def _around(rid: str, category: str, t: Transition, reason: str) -> Window:
    half = WINDOW_PX // 2
    return Window(rid, category, int(t.x) - half, int(t.x) + half, f"{reason} ({t.slot})")


def _spread(items: list[Transition], count: int) -> list[Transition]:
    if len(items) <= count:
        return items
    step = len(items) / count
    return [items[int((i + 0.5) * step)] for i in range(count)]


def _merge(windows: list[Window]) -> list[Window]:
    """Merge overlapping windows; named categories win over automatic ones."""
    out: list[Window] = []
    for window in sorted(windows, key=lambda w: w.x_lo):
        last = out[-1] if out else None
        if last is not None and window.x_lo <= last.x_hi:
            keep = last if last.reason.startswith("named") else window
            keep_reason = " + ".join(sorted({last.reason, window.reason}))
            out[-1] = Window(
                last.recording,
                keep.category,
                min(last.x_lo, window.x_lo),
                max(last.x_hi, window.x_hi),
                keep_reason,
            )
        else:
            out.append(window)
    return out


def render_window(
    dataset: ReviewTimelineDataset, window: Window, config: Any
) -> tuple[np.ndarray, ReviewTimelineSample]:
    """Lane crop with guides and ruler, then roster crops per transition."""
    sample = _clear_frame(dataset, window, config)
    image = cv2.imread(sample.image_path)
    x1, y1, x2, y2 = lane_origin(image, config)
    lane = _scale(image[y1:y2, window.x_lo : window.x_hi], LANE_SCALE)
    panel = _lane_panel(lane, window, dataset, y2 - y1, config)
    parts = [
        _text_bar(
            f"{window.recording}  {window.category}  x {window.x_lo}-{window.x_hi}  "
            f"frame {sample.sample_id}",
            panel.shape[1],
        ),
        _text_bar(window.reason, panel.shape[1]),
        panel,
    ]
    for t in window.transitions:
        parts.append(_transition_strip(t, config, panel.shape[1]))
    width = max(p.shape[1] for p in parts)
    return np.vstack(
        [
            cv2.copyMakeBorder(p, 0, 4, 0, width - p.shape[1], cv2.BORDER_CONSTANT)
            for p in parts
        ]
    ), sample


def _clear_frame(
    dataset: ReviewTimelineDataset, window: Window, config: Any
) -> ReviewTimelineSample:
    """A production static frame whose cursor is far from the window."""
    static = select_static_samples(dataset.samples, config.static_frame_count)
    pool = static + [s for s in dataset.samples if s not in static]
    centre = (window.x_lo + window.x_hi) / 2
    return next(
        s for s in pool if s.cursor.x is not None and abs(s.cursor.x - centre) > 150
    )


def _lane_panel(
    lane: np.ndarray,
    window: Window,
    dataset: ReviewTimelineDataset,
    lane_h: int,
    config: Any,
) -> np.ndarray:
    margin_left, ruler = 150, 70
    panel = np.zeros((lane.shape[0] + ruler, lane.shape[1] + margin_left, 3), np.uint8)
    panel[ruler:, margin_left:] = lane
    for row, centre in enumerate(config.event_lane_row_centers):
        y = ruler + int(centre * lane_h * LANE_SCALE)
        cv2.line(panel, (margin_left - 12, y), (margin_left - 1, y), (0, 255, 255), 2)
        cv2.putText(
            panel,
            f"row {row} {ROW_SLOTS[row]}",
            (4, y + 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 255),
            1,
        )
    axis = dataset.axis_calibration
    for x in range(window.x_lo - window.x_lo % 5, window.x_hi + 1, 5):
        if x < window.x_lo:
            continue
        px = margin_left + (x - window.x_lo) * LANE_SCALE
        tall = x % 10 == 0
        cv2.line(
            panel, (px, ruler - (18 if tall else 8)), (px, ruler - 1), (200, 200, 200), 1
        )
        if tall:
            cv2.putText(
                panel,
                str(x),
                (px - 14, 18),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (255, 255, 255),
                1,
            )
            if axis is not None:
                clock = axis.elapsed_at(x / 1920)
                cv2.putText(
                    panel,
                    f"{clock:.1f}",
                    (px - 14, 36),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.38,
                    (150, 200, 255),
                    1,
                )
    return panel


def _transition_strip(t: Transition, config: Any, width: int) -> np.ndarray:
    band = (
        config.roster_top_band if t.slot.startswith("top") else config.roster_bottom_band
    )
    crops = [_roster_crop(s, band) for s in (t.alive_sample, t.dead_sample)]
    text = (
        f"lamp {t.slot}: alive @x {t.last_alive_x:.0f} "
        f"({t.alive_sample.displayed_clock}) -> dead @x {t.first_dead_x:.0f} "
        f"({t.dead_sample.displayed_clock})"
    )
    gap = np.zeros((crops[0].shape[0], 10, 3), np.uint8)
    body = np.hstack([crops[0], gap, crops[1]])
    return np.vstack([_text_bar(text, max(width, body.shape[1])), body])


def _roster_crop(sample: ReviewTimelineSample, band: tuple[float, float]) -> np.ndarray:
    image = cv2.imread(sample.image_path)
    h, w = image.shape[:2]
    x = int(sample.cursor.x or 0)
    crop = image[int(h * band[0]) : int(h * band[1]), max(0, x - 140) : min(w, x + 140)]
    return _scale(crop, ROSTER_SCALE)


def _text_bar(text: str, width: int) -> np.ndarray:
    bar = np.zeros((24, max(width, 10), 3), np.uint8)
    cv2.putText(bar, text, (4, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    return bar


def _scale(image: np.ndarray, factor: float) -> np.ndarray:
    return cv2.resize(image, None, fx=factor, fy=factor, interpolation=cv2.INTER_NEAREST)


def _entry(
    number: int,
    window: Window,
    dataset: ReviewTimelineDataset,
    sample: ReviewTimelineSample,
    import_dir: Path,
) -> dict[str, Any]:
    axis = dataset.axis_calibration
    clocks = (
        sorted(round(axis.elapsed_at(x / 1920), 2) for x in (window.x_lo, window.x_hi))
        if axis
        else None
    )
    return {
        "window_id": f"W{number:02d}",
        "recording": window.recording,
        "source_artifact": str(import_dir / "review_timeline.json"),
        "category": window.category,
        "x_range": [window.x_lo, window.x_hi],
        "clock_range": clocks,
        "render_sample_id": sample.sample_id,
        "selection": window.reason,
        "lamp_transitions": [
            {
                "slot": t.slot,
                "last_alive_x": round(t.last_alive_x, 1),
                "first_dead_x": round(t.first_dead_x, 1),
                "last_alive_clock": t.alive_sample.displayed_clock,
                "first_dead_clock": t.dead_sample.displayed_clock,
            }
            for t in window.transitions
        ],
        # true once every visible glyph in all eight rows is listed below
        "complete": None,
        # each: {row: 0-7, x: screen px, status: confirmed_death | ambiguous |
        # not_death_glyph, identity: death | <special_id> | unknown,
        # obscured: bool, notes: str}
        "glyphs": [],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("imports", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--named", action="append", default=[], help="rid:clock_lo:clock_hi:category"
    )
    parser.add_argument("--unmarked", type=int, default=2, help="per row per recording")
    parser.add_argument(
        "--named-only", action="store_true", help="only the --named windows"
    )
    parser.add_argument(
        "--first-number", type=int, default=1, help="window number to start from"
    )
    parser.add_argument("--isolation-seconds", type=float, default=5.0)
    parser.add_argument("--marker-seconds", type=float, default=3.0)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
