"""Viewer-only team color interpretation from early roster-slot ROIs.

Not production ``TeamColorCalibration``. Samples high-S pixels in the same
geometry as ``player_count`` slots on early ``debug_snapshots`` frames so the
manifest viewer can show ally/opponent swatches and a per-slot probe report.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from vision_manifest_viewer.model import (
    MatchIdentityView,
    TeamColorSideView,
    TeamColorSlotView,
    TeamColorsView,
)

# Defaults mirror PlayerCountDetectorConfig @ 1920×1080.
_DEFAULT_ALLY_SLOTS: list[tuple[float, float, float, float]] = [
    (0.254167, 0.015741, 0.328646, 0.112963),
    (0.308333, 0.017593, 0.375521, 0.112037),
    (0.358854, 0.011111, 0.416146, 0.112963),
    (0.407292, 0.013889, 0.464583, 0.115741),
]
_DEFAULT_OPP_SLOTS: list[tuple[float, float, float, float]] = [
    (0.536458, 0.013889, 0.594271, 0.115741),
    (0.585417, 0.013889, 0.642188, 0.115741),
    (0.623437, 0.017593, 0.681771, 0.110185),
    (0.664583, 0.013889, 0.738021, 0.109259),
]

_SNAP_RE = re.compile(
    r"^(?P<idx>\d+)_(?P<sec>\d+)\.(?P<frac>\d+)\.(?:jpg|jpeg|png)$",
    re.IGNORECASE,
)
_S_MIN = 70
_V_MIN = 40
_MIN_PIXELS_PER_SLOT = 10
_MIN_PIXELS_SIDE = 40
_WINDOW_SECONDS = 60.0
_MIN_HUE_SEPARATION = 30.0


def interpret_team_colors(
    analysis_dir: Path,
    *,
    config_path: Path | None = None,
    match_identity: MatchIdentityView | None = None,
) -> TeamColorsView | None:
    """Prefer latched match_identity binding; else probe early debug_snapshots."""
    latched = _from_latched_calibration(match_identity)
    if latched is not None:
        return latched
    return _probe_debug_snapshots(
        analysis_dir,
        config_path=config_path,
        match_identity=match_identity,
    )


def _from_latched_calibration(
    match_identity: MatchIdentityView | None,
) -> TeamColorsView | None:
    """Build swatches from production ``team_color_calibration`` when present."""
    if match_identity is None or match_identity.team_color_calibration is None:
        return None
    try:
        import numpy as np
    except ImportError:  # pragma: no cover
        return None
    cal = match_identity.team_color_calibration
    ally = TeamColorSideView(
        h_median=round(cal.ally_h, 1),
        css_hex=_hsv_to_hex(cal.ally_h, 200, 210, np=np),
        sample_count=1,
    )
    opponent = TeamColorSideView(
        h_median=round(cal.opponent_h, 1),
        css_hex=_hsv_to_hex(cal.opponent_h, 200, 210, np=np),
        sample_count=1,
    )
    return TeamColorsView(
        ally=ally,
        opponent=opponent,
        source=cal.source or "hud_roster_slots",
        sample_frame_count=1,
        sample_time_start=cal.calibrated_at,
        sample_time_end=cal.calibrated_at,
        separation_degrees=round(cal.separation_degrees, 1),
        note=(
            f"Latched TeamColorCalibration @ {cal.calibrated_at:.1f}s "
            f"(separation {cal.separation_degrees:.0f}°)."
        ),
        calibration_latched=True,
    )


def _probe_debug_snapshots(
    analysis_dir: Path,
    *,
    config_path: Path | None = None,
    match_identity: MatchIdentityView | None = None,
) -> TeamColorsView | None:
    """Estimate ally/opponent hues from early debug_snapshots roster crops."""
    try:
        import cv2
        import numpy as np
    except ImportError:  # pragma: no cover
        return None

    snap_dir = analysis_dir / "debug_snapshots"
    if not snap_dir.is_dir():
        return None

    ally_slots, opp_slots = _load_roster_slots(config_path)
    t0, t1 = _sample_window(match_identity)
    frames = _list_snapshots_in_window(snap_dir, t0, t1)
    if not frames:
        return None

    # Per slot: lists of (h_median, usable_pixels) across accepted frames.
    ally_slot_h: list[list[float]] = [[] for _ in range(4)]
    ally_slot_usable: list[list[int]] = [[] for _ in range(4)]
    opp_slot_h: list[list[float]] = [[] for _ in range(4)]
    opp_slot_usable: list[list[int]] = [[] for _ in range(4)]
    used_times: list[float] = []

    for ts, path in frames:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            continue
        ally_samples = _slot_samples(image, ally_slots, np=np, cv2=cv2)
        opp_samples = _slot_samples(image, opp_slots, np=np, cv2=cv2)
        ah = _aggregate_frame_hue(ally_samples, np=np)
        oh = _aggregate_frame_hue(opp_samples, np=np)
        if ah is None or oh is None:
            continue
        if _circular_hue_distance(ah, oh) < _MIN_HUE_SEPARATION:
            continue
        used_times.append(ts)
        for i, sample in enumerate(ally_samples):
            if sample is None:
                continue
            h, usable = sample
            ally_slot_h[i].append(h)
            ally_slot_usable[i].append(usable)
        for i, sample in enumerate(opp_samples):
            if sample is None:
                continue
            h, usable = sample
            opp_slot_h[i].append(h)
            opp_slot_usable[i].append(usable)

    if not used_times:
        return None

    ally = _side_from_slots(ally_slot_h, ally_slot_usable, np=np)
    opponent = _side_from_slots(opp_slot_h, opp_slot_usable, np=np)
    if ally is None or opponent is None:
        return None

    return TeamColorsView(
        ally=ally,
        opponent=opponent,
        source="hud_roster_slots",
        sample_frame_count=len(used_times),
        sample_time_start=min(used_times),
        sample_time_end=max(used_times),
        separation_degrees=round(
            _circular_hue_distance(ally.h_median, opponent.h_median), 1
        ),
        note=(
            "Calibration: unavailable — using YAML fallback. "
            "Swatches from viewer debug_snapshots probe (not authoritative)."
        ),
        calibration_latched=False,
    )


def format_team_color_probe(view: TeamColorsView | None) -> str:
    """Human-readable TEAM COLOR PROBE report."""
    if view is None or (view.ally is None and view.opponent is None):
        return "TEAM COLOR PROBE\n(no usable early roster-slot samples)"

    lines = [
        "TEAM COLOR PROBE",
        f"Source: {view.source}",
    ]
    frame_line = f"Frames: {view.sample_frame_count}"
    if view.sample_time_start is not None and view.sample_time_end is not None:
        frame_line += (
            f"  t={view.sample_time_start:.1f}–{view.sample_time_end:.1f}s"
        )
    lines.append(frame_line)
    lines.append("")
    lines.extend(_format_side_block("ALLY", view.ally))
    lines.append("")
    lines.extend(_format_side_block("OPPONENT", view.opponent))
    lines.append("")
    if view.separation_degrees is not None:
        lines.append(f"separation: {view.separation_degrees:.0f}°")
    if view.note:
        lines.append(f"note: {view.note}")
    return "\n".join(lines)


def _format_side_block(title: str, side: TeamColorSideView | None) -> list[str]:
    if side is None:
        return [title, "  (none)"]
    lines = [title]
    for slot in side.slots:
        if slot.h_median is None:
            lines.append(
                f"  slot {slot.slot_index}: —  usable {slot.usable_pixels}"
            )
        else:
            lines.append(
                f"  slot {slot.slot_index}: H {slot.h_median:.0f}  "
                f"usable {slot.usable_pixels}"
            )
    lines.append(f"  aggregate: H {side.h_median:.0f}")
    return lines


def _sample_window(
    match_identity: MatchIdentityView | None,
) -> tuple[float, float]:
    """Scan from identity resolve through Ready/Go into early in-match HUD."""
    if match_identity is not None and match_identity.resolved_at is not None:
        t0 = float(match_identity.resolved_at)
        return t0, t0 + _WINDOW_SECONDS
    return 5.0, 5.0 + _WINDOW_SECONDS


def _circular_hue_distance(a: float, b: float) -> float:
    """Shortest distance on OpenCV H circle ``[0, 180)``."""
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _list_snapshots_in_window(
    snap_dir: Path,
    t0: float,
    t1: float,
) -> list[tuple[float, Path]]:
    """Parse debug_snapshots filenames and keep those in ``[t0, t1]``."""
    found: list[tuple[float, Path]] = []
    for path in sorted(snap_dir.iterdir()):
        if not path.is_file():
            continue
        match = _SNAP_RE.match(path.name)
        if not match:
            continue
        ts = float(f"{int(match.group('sec'))}.{match.group('frac')}")
        if t0 <= ts <= t1:
            found.append((ts, path))
    return found


def _load_roster_slots(
    config_path: Path | None,
) -> tuple[
    list[tuple[float, float, float, float]],
    list[tuple[float, float, float, float]],
]:
    """Load ally/opponent slot boxes from vision config when present."""
    ally = list(_DEFAULT_ALLY_SLOTS)
    opp = list(_DEFAULT_OPP_SLOTS)
    path = config_path
    if path is None:
        candidate = Path("configs/default.yaml")
        if candidate.is_file():
            path = candidate
    if path is None or not path.is_file():
        return ally, opp
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        return ally, opp
    vision = data.get("vision") or {}
    pc = vision.get("player_count") or {}
    loaded_ally = _parse_slots(pc.get("ally_slots"))
    loaded_opp = _parse_slots(pc.get("opponent_slots"))
    if loaded_ally:
        ally = loaded_ally
    if loaded_opp:
        opp = loaded_opp
    return ally, opp


def _parse_slots(raw: Any) -> list[tuple[float, float, float, float]]:
    if not isinstance(raw, list):
        return []
    out: list[tuple[float, float, float, float]] = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 4:
            out.append(
                (float(item[0]), float(item[1]), float(item[2]), float(item[3]))
            )
    return out if len(out) == 4 else []


def _slot_samples(
    image: Any,
    slots: list[tuple[float, float, float, float]],
    *,
    np: Any,
    cv2: Any,
) -> list[tuple[float, int] | None]:
    """Per-slot ``(h_median, usable_pixels)`` or ``None`` if too few pixels."""
    height, width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    out: list[tuple[float, int] | None] = []
    for box in slots:
        left = max(0, min(width, int(box[0] * width)))
        top = max(0, min(height, int(box[1] * height)))
        right = max(0, min(width, int(box[2] * width)))
        bottom = max(0, min(height, int(box[3] * height)))
        if right <= left or bottom <= top:
            out.append(None)
            continue
        crop = hsv[top:bottom, left:right]
        mask = (crop[:, :, 1] >= _S_MIN) & (crop[:, :, 2] >= _V_MIN)
        usable = int(np.count_nonzero(mask))
        if usable < _MIN_PIXELS_PER_SLOT:
            out.append(None)
            continue
        h_med = float(np.median(crop[:, :, 0][mask].astype(np.float64)))
        out.append((h_med, usable))
    return out


def _aggregate_frame_hue(
    samples: list[tuple[float, int] | None],
    *,
    np: Any,
) -> float | None:
    """Median H across usable slots on one frame (weighted by nothing — slot medians)."""
    hues = [h for item in samples if item is not None for h in (item[0],)]
    usable_total = sum(u for item in samples if item is not None for u in (item[1],))
    if not hues or usable_total < _MIN_PIXELS_SIDE:
        return None
    return float(np.median(np.asarray(hues, dtype=np.float64)))


def _side_from_slots(
    slot_h: list[list[float]],
    slot_usable: list[list[int]],
    *,
    np: Any,
) -> TeamColorSideView | None:
    """Build side view from per-slot frame lists."""
    slots: list[TeamColorSlotView] = []
    slot_medians: list[float] = []
    for index in range(4):
        hues = slot_h[index]
        usables = slot_usable[index]
        if not hues:
            slots.append(
                TeamColorSlotView(
                    slot_index=index + 1,
                    h_median=None,
                    usable_pixels=0,
                    sample_frames=0,
                )
            )
            continue
        h_med = float(np.median(np.asarray(hues, dtype=np.float64)))
        usable_med = int(np.median(np.asarray(usables, dtype=np.float64)))
        slots.append(
            TeamColorSlotView(
                slot_index=index + 1,
                h_median=round(h_med, 1),
                usable_pixels=usable_med,
                sample_frames=len(hues),
            )
        )
        slot_medians.append(h_med)

    if not slot_medians:
        return None
    h_agg = float(np.median(np.asarray(slot_medians, dtype=np.float64)))
    return TeamColorSideView(
        h_median=round(h_agg, 1),
        css_hex=_hsv_to_hex(h_agg, 200, 210, np=np),
        sample_count=sum(s.sample_frames for s in slots),
        slots=slots,
    )


def _hsv_to_hex(h: float, s: float, v: float, *, np: Any) -> str:
    """OpenCV HSV → ``#rrggbb`` for UI swatches."""
    import cv2

    cell = np.uint8([[[int(round(h)) % 180, int(s), int(v)]]])
    bgr = cv2.cvtColor(cell, cv2.COLOR_HSV2BGR)[0, 0]
    return f"#{int(bgr[2]):02x}{int(bgr[1]):02x}{int(bgr[0]):02x}"
