"""Ground-truth windows from vision-manifest-viewer exports."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig, VisionLanguage
from splatoon3_ai_coach.vision.events import infer_events
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    GameEventReason,
    GameEventType,
    GameStateSnapshot,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.registry import build_detectors
from splatoon3_ai_coach.vision.respawn import RespawnDetector
from splatoon3_ai_coach.vision.state import fuse_game_state


def _ja_config() -> AppConfig:
    """GT fixtures in this suite use Japanese UI text templates."""
    config = load_config(default_config_path())
    config.vision.language = VisionLanguage.JA
    return config

_REPO = Path(__file__).resolve().parents[1]
_GT_PATH = (
    _REPO
    / "tests"
    / "fixtures"
    / "ground_truth"
    / "2026-09-06_22-35-09_ground_truth.json"
)
_GT_091730 = (
    _REPO
    / "tests"
    / "fixtures"
    / "ground_truth"
    / "2026-09-07_09-17-30_ground_truth.json"
)
_GT_093424 = (
    _REPO
    / "tests"
    / "fixtures"
    / "ground_truth"
    / "2026-09-05_09-34-24_ground_truth.json"
)
_GT_140214 = (
    _REPO
    / "tests"
    / "fixtures"
    / "ground_truth"
    / "2026-09-07_14-02-14_ground_truth.json"
)
_GT_140214_FROZEN = (
    _REPO
    / "tests"
    / "fixtures"
    / "ground_truth"
    / "2026-09-07_14-02-14_ground_truth_frozen.json"
)
_GT_155836 = (
    _REPO
    / "tests"
    / "fixtures"
    / "ground_truth"
    / "2026-09-10_15-58-36_ground_truth.json"
)
_SNAPS_155836 = _REPO / "analysis" / "2026-09-10 15-58-36" / "debug_snapshots"
_SLICE_091730 = (
    _REPO
    / "tests"
    / "fixtures"
    / "vision_frames"
    / "2026-09-07_09-17-30_120-148.json"
)
_SLICE_093424 = (
    _REPO
    / "tests"
    / "fixtures"
    / "vision_frames"
    / "2026-09-05_09-34-24_70-90.json"
)
_SLICE_140214 = (
    _REPO
    / "tests"
    / "fixtures"
    / "vision_frames"
    / "2026-09-07_14-02-14_190-227.json"
)
_MANIFEST_140214 = _REPO / "analysis" / "2026-09-07 14-02-14" / "vision_manifest.json"
_SESSION = _REPO / "analysis" / "2026-09-06 22-35-09"
_FRAMES = _SESSION / "debug_snapshots"
_MANIFEST_093424 = _REPO / "analysis" / "2026-09-05 09-34-24" / "vision_manifest.json"
# Retained GT labels; map detector still misses weak close-X. Do not fail.
_UNRESOLVED_MAP_POSITIVES = frozenset(
    {
        (191.0, 191.5),
        (157.5, 157.5),
        (158.0, 158.0),
        (159.5, 159.5),
    }
)

# Labels that score fused ACTIVE_GAMEPLAY state, not HUD-detector evidence.
_STATE_ACTIVE = {"real_active_gameplay", "not_active_gameplay"}
_MATCH_PHASE_LABELS = {
    "intro",
    "opening_countdown",
    "in_match",
    "post_match",
    "results_lobby",
}

# Windows fused in one continuous pass, with the DEATH timestamps each is
# expected to contain. Window A deliberately spans two deaths so the
# "no bounce back to DEAD" check has to be scoped per episode.
_EPISODE_WINDOWS: tuple[tuple[float, float, tuple[float, ...]], ...] = (
    (50.0, 100.0, (58.5, 87.5)),
    (185.0, 215.0, (189.5,)),
)


def _is_positive(reading: object, name: str) -> bool:
    """Return detector positivity for one reading."""
    if name == "map_overlay":
        return bool(getattr(reading, "present", False))
    return bool(getattr(reading, "detected", False))


def _frames_in_window(t0: float, t1: float) -> list[tuple[float, Path]]:
    """Debug JPEGs whose filename timestamp falls in ``[t0, t1]``."""
    matches: list[tuple[float, Path]] = []
    for path in _FRAMES.glob("*.jpg"):
        timestamp = float(path.stem.split("_")[1])
        if t0 <= timestamp <= t1:
            matches.append((timestamp, path))
    matches.sort()
    return matches


def _expect_positive(label: str) -> bool:
    """Viewer ``real_*`` labels are positives; ``not_*`` are negatives."""
    return label.startswith("real_")


def _fresh_detectors(config: AppConfig) -> dict[str, object]:
    """New detector instances so debounce/hold does not leak across windows."""
    detectors = {d.name: d for d in build_detectors(config.vision)}
    detectors["respawn"] = RespawnDetector(
        config.vision.respawn,
        language=config.vision.language,
        ocr_lang=config.vision.tesseract_lang(),
    )
    return detectors


def _fuse_window(
    t0: float,
    t1: float,
    config: AppConfig,
    *,
    warmup: float = 0.0,
) -> list:
    """Run detectors and fuse snapshots covering ``[t0 - warmup, t1]``."""
    detectors = _fresh_detectors(config)
    frame_results: list[VisionFrameResult] = []
    for index, (timestamp, path) in enumerate(_frames_in_window(t0 - warmup, t1)):
        image = cv2.imread(str(path))
        assert image is not None
        detections: list[DetectorResult] = []
        for name, detector in detectors.items():
            reading, confidence = detector.detect(image, timestamp=timestamp)
            if reading is None:
                continue
            detections.append(
                DetectorResult(
                    id=f"{name}:{timestamp:.3f}",
                    detector_name=name,
                    detector_version=f"{name}@gt",
                    confidence=confidence,
                    reading=reading,
                )
            )
        frame_results.append(
            VisionFrameResult(
                frame_id=f"gt:{index}",
                timestamp=timestamp,
                source="cadence",
                source_frame_index=index,
                frame_path=str(path),
                detections=detections,
            )
        )
    assert frame_results, f"no frames in window {t0}-{t1}"
    return fuse_game_state(
        frame_results,
        config.vision.timer,
        config.vision.state_fusion,
        config.vision.death,
        config.vision.splat,
        config.vision.respawn,
        config.vision.active_gameplay,
        config.vision.lifecycle,
        config.vision.map_overlay,
    )


def _recorded_death_times() -> list[float] | None:
    """DEATH events from the analysis manifest, if present."""
    path = _SESSION / "vision_manifest.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        float(event["start_time"])
        for event in payload.get("game_events") or []
        if event.get("event_type") == "death"
    ]


def _score_death_event_window(interval: dict, config: AppConfig) -> None:
    """real_death is the DEATH event, not repeated DeathReading.detected."""
    _ = config
    t0 = float(interval["t0"])
    t1 = float(interval["t1"])
    deaths = _recorded_death_times()
    assert deaths is not None, "vision_manifest.json required to score real_death"
    hit = [stamp for stamp in deaths if t0 - 0.26 <= stamp <= t1 + 0.26]
    assert hit, (
        f"missed DEATH event ({interval['label']}) in {t0}-{t1}, got {deaths}"
    )


def _score_detector_window(
    interval: dict,
    config: AppConfig,
) -> None:
    """Score death/splat/respawn GT against detector evidence.

    ``real_death`` is scored as the DEATH event (once per episode).
    ``not_a_death`` still judges detector false positives.
    """
    t0 = float(interval["t0"])
    t1 = float(interval["t1"])
    name = str(interval["detector"])
    expect = _expect_positive(str(interval["label"]))
    if name == "death" and expect:
        _score_death_event_window(interval, config)
        return
    detectors = _fresh_detectors(config)
    detector = detectors[name]
    warmup = 8.0 if name == "respawn" else 0.0
    frames = _frames_in_window(t0 - warmup, t1)
    assert frames, f"no frames in {name} window {t0}-{t1}"
    hits = 0
    for timestamp, path in frames:
        image = cv2.imread(str(path))
        assert image is not None
        reading, _ = detector.detect(image, timestamp=timestamp)
        if timestamp < t0:
            continue
        if reading is not None and _is_positive(reading, name):
            hits += 1
    if expect:
        assert hits >= 1, f"missed {name} ({interval['label']}) in {t0}-{t1}"
    else:
        assert hits == 0, (
            f"false {name} ({interval['label']}) in {t0}-{t1} ({hits} hits)"
        )


def _lifecycle_in_window(
    snaps: list[GameStateSnapshot], t0: float, t1: float
) -> set[str]:
    """Distinct player_lifecycle values observed in ``[t0, t1]``."""
    return {
        str(snap.player_lifecycle)
        for snap in snaps
        if t0 <= snap.timestamp <= t1
    }


def _is_lifecycle_edge_window(
    snaps: list[GameStateSnapshot], t0: float, t1: float
) -> bool:
    """True when a GT window straddles a lifecycle transition.

    Those windows are scoring edges, not ordinary FP/MISS evidence.
    Point marks (t0 == t1) look one cadence step (±0.5s) so a latch-exit
    frame immediately before ACTIVE_AGAIN is treated as an edge.
    """
    if len(_lifecycle_in_window(snaps, t0, t1)) > 1:
        return True
    if abs(t1 - t0) > 1e-9:
        return False
    return len(_lifecycle_in_window(snaps, t0 - 0.51, t1 + 0.51)) > 1


def _score_active_against_snaps(
    interval: dict, snaps: list[GameStateSnapshot]
) -> None:
    """Score one active_gameplay GT interval against already-fused snaps."""
    t0 = float(interval["t0"])
    t1 = float(interval["t1"])
    if _is_lifecycle_edge_window(snaps, t0, t1):
        return
    expect = _expect_positive(str(interval["label"]))
    hits = sum(
        1
        for snap in snaps
        if t0 <= snap.timestamp <= t1 and snap.active_gameplay is True
    )
    if expect:
        assert hits >= 1, (
            f"missed ACTIVE state ({interval['label']}) in {t0}-{t1}"
        )
    else:
        assert hits == 0, (
            f"false ACTIVE state ({interval['label']}) in {t0}-{t1} ({hits} hits)"
        )


def _score_active_state(interval: dict, config: AppConfig) -> None:
    """Score real/not active_gameplay against fused ACTIVE state."""
    t0 = float(interval["t0"])
    t1 = float(interval["t1"])
    snaps = _fuse_window(t0, t1, config)
    _score_active_against_snaps(interval, snaps)


def _score_match_phase(interval: dict, config: AppConfig) -> None:
    """Score match-phase GT labels against fused match_phase."""
    t0 = float(interval["t0"])
    t1 = float(interval["t1"])
    label = str(interval["label"])
    expected = "post_match" if label == "results_lobby" else label
    snaps = _fuse_window(t0, t1, config)
    phases = {snap.match_phase for snap in snaps if t0 <= snap.timestamp <= t1}
    if expected == "post_match":
        assert phases <= {"post_match", "out_of_match"}, (
            f"expected post-match in {t0}-{t1}, got {phases}"
        )
        return
    assert expected in phases, f"expected {expected} in {t0}-{t1}, got {phases}"


def _assert_no_countdown_bounce(
    snaps: list[GameStateSnapshot],
    death_at: float,
    recovered_at: float,
) -> None:
    """No DEAD between this episode's first COUNTDOWN and its recovery.

    Scoped to one episode on purpose: a later legitimate death in the same
    continuous window is expected and must not fail this.
    """
    window = [s for s in snaps if death_at <= s.timestamp <= recovered_at]
    countdowns = [s for s in window if s.player_lifecycle == "countdown"]
    assert countdowns, f"episode at {death_at} never reached countdown"
    first_countdown = countdowns[0].timestamp
    bounced = [
        s.timestamp
        for s in window
        if s.timestamp > first_countdown and s.player_lifecycle == "dead"
    ]
    assert not bounced, (
        f"episode at {death_at} bounced back to DEAD at {bounced} after "
        f"reaching countdown at {first_countdown}"
    )


def _assert_active_state_matches_gt(
    snaps: list[GameStateSnapshot],
    intervals: list[dict],
    t0: float,
    t1: float,
) -> None:
    """Check fused ACTIVE against active_gameplay GT inside the window."""
    for interval in intervals:
        if str(interval["detector"]) != "active_gameplay":
            continue
        g0 = float(interval["t0"])
        g1 = float(interval["t1"])
        if g0 < t0 or g1 > t1:
            continue
        _score_active_against_snaps(interval, snaps)


@pytest.mark.skipif(not _FRAMES.is_dir(), reason="GT analysis frames are not present")
@pytest.mark.parametrize(("t0", "t1", "expected_deaths"), _EPISODE_WINDOWS)
def test_death_episodes_recover_in_continuous_fusion(
    t0: float,
    t1: float,
    expected_deaths: tuple[float, ...],
) -> None:
    """Each death episode runs DEATH → RESPAWN → ACTIVE_AGAIN in one pass.

    Per-interval scoring cannot catch episode-level regressions: it rebuilds
    the fuser inside every one-second GT window, so the death → respawn →
    active path is never exercised. This fuses the whole window once.
    """
    config = _ja_config()
    payload = json.loads(_GT_PATH.read_text(encoding="utf-8"))
    snaps = _fuse_window(t0, t1, config)
    events = infer_events(snaps, config.vision.events)

    deaths = [e.start_time for e in events if e.event_type is GameEventType.DEATH]
    assert deaths == pytest.approx(list(expected_deaths), abs=1.0), (
        f"expected one DEATH per episode at {expected_deaths}, got {deaths}"
    )

    respawn_gt = [
        (float(i["t0"]), float(i["t1"]))
        for i in payload["intervals"]
        if str(i["label"]) == "real_respawn"
    ]

    for index, death_at in enumerate(deaths):
        end = deaths[index + 1] if index + 1 < len(deaths) else t1 + 1.0
        respawns = [
            e.start_time
            for e in events
            if e.event_type is GameEventType.RESPAWN and death_at < e.start_time < end
        ]
        actives = [
            e.start_time
            for e in events
            if e.event_type is GameEventType.ACTIVE_AGAIN
            and death_at < e.start_time < end
        ]
        assert respawns, f"no RESPAWN after death@{death_at}"
        assert actives, f"no ACTIVE_AGAIN after death@{death_at}"
        assert actives[0] > respawns[0], (
            f"ACTIVE_AGAIN@{actives[0]} must follow RESPAWN@{respawns[0]}"
        )
        _assert_no_countdown_bounce(snaps, death_at, actives[0])

        covering = [g for g in respawn_gt if death_at < g[1] and g[0] < end]
        for g0, g1 in covering:
            assert g0 <= respawns[0] <= g1, (
                f"RESPAWN@{respawns[0]} outside GT interval {g0}-{g1}"
            )

    _assert_active_state_matches_gt(snaps, payload["intervals"], t0, t1)


@pytest.mark.skipif(not _FRAMES.is_dir(), reason="GT analysis frames are not present")
def test_viewer_gt_file_2026_09_06_22_35_09() -> None:
    """Score the exported GT file against debug snapshots."""
    payload = json.loads(_GT_PATH.read_text(encoding="utf-8"))
    config = _ja_config()
    for interval in payload["intervals"]:
        label = str(interval["label"])
        name = str(interval["detector"])
        if name == "match_phase" or label in _MATCH_PHASE_LABELS:
            _score_match_phase(interval, config)
        elif name == "active_gameplay" and label in _STATE_ACTIVE:
            _score_active_state(interval, config)
        else:
            _score_detector_window(interval, config)


def _load_recorded_frames(path: Path) -> list[VisionFrameResult]:
    """Load cadence frame results saved from a vision manifest slice."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        VisionFrameResult.model_validate(frame)
        for frame in payload["frame_results"]
    ]


