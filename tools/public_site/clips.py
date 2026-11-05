"""Presentation-only gameplay clips extracted after coaching selection.

Clips use recording seconds, never inferred game time. Source cadence is
preserved up to 60 fps; no frames are synthesized for lower-rate uploads.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger

from public_site.frames import Runner, frame_path, ranked_entries


def clip_path(analysis: Path, safe_id: str) -> Path | None:
    """Return a clip location using the same safe-ID boundary as stills."""
    still = frame_path(analysis, safe_id)
    return None if still is None else analysis / "public_clips" / f"{safe_id}.mp4"


def has_clip(analysis: Path, safe_id: str) -> bool:
    """Whether a completed clip is available."""
    path = clip_path(analysis, safe_id)
    return path is not None and path.is_file() and path.stat().st_size > 0


def missing_clips(analysis: Path) -> bool:
    """Whether any selected coaching moment still needs its clip."""
    return any(not has_clip(analysis, entry.safe_id) for entry in ranked_entries(analysis))


def clip_argv(video: Path, dest: Path, anchor: float) -> list[str]:
    """Encode five seconds before and three after the anchor, bounded by EOF.

    Near the beginning, shorten the lead rather than shifting the end.
    Preserve source audio when present using browser-compatible AAC.
    """
    start = max(0.0, anchor - 5.0)
    duration = anchor + 3.0 - start
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{duration:.3f}",
        "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
        "-c:a", "aac", "-b:a", "128k",
        "-vf", "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-fpsmax", "60", "-movflags", "+faststart",
        str(dest),
    ]


def extract_clips(
    analysis: Path,
    video: Path,
    *,
    submission_id: str = "",
    run: Runner | None = None,
) -> None:
    """Atomically write missing clips; failures leave the source retryable."""
    if not video.is_file():
        return
    for entry in ranked_entries(analysis):
        dest = clip_path(analysis, entry.safe_id)
        if dest is None or has_clip(analysis, entry.safe_id):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_suffix(".part.mp4")
        try:
            argv = clip_argv(video, partial, entry.video_time)
            code = run(argv) if run else subprocess.run(
                argv, capture_output=True, timeout=180, check=False,
            ).returncode
            if code == 0 and partial.is_file() and partial.stat().st_size > 0:
                partial.replace(dest)
                continue
        except (OSError, subprocess.TimeoutExpired):
            pass
        partial.unlink(missing_ok=True)
        logger.warning("moment clip extraction failed for submission {} moment {}", submission_id, entry.safe_id)
