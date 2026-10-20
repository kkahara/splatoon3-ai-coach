#!/usr/bin/env python3
"""Prototype probe for the end-of-match "GAME!" tape (match-end evidence).

The tape covers the HUD timer at the end of every match, so the 0:00 reading
is usually never sampled. Its layout is fixed while its color varies, so the
probe uses a layout mask and asks whether the masked area is filled by one
uniform saturated color (language- and color-neutral).

Commands::

    # build the band mask from settled-tape reference frames (video:time)
    python tools/diagnostics/game_tape_probe.py mask OUT.png VIDEO:T [VIDEO:T ...]
    # score every 0.5 s frame of each video and report tape onsets
    python tools/diagnostics/game_tape_probe.py scan MASK.png VIDEO [VIDEO ...]

The first mask was built from these settled-tape references (2026-10-11
playerview recordings): ``22-57-30:318.0``, ``22-20-42:326.0``,
``22-31-53:221.5``, ``18-25-16:199.5``.

Prototype only: nothing in ``src/`` reads this mask or these scores.
"""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

WIDTH, HEIGHT = 480, 270
SAMPLE_FPS = 2.0
MIN_SAT, MIN_VAL = 90, 90
HUE_TOLERANCE = 10


def frames(
    video: Path, start: float = 0.0, duration: float | None = None
) -> Iterator[tuple[float, np.ndarray]]:
    """Decode ``video`` at ``SAMPLE_FPS`` as small BGR frames with their time."""
    cmd = ["ffmpeg", "-loglevel", "error", "-ss", f"{start}", "-i", str(video)]
    if duration is not None:
        cmd += ["-t", f"{duration}"]
    cmd += [
        "-vf",
        f"fps={SAMPLE_FPS},scale={WIDTH}:{HEIGHT}",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-",
    ]
    size = WIDTH * HEIGHT * 3
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
        assert proc.stdout is not None
        index = 0
        while chunk := proc.stdout.read(size):
            if len(chunk) < size:
                break
            image = np.frombuffer(chunk, np.uint8).reshape(HEIGHT, WIDTH, 3)
            yield start + index / SAMPLE_FPS, image
            index += 1


def _saturated(hsv: np.ndarray) -> np.ndarray:
    return (hsv[..., 1] >= MIN_SAT) & (hsv[..., 2] >= MIN_VAL)


def _hue_distance(hue: np.ndarray, center: float) -> np.ndarray:
    diff = np.abs(hue.astype(np.int16) - int(round(center)))
    return np.minimum(diff, 180 - diff)


def dominant_hue(hsv: np.ndarray, region: np.ndarray) -> float | None:
    """Most common hue (OpenCV 0-179) among saturated pixels in ``region``."""
    pick = region & _saturated(hsv)
    if not pick.any():
        return None
    hist = np.bincount(hsv[..., 0][pick].ravel(), minlength=180)
    return float(np.argmax(hist))


def hue_peaks(hsv: np.ndarray, count: int = 3) -> list[int]:
    """Up to ``count`` distinct saturated-hue peaks (at least 2x tolerance apart)."""
    hist = np.bincount(hsv[..., 0][_saturated(hsv)].ravel(), minlength=180)
    peaks: list[int] = []
    for hue in np.argsort(hist)[::-1]:
        if all(_hue_distance(np.array([hue]), p)[0] > 2 * HUE_TOLERANCE for p in peaks):
            peaks.append(int(hue))
        if len(peaks) == count:
            break
    return peaks


def hue_mask(hsv: np.ndarray, hue: float) -> np.ndarray:
    """Saturated pixels within ``HUE_TOLERANCE`` of ``hue``."""
    return _saturated(hsv) & (_hue_distance(hsv[..., 0], hue) <= HUE_TOLERANCE)


def consensus_mask(images: Sequence[np.ndarray]) -> np.ndarray:
    """Largest intersection over one hue peak per settled-tape reference.

    The tape is not always the dominant hue (uniform ink can outweigh it), but
    its layout is the only region shared by every reference.
    """
    candidates = []
    for image in images:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        candidates.append([hue_mask(hsv, hue) for hue in hue_peaks(hsv)])
    best = np.zeros(images[0].shape[:2], bool)
    for combo in itertools.product(*candidates):
        mask = np.logical_and.reduce(combo)
        if mask.sum() > best.sum():
            best = mask
    return best


@dataclass(frozen=True)
class TapeLayout:
    """Band pixels, the ring just outside the bands, and the text holes."""

    band: np.ndarray
    ring: np.ndarray
    text: np.ndarray

    @classmethod
    def from_mask(cls, mask: np.ndarray) -> TapeLayout:
        """Derive the ring and text holes from the band mask."""
        raw = mask.astype(np.uint8)
        closed = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8)) > 0
        outer = cv2.dilate(closed.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
        return cls(band=mask, ring=outer & ~closed, text=closed & ~mask)


@dataclass(frozen=True)
class TapeFeatures:
    """``fill``: band pixels on the band hue; ``contrast``: ring pixels off it;
    ``text_dark``: dark pixels in the text holes (language-specific layout)."""

    fill: float
    contrast: float
    text_dark: float


def tape_features(image: np.ndarray, layout: TapeLayout) -> TapeFeatures:
    """Measure the three tape features on one frame."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue = dominant_hue(hsv, layout.band)
    if hue is None:
        return TapeFeatures(0.0, 0.0, 0.0)
    on_hue = hue_mask(hsv, hue)
    dark = hsv[..., 2] < 100
    return TapeFeatures(
        fill=float((on_hue & layout.band).sum() / layout.band.sum()),
        contrast=float((~on_hue & layout.ring).sum() / layout.ring.sum()),
        text_dark=float((dark & layout.text).sum() / layout.text.sum()),
    )


def _reference(spec: str) -> np.ndarray:
    video, _, time = spec.rpartition(":")
    for _, image in frames(Path(video), float(time), 0.5):
        return image
    raise SystemExit(f"no frame at {spec}")


def build_mask(specs: Sequence[str], out: Path) -> int:
    """Consensus band mask over the references, with the edges eroded."""
    mask = consensus_mask([_reference(spec) for spec in specs]).astype(np.uint8) * 255
    mask = cv2.erode(mask, np.ones((3, 3), np.uint8))
    cv2.imwrite(str(out), mask)
    sys.stdout.write(f"mask {out}: {int((mask > 0).sum())} px of {WIDTH * HEIGHT}\n")
    return 0


def scan_video(video: Path, layout: TapeLayout) -> dict:
    """Measure every sampled frame of one video."""
    rows = [
        {
            "t": t,
            **{k: round(v, 3) for k, v in asdict(tape_features(image, layout)).items()},
        }
        for t, image in frames(video)
    ]
    return {"video": video.name, "frames": rows}


def scan(mask_path: Path, videos: Sequence[str], out: Path) -> int:
    """Measure every 0.5 s frame of each video and write the features as JSON."""
    layout = TapeLayout.from_mask(cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE) > 0)
    results = []
    for video in videos:
        results.append(scan_video(Path(video), layout))
        sys.stdout.write(f"{Path(video).name}: {len(results[-1]['frames'])} frames\n")
    out.write_text(json.dumps(results) + "\n", encoding="utf-8")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("mask")
    m.add_argument("out", type=Path)
    m.add_argument("refs", nargs="+", help="VIDEO:SECONDS of a settled tape frame")
    s = sub.add_parser("scan")
    s.add_argument("mask", type=Path)
    s.add_argument("videos", nargs="+")
    s.add_argument("--json", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "mask":
        return build_mask(args.refs, args.out)
    return scan(args.mask, args.videos, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