def _fuse_recorded_frames(
    frames: list[VisionFrameResult], config: AppConfig
) -> list[GameStateSnapshot]:
    """Fuse recorded detections with the current lifecycle config."""
    return fuse_game_state(
        frames,
        config.vision.timer,
        config.vision.state_fusion,
        config.vision.death,
        config.vision.splat,
        config.vision.respawn,
        config.vision.active_gameplay,
        config.vision.lifecycle,
        config.vision.map_overlay,
    )


def _score_detector_from_frames(
    interval: dict, frames: list[VisionFrameResult]
) -> None:
    """Score death/splat/respawn/map GT against recorded readings."""
    t0 = float(interval["t0"])
    t1 = float(interval["t1"])
    name = str(interval["detector"])
    expect = _expect_positive(str(interval["label"]))
    if name == "map_overlay" and (t0, t1) in _UNRESOLVED_MAP_POSITIVES:
        return
    hits = 0
    for frame in frames:
        if not (t0 <= frame.timestamp <= t1):
            continue
        for detection in frame.detections:
            if detection.detector_name != name:
                continue
            if _is_positive(detection.reading, name):
                hits += 1
    if expect:
        assert hits >= 1, f"missed {name} ({interval['label']}) in {t0}-{t1}"
    else:
        assert hits == 0, (
            f"false {name} ({interval['label']}) in {t0}-{t1} ({hits} hits)"
        )


