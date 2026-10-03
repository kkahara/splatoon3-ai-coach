#!/usr/bin/env python3
"""Phase 0 research survey of Splatoon 3 review-feature recordings.

Research only: nothing here is imported by the pipeline, and outputs stay
under ``analysis/review_survey/``. Filename tags (``arial``, ``map``,
``timeline``, ``fast``...) are hints; the survey records what is on screen.

Subcommands:

- ``inventory``: container facts and filename tags -> ``inventory.json``.
- ``sheets``: 1 fps labeled contact sheets (``sheets/``) and full-resolution
  keyframes every ``--keyframe-every`` seconds (``frames/``).
- ``cuts``: mean absolute frame difference at 10 fps -> ``cuts.json`` and
  ``cuts/<name>.png``; large spikes are view switches, menus or seeks.
- ``motion``: per-region change at 60 fps inside labeled windows, summarized
  for 2/5/10/20/60 fps sampling -> ``motion.json``.
- ``speed``: playback speed from a moving review-UI element (playhead) or a
  game clock region over video time -> ``speed.json``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml
from loguru import logger

from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.media.video import VideoFrame, VideoLoader

STUDY_DIR = PROJECT_ROOT / "analysis" / "review_survey"
DEFAULT_GLOB = str(Path.home() / "Movies" / "en-review-*")
THUMB_W = 320
SHEET_COLS = 6
SHEET_ROWS = 5
DIFF_W = 160
SAMPLE_RATES = (2, 5, 10, 20, 60)
_VIEW_TAGS = ("arial", "map", "timeline", "results", "full")
_SPEED_TAGS = ("fast", "regularspeed", "regular")


@dataclass(frozen=True)
class Recording:
    """One review recording and the hints its filename carries."""

    path: Path

    @property
    def name(self) -> str:
        """Filesystem-safe short name used for output files."""
        stem = self.path.name.replace(".mov-.mp4", "").rsplit(".", 1)[0]
        return re.sub(r"[^A-Za-z0-9_-]+", "_", stem.removeprefix("en-review-"))

    @property
    def view_tags(self) -> list[str]:
        """View hints from the filename, in filename order."""
        parts = self.path.name.removeprefix("en-review-").split("-")
        return [part for part in parts if part in _VIEW_TAGS]

    @property
    def speed_tag(self) -> str | None:
        """Playback-speed hint from the filename, if any."""
        lowered = self.path.name.lower()
        return next((tag for tag in _SPEED_TAGS if f"-{tag}-" in lowered), None)

    @property
    def session(self) -> str | None:
        """Recording-session timestamp; equal values come from one sitting."""
        match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2})", self.path.name)
        return match.group(1) if match else None


def recordings(pattern: str) -> list[Recording]:
    """All recordings matching ``pattern``, sorted by name."""
    return [Recording(p) for p in sorted(Path("/").glob(pattern.lstrip("/")))]


def sampled(loader: VideoLoader, every_s: float) -> Iterator[VideoFrame]:
    """Yield roughly one decoded frame per ``every_s`` seconds of video time."""
    next_t = 0.0
    for frame in loader.frames():
        if frame.timestamp + 1e-6 >= next_t:
            next_t = frame.timestamp + every_s
            yield frame


def _ffprobe(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        capture_output=True, check=True, text=True,
    ).stdout
    return json.loads(out)


def cmd_inventory(args: argparse.Namespace) -> None:
    """Write container facts and filename hints for every recording."""
    rows = []
    for rec in recordings(args.glob):
        probe = _ffprobe(rec.path)
        video = next(s for s in probe["streams"] if s["codec_type"] == "video")
        rows.append(
            {
                "name": rec.name,
                "file": rec.path.name,
                "session": rec.session,
                "view_tags": rec.view_tags,
                "speed_tag": rec.speed_tag,
                "duration_s": round(float(probe["format"]["duration"]), 3),
                "size_mb": round(int(probe["format"]["size"]) / 1e6, 1),
                "width": video["width"],
                "height": video["height"],
                "fps": video["r_frame_rate"],
                "codec": video["codec_name"],
                "has_audio": any(s["codec_type"] == "audio" for s in probe["streams"]),
            }
        )
    _write_json(STUDY_DIR / "inventory.json", rows)
    logger.info("inventory: {} recordings", len(rows))


def _label(image: np.ndarray, text: str) -> np.ndarray:
    out = image.copy()
    cv2.rectangle(out, (0, 0), (out.shape[1], 22), (0, 0, 0), -1)
    cv2.putText(out, text, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return out


def _flush_sheet(thumbs: list[np.ndarray], path: Path) -> None:
    h, w = thumbs[0].shape[:2]
    sheet = np.zeros((h * SHEET_ROWS, w * SHEET_COLS, 3), dtype=np.uint8)
    for i, thumb in enumerate(thumbs):
        r, c = divmod(i, SHEET_COLS)
        sheet[r * h : (r + 1) * h, c * w : (c + 1) * w] = thumb
    cv2.imwrite(str(path), sheet)


def cmd_sheets(args: argparse.Namespace) -> None:
    """1 fps contact sheets and periodic full-resolution keyframes."""
    sheets_dir = STUDY_DIR / "sheets"
    frames_dir = STUDY_DIR / "frames"
    for rec in _selected(args):
        thumbs: list[np.ndarray] = []
        page = 0
        next_key = 0.0
        out_frames = frames_dir / rec.name
        out_frames.mkdir(parents=True, exist_ok=True)
        with VideoLoader(rec.path) as loader:
            for frame in sampled(loader, 1.0):
                t = frame.timestamp
                if t + 1e-6 >= next_key:
                    cv2.imwrite(str(out_frames / f"t{t:07.2f}.jpg"), frame.image)
                    next_key = t + args.keyframe_every
                h = int(frame.image.shape[0] * THUMB_W / frame.image.shape[1])
                thumb = cv2.resize(frame.image, (THUMB_W, h), interpolation=cv2.INTER_AREA)
                thumbs.append(_label(thumb, f"{rec.name[:28]} {t:6.1f}s"))
                if len(thumbs) == SHEET_COLS * SHEET_ROWS:
                    sheets_dir.mkdir(parents=True, exist_ok=True)
                    _flush_sheet(thumbs, sheets_dir / f"{rec.name}_{page:03d}.png")
                    thumbs, page = [], page + 1
        if thumbs:
            sheets_dir.mkdir(parents=True, exist_ok=True)
            _flush_sheet(thumbs, sheets_dir / f"{rec.name}_{page:03d}.png")
            page += 1
        logger.info("sheets: {} -> {} pages", rec.name, page)


def _small_gray(image: np.ndarray) -> np.ndarray:
    h = int(image.shape[0] * DIFF_W / image.shape[1])
    small = cv2.resize(image, (DIFF_W, h), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)


def cmd_cuts(args: argparse.Namespace) -> None:
    """Frame-difference timeline at 10 fps; spikes mark view changes."""
    summary: dict[str, Any] = {}
    plot_dir = STUDY_DIR / "cuts"
    plot_dir.mkdir(parents=True, exist_ok=True)
    for rec in _selected(args):
        times: list[float] = []
        diffs: list[float] = []
        prev: np.ndarray | None = None
        with VideoLoader(rec.path) as loader:
            for frame in sampled(loader, 0.1):
                gray = _small_gray(frame.image)
                if prev is not None:
                    times.append(round(frame.timestamp, 2))
                    diffs.append(round(float(np.mean(np.abs(gray - prev))), 2))
                prev = gray
        cuts = [
            {"t": t, "diff": d}
            for t, d in zip(times, diffs, strict=True)
            if d >= args.cut_threshold
        ]
        static = sum(1 for d in diffs if d < 0.5) / max(len(diffs), 1)
        summary[rec.name] = {
            "samples": len(diffs),
            "median_diff": float(np.median(diffs)) if diffs else None,
            "p95_diff": float(np.percentile(diffs, 95)) if diffs else None,
            "static_fraction": round(static, 3),
            "cuts": cuts,
        }
        _plot_series(times, diffs, args.cut_threshold, plot_dir / f"{rec.name}.png")
        logger.info("cuts: {} -> {} spikes", rec.name, len(cuts))
    _write_json(STUDY_DIR / "cuts.json", summary)


def _plot_series(times: list[float], values: list[float], line: float, path: Path) -> None:
    w, h = 1600, 300
    canvas = np.full((h, w, 3), 255, dtype=np.uint8)
    if not times:
        cv2.imwrite(str(path), canvas)
        return
    t_max = max(times[-1], 1.0)
    v_max = max(max(values), line * 1.5, 1.0)

    def xy(t: float, v: float) -> tuple[int, int]:
        return int(t / t_max * (w - 1)), int(h - 1 - v / v_max * (h - 20))

    cv2.line(canvas, xy(0, line), xy(t_max, line), (0, 0, 255), 1)
    for t0 in range(0, int(t_max) + 1, 10):
        x = xy(t0, 0)[0]
        cv2.line(canvas, (x, h - 6), (x, h - 1), (120, 120, 120), 1)
    pts = np.array([xy(t, v) for t, v in zip(times, values, strict=True)], np.int32)
    cv2.polylines(canvas, [pts], False, (40, 40, 40), 1)
    cv2.imwrite(str(path), canvas)


def _crop(image: np.ndarray, roi: list[float]) -> np.ndarray:
    h, w = image.shape[:2]
    x0, y0, x1, y1 = roi
    return image[int(y0 * h) : int(y1 * h), int(x0 * w) : int(x1 * w)]


def _region_gray(image: np.ndarray, roi: list[float], scale: float) -> np.ndarray:
    crop = _crop(image, roi)
    small = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def _load_rois() -> dict[str, Any]:
    path = STUDY_DIR / "rois.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _window_frames(rec: Recording, start: float, end: float) -> list[VideoFrame]:
    out: list[VideoFrame] = []
    with VideoLoader(rec.path) as loader:
        for frame in loader.frames():
            if frame.timestamp < start:
                continue
            if frame.timestamp > end:
                break
            out.append(frame)
    return out


def _region_motion(frames: list[VideoFrame], roi: list[float], flow: bool) -> dict[str, Any]:
    """Change between samples taken at each rate in ``SAMPLE_RATES``."""
    scale = 0.5
    grays = [_region_gray(f.image, roi, scale) for f in frames]
    fps = (len(frames) - 1) / max(frames[-1].timestamp - frames[0].timestamp, 1e-6)
    result: dict[str, Any] = {}
    for rate in SAMPLE_RATES:
        step = max(1, round(fps / rate))
        pairs = list(zip(grays[::step], grays[step::step], strict=False))
        diffs = [float(np.mean(cv2.absdiff(a, b))) for a, b in pairs]
        entry: dict[str, Any] = {
            "step_frames": step,
            "mean_abs_diff_p50": round(float(np.median(diffs)), 2) if diffs else None,
            "mean_abs_diff_p95": round(float(np.percentile(diffs, 95)), 2) if diffs else None,
        }
        if flow and pairs:
            mags = [_flow_p95(a, b) / scale for a, b in pairs[:: max(1, len(pairs) // 40)]]
            entry["flow_p95_px_p50"] = round(float(np.median(mags)), 1)
            entry["flow_p95_px_max"] = round(float(np.max(mags)), 1)
        result[f"{rate}fps"] = entry
    result["flashes"] = _flashes(grays, fps)
    return result


def _flow_p95(a: np.ndarray, b: np.ndarray) -> float:
    flow = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    mag = np.linalg.norm(flow, axis=2)
    return float(np.percentile(mag, 95))


def _flashes(grays: list[np.ndarray], fps: float) -> dict[str, Any]:
    """Short-lived changes: runs of frames that differ from the frame before the run."""
    durations: list[float] = []
    i = 1
    while i < len(grays):
        if float(np.mean(cv2.absdiff(grays[i], grays[i - 1]))) < 6.0:
            i += 1
            continue
        base = grays[i - 1]
        j = i
        while j < len(grays) and float(np.mean(cv2.absdiff(grays[j], base))) >= 6.0:
            j += 1
        durations.append((j - i) / fps)
        i = j + 1
    shorter = {f"under_{1 / r:.2f}s": sum(1 for d in durations if d < 1 / r) for r in (2, 5, 10)}
    return {"count": len(durations), **shorter}


def cmd_motion(args: argparse.Namespace) -> None:
    """Per-region change at 60 fps inside the windows listed in rois.yaml."""
    config = _load_rois()
    by_name = {rec.name: rec for rec in recordings(args.glob)}
    results: dict[str, Any] = {}
    for window in config["motion_windows"]:
        rec = by_name[window["recording"]]
        frames = _window_frames(rec, window["start"], window["end"])
        view = config["views"][window["view"]]
        key = f"{rec.name}@{window['start']}-{window['end']}"
        results[key] = {"view": window["view"], "frames": len(frames), "regions": {}}
        for region, spec in view["regions"].items():
            results[key]["regions"][region] = _region_motion(
                frames, spec["roi"], flow=bool(spec.get("flow"))
            )
        logger.info("motion: {} ({} frames)", key, len(frames))
    _write_json(STUDY_DIR / "motion.json", results)


def cmd_speed(args: argparse.Namespace) -> None:
    """Game clock vs video time: playback speed, pauses and seeks per recording."""
    from splatoon3_ai_coach.config.loader import load_config
    from splatoon3_ai_coach.config.paths import default_config_path
    from splatoon3_ai_coach.vision.timer import TimerDetector

    vision = load_config(default_config_path()).vision
    detector = TimerDetector(vision.timer, cadence_fps=2.0)
    results: dict[str, Any] = {}
    for rec in _selected(args):
        samples: list[tuple[float, float]] = []
        with VideoLoader(rec.path) as loader:
            for frame in sampled(loader, 0.5):
                reading, confidence = detector.detect(frame.image, frame.timestamp)
                if reading is not None and confidence >= vision.timer.min_usable_confidence:
                    samples.append((round(frame.timestamp, 2), reading.seconds_remaining))
        results[rec.name] = {"samples": samples, "segments": _speed_segments(samples)}
        logger.info("speed: {} -> {} clock samples", rec.name, len(samples))
    _write_json(STUDY_DIR / "speed.json", results)


def _speed_segments(samples: list[tuple[float, float]]) -> list[dict[str, Any]]:
    """Split where the clock jumps or reverses; fit game-seconds per video-second."""
    segments: list[list[tuple[float, float]]] = []
    for t, clock in samples:
        if segments:
            pt, pc = segments[-1][-1]
            dt, dc = t - pt, pc - clock
            if dt <= 0 or dc < -1.5 or dc > 4.0 * dt + 1.5:
                segments.append([])
        else:
            segments.append([])
        segments[-1].append((t, clock))
    out = []
    for seg in segments:
        if len(seg) < 6:
            continue
        ts = np.array([s[0] for s in seg])
        cs = np.array([s[1] for s in seg])
        slope = float(np.polyfit(ts, cs, 1)[0])
        out.append(
            {
                "video_start": float(ts[0]),
                "video_end": float(ts[-1]),
                "clock_start": float(cs[0]),
                "clock_end": float(cs[-1]),
                "game_s_per_video_s": round(-slope, 3),
                "samples": len(seg),
            }
        )
    return out


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _selected(args: argparse.Namespace) -> list[Recording]:
    found = recordings(args.glob)
    if args.only:
        found = [r for r in found if any(key in r.name for key in args.only)]
    return found


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--glob", default=DEFAULT_GLOB)
    parser.add_argument("--only", nargs="*", default=[], help="substring filter on names")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inventory").set_defaults(func=cmd_inventory)
    sheets = sub.add_parser("sheets")
    sheets.add_argument("--keyframe-every", type=float, default=5.0)
    sheets.set_defaults(func=cmd_sheets)
    cuts = sub.add_parser("cuts")
    cuts.add_argument("--cut-threshold", type=float, default=25.0)
    cuts.set_defaults(func=cmd_cuts)
    sub.add_parser("motion").set_defaults(func=cmd_motion)
    sub.add_parser("speed").set_defaults(func=cmd_speed)
    args = parser.parse_args()
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    args.func(args)


if __name__ == "__main__":
    main()