def _snap_at(snaps: list[GameStateSnapshot], timestamp: float) -> GameStateSnapshot:
    """Nearest snapshot to ``timestamp``."""
    return min(snaps, key=lambda snap: abs(snap.timestamp - timestamp))


def test_canonical_091730_episode_126_148_mid_tier_exit() -> None:
    """Death@126.5 recovers with ACTIVE_AGAIN in 137-143; 135.0 stays latched."""
    config = _ja_config()
    frames = _load_recorded_frames(_SLICE_091730)
    snaps = _fuse_recorded_frames(frames, config)
    events = infer_events(snaps, config.vision.events)

    deaths = [e.start_time for e in events if e.event_type is GameEventType.DEATH]
    respawns = [e.start_time for e in events if e.event_type is GameEventType.RESPAWN]
    actives = [
        e.start_time for e in events if e.event_type is GameEventType.ACTIVE_AGAIN
    ]
    assert deaths == pytest.approx([126.5], abs=0.6)
    assert respawns == pytest.approx([133.5], abs=0.6)
    assert _snap_at(snaps, 134.0).player_lifecycle == "awaiting_control"
    hold = _snap_at(snaps, 135.0)
    assert hold.player_lifecycle == "awaiting_control"
    assert hold.active_gameplay is not True
    assert actives, "expected ACTIVE_AGAIN in the 126-148 episode"
    assert 137.0 <= actives[0] <= 143.0, (
        f"ACTIVE_AGAIN@{actives[0]} outside GT window 137-143"
    )
    after = [s for s in snaps if s.timestamp >= actives[0]]
    assert after and all(s.player_lifecycle == "alive" for s in after)

    payload = json.loads(_GT_091730.read_text(encoding="utf-8"))
    # 120-125 is warmup so fusion is already in-match; score the episode only.
    slice_t0, slice_t1 = 126.0, 148.5
    for interval in payload["intervals"]:
        t0 = float(interval["t0"])
        t1 = float(interval["t1"])
        if t0 < slice_t0 or t1 > slice_t1:
            continue
        name = str(interval["detector"])
        label = str(interval["label"])
        if name == "active_gameplay" and label in _STATE_ACTIVE:
            _score_active_against_snaps(interval, snaps)
        elif name == "map_overlay":
            _score_detector_from_frames(interval, frames)


def test_140214_frozen_gt_is_kept_for_next_detector_pass() -> None:
    """Viewer export after freezing detection stays available for later work."""
    payload = json.loads(_GT_140214_FROZEN.read_text(encoding="utf-8"))
    assert payload["video_label"] == "2026-09-07 14-02-14"
    assert payload["exported_at"] == "2026-09-07T23:34:22.723Z"
    labels = {str(item["label"]) for item in payload["intervals"]}
    assert "real_map_overlay" in labels
    assert payload["intervals"]


def test_gt_fixtures_document_real_death_as_event() -> None:
    """Exported GT files state that real_death is the DEATH event."""
    for path in (
        _GT_PATH,
        _GT_091730,
        _GT_093424,
        _GT_140214,
        _GT_140214_FROZEN,
        _GT_155836,
    ):
        payload = json.loads(path.read_text(encoding="utf-8"))
        meaning = (payload.get("label_meanings") or {}).get("real_death", "")
        assert "DEATH event" in meaning
        assert "DeathReading.detected" in meaning


@pytest.mark.skipif(
    not _SNAPS_155836.is_dir(),
    reason="analysis debug_snapshots for 2026-09-10 15-58-36 are not present",
)
def test_map_overlay_calibrated_on_155836_gt() -> None:
    """Layout-gated map_overlay matches the 15-58-36 viewer export.

    Template-only threshold scored 0 TP / 2 FP on this GT; layout gates
    recover real opens and reject the early HUD false positives.
    """
    from splatoon3_ai_coach.vision.map_overlay import MapOverlayDetector

    payload = json.loads(_GT_155836.read_text(encoding="utf-8"))
    assert payload["video_label"] == "2026-09-10 15-58-36"
    config = load_config(default_config_path())
    detector = MapOverlayDetector(config.vision.map_overlay)

    by_ts: dict[float, Path] = {}
    for path in _SNAPS_155836.iterdir():
        if not path.is_file():
            continue
        # 00005910_000098.500.jpg
        parts = path.name.rsplit(".", 2)
        if len(parts) < 3:
            continue
        try:
            stem = path.stem  # 00005910_000098.500
            ts = float(stem.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        by_ts[round(ts, 1)] = path

    tp = fp = tn = fn = 0
    for interval in payload["intervals"]:
        if interval.get("detector") != "map_overlay":
            continue
        t = round(float(interval["t0"]), 1)
        path = by_ts.get(t)
        assert path is not None, f"missing snapshot for GT t={t}"
        image = cv2.imread(str(path))
        assert image is not None
        reading, _conf = detector.detect(image, timestamp=t)
        assert reading is not None
        expect = _expect_positive(str(interval["label"]))
        if expect and reading.present:
            tp += 1
        elif expect and not reading.present:
            fn += 1
        elif not expect and reading.present:
            fp += 1
        else:
            tn += 1
    assert fp == 0, f"map_overlay false positives on GT: fp={fp}"
    assert fn <= 1, f"map_overlay misses on GT: fn={fn} tp={tp}"
    assert tp >= 25


def _score_gt_intervals_from_recorded(
    intervals: list[dict],
    frames: list[VisionFrameResult],
    snaps: list[GameStateSnapshot],
    *,
    slice_t0: float | None = None,
    slice_t1: float | None = None,
) -> None:
    """Score GT intervals against fused snaps and recorded detections."""
    for interval in intervals:
        t0 = float(interval["t0"])
        t1 = float(interval["t1"])
        if slice_t0 is not None and t0 < slice_t0:
            continue
        if slice_t1 is not None and t1 > slice_t1:
            continue
        name = str(interval["detector"])
        label = str(interval["label"])
        if name == "active_gameplay" and label in _STATE_ACTIVE:
            _score_active_against_snaps(interval, snaps)
        elif name == "map_overlay":
            _score_detector_from_frames(interval, frames)


def test_093424_gt_keeps_weak_x_map_marks() -> None:
    """Weak close-X frames stay in GT; they are unresolved detector misses."""
    payload = json.loads(_GT_093424.read_text(encoding="utf-8"))
    keys = {
        (str(iv["detector"]), round(float(iv["t0"]), 1), round(float(iv["t1"]), 1)): str(
            iv["label"]
        )
        for iv in payload["intervals"]
    }
    assert keys[("map_overlay", 157.5, 157.5)] == "real_map_overlay"
    assert keys[("map_overlay", 158.0, 158.0)] == "real_map_overlay"
    assert keys[("map_overlay", 159.5, 159.5)] == "real_map_overlay"
    assert (157.5, 157.5) in _UNRESOLVED_MAP_POSITIVES
    assert (158.0, 158.0) in _UNRESOLVED_MAP_POSITIVES
    assert (159.5, 159.5) in _UNRESOLVED_MAP_POSITIVES


def test_140214_water_wipeout_skips_countdown_plate() -> None:
    """Water death@196.5 has no Respawn-in-N plate; recover then second DEATH.

    GT: not_active 196.5-203, real_respawn 204-206. The wipeout after the
    fall skips the countdown banner, so skip-countdown must emit RESPAWN
    in that window and ACTIVE_AGAIN before the next splat at 216.5.
    """
    config = _ja_config()
    frames = _load_recorded_frames(_SLICE_140214)
    snaps = _fuse_recorded_frames(frames, config)
    events = infer_events(snaps, config.vision.events)

    deaths = [e.start_time for e in events if e.event_type is GameEventType.DEATH]
    respawns = [e.start_time for e in events if e.event_type is GameEventType.RESPAWN]
    actives = [
        e.start_time for e in events if e.event_type is GameEventType.ACTIVE_AGAIN
    ]
    assert deaths[0] == pytest.approx(196.5, abs=0.26)
    assert 204.0 <= respawns[0] <= 206.0, (
        f"RESPAWN@{respawns[0]} outside GT real_respawn 204-206"
    )
    assert actives, "expected ACTIVE_AGAIN after the plate-less water death"
    assert respawns[0] < actives[0] < 216.5
    assert _snap_at(snaps, 203.0).player_lifecycle == "dead"
    assert _snap_at(snaps, 203.0).active_gameplay is not True
    assert deaths[1] == pytest.approx(216.5, abs=0.26), (
        f"second DEATH swallowed; got {deaths}"
    )
    death_ev = next(e for e in events if e.event_type is GameEventType.DEATH)
    respawn_ev = next(e for e in events if e.event_type is GameEventType.RESPAWN)
    active_ev = next(e for e in events if e.event_type is GameEventType.ACTIVE_AGAIN)
    assert death_ev.reason is GameEventReason.ALIVE_TO_DEAD
    assert respawn_ev.reason is GameEventReason.SKIP_COUNTDOWN_CONTROL
    assert active_ev.reason is GameEventReason.AWAITING_CONTROL_TO_ALIVE

    payload = json.loads(_GT_140214.read_text(encoding="utf-8"))
    _assert_active_state_matches_gt(snaps, payload["intervals"], 196.5, 203.0)


@pytest.mark.skipif(
    not _MANIFEST_140214.exists(),
    reason="14-02-14 analysis manifest is not present",
)
def test_140214_plate_path_deaths_still_respawn() -> None:
    """Normal enemy deaths on 14-02-14 still use the Respawn-in-N plate."""
    config = _ja_config()
    frames = _load_recorded_frames(_MANIFEST_140214)
    snaps = _fuse_recorded_frames(frames, config)
    events = infer_events(snaps, config.vision.events)
    plate_deaths = (37.5, 61.5, 79.0, 96.0, 173.0)
    for death_at in plate_deaths:
        after = [
            e
            for e in events
            if e.event_type is GameEventType.RESPAWN
            and death_at < e.start_time < death_at + 12.0
        ]
        assert after, f"no RESPAWN after plate-path death@{death_at}"
        assert after[0].reason is GameEventReason.COUNTDOWN_PLATE_ENDED
        actives = [
            e
            for e in events
            if e.event_type is GameEventType.ACTIVE_AGAIN
            and after[0].start_time < e.start_time < death_at + 16.0
        ]
        assert actives, f"no ACTIVE_AGAIN after plate-path death@{death_at}"


def test_093424_episode_76_90_continuous_fusion() -> None:
    """DEATH@76 → RESPAWN@83 → ACTIVE_AGAIN in [86.5, 87.5]; 86.5 may stay latched."""
    config = _ja_config()
    frames = _load_recorded_frames(_SLICE_093424)
    snaps = _fuse_recorded_frames(frames, config)
    events = infer_events(snaps, config.vision.events)

    deaths = [e.start_time for e in events if e.event_type is GameEventType.DEATH]
    respawns = [e.start_time for e in events if e.event_type is GameEventType.RESPAWN]
    actives = [
        e.start_time for e in events if e.event_type is GameEventType.ACTIVE_AGAIN
    ]
    assert deaths == pytest.approx([76.0], abs=0.6)
    assert respawns == pytest.approx([83.0], abs=0.6)
    hold = _snap_at(snaps, 86.5)
    assert hold.player_lifecycle == "awaiting_control"
    assert hold.active_gameplay is not True
    assert actives, "expected ACTIVE_AGAIN in the 76-90 episode"
    assert 86.5 <= actives[0] <= 87.5, (
        f"ACTIVE_AGAIN@{actives[0]} outside latch-exit window [86.5, 87.5]"
    )
    after = [s for s in snaps if s.timestamp >= actives[0]]
    assert after and all(s.player_lifecycle == "alive" for s in after)

    payload = json.loads(_GT_093424.read_text(encoding="utf-8"))
    _score_gt_intervals_from_recorded(
        payload["intervals"], frames, snaps, slice_t0=76.0, slice_t1=90.5
    )


@pytest.mark.skipif(
    not _MANIFEST_093424.exists(),
    reason="09-34-24 analysis manifest is not present",
)
def test_viewer_gt_file_2026_09_05_09_34_24() -> None:
    """Score 09-34-24 GT against recorded frames and fused snapshots."""
    payload = json.loads(_GT_093424.read_text(encoding="utf-8"))
    config = _ja_config()
    frames = _load_recorded_frames(_MANIFEST_093424)
    snaps = _fuse_recorded_frames(frames, config)
    _score_gt_intervals_from_recorded(payload["intervals"], frames, snaps)


def test_canonical_091730_gt_has_superseding_map_labels() -> None:
    """Canonical file keeps later map corrections, not stale nearby windows."""
    payload = json.loads(_GT_091730.read_text(encoding="utf-8"))
    keys = {
        (str(iv["detector"]), round(float(iv["t0"]), 1), round(float(iv["t1"]), 1)): str(
            iv["label"]
        )
        for iv in payload["intervals"]
    }
    assert keys[("map_overlay", 130.5, 131.0)] == "not_a_map_overlay"
    assert keys[("map_overlay", 161.0, 161.5)] == "not_a_map_overlay"
    assert keys[("map_overlay", 89.0, 89.5)] == "real_map_overlay"
    assert keys[("map_overlay", 191.0, 191.5)] == "real_map_overlay"
    assert ("map_overlay", 131.0, 131.5) not in keys
    assert ("map_overlay", 160.5, 161.0) not in keys
    assert ("map_overlay", 234.5, 235.0) not in keys


def test_lifecycle_edge_windows_are_not_scored_as_fp() -> None:
    """A 0.5s window that crosses latch-exit / death is an edge, not a miss."""
    snaps = [
        GameStateSnapshot(
            timestamp=76.0,
            player_alive=False,
            player_lifecycle="awaiting_control",
            active_gameplay=False,
            match_phase="in_match",
        ),
        GameStateSnapshot(
            timestamp=76.5,
            player_alive=True,
            player_lifecycle="alive",
            active_gameplay=True,
            match_phase="in_match",
        ),
        GameStateSnapshot(
            timestamp=204.5,
            player_alive=True,
            player_lifecycle="alive",
            active_gameplay=True,
            match_phase="in_match",
        ),
        GameStateSnapshot(
            timestamp=205.0,
            player_alive=False,
            player_lifecycle="dead",
            active_gameplay=False,
            match_phase="in_match",
        ),
    ]
    assert _is_lifecycle_edge_window(snaps, 76.0, 76.5)
    assert _is_lifecycle_edge_window(snaps, 204.5, 205.0)
    latch_exit = [
        GameStateSnapshot(
            timestamp=86.0,
            player_alive=False,
            player_lifecycle="awaiting_control",
            active_gameplay=False,
            match_phase="in_match",
        ),
        GameStateSnapshot(
            timestamp=86.5,
            player_alive=False,
            player_lifecycle="awaiting_control",
            active_gameplay=False,
            match_phase="in_match",
        ),
        GameStateSnapshot(
            timestamp=87.0,
            player_alive=True,
            player_lifecycle="alive",
            active_gameplay=True,
            match_phase="in_match",
        ),
    ]
    assert _is_lifecycle_edge_window(latch_exit, 86.5, 86.5)
    _score_active_against_snaps(
        {
            "t0": 76.0,
            "t1": 76.5,
            "detector": "active_gameplay",
            "label": "not_active_gameplay",
        },
        snaps,
    )
    _score_active_against_snaps(
        {
            "t0": 204.5,
            "t1": 205.0,
            "detector": "active_gameplay",
            "label": "not_active_gameplay",
        },
        snaps,
    )
    _score_active_against_snaps(
        {
            "t0": 86.5,
            "t1": 86.5,
            "detector": "active_gameplay",
            "label": "real_active_gameplay",
        },
        latch_exit,
    )
