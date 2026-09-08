"""Tests for local-kill splat detection, fusion, and SPLAT events."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config.models import (
    DeathDetectorConfig,
    EventFusionConfig,
    SplatDetectorConfig,
    StateFusionConfig,
    TimerDetectorConfig,
    VisionLanguage,
)
from splatoon3_ai_coach.vision.death import DeathDetector
from splatoon3_ai_coach.vision.events import infer_events
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    GameEventType,
    GameStateSnapshot,
    SplatBannerInstance,
    SplatReading,
    TimerReading,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.splat import SplatDetector, fingerprint_distance
from splatoon3_ai_coach.vision.splat_episodes import SplatEpisodeFuser
from splatoon3_ai_coach.vision.state import fuse_game_state

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE_DIR = _REPO_ROOT / "calibration" / "templates" / "splat"
_ASSETS = Path(
    "/Users/kenjikahara/.cursor/projects/Users-kenjikahara-splatoon3-ai-coach/assets"
)
_ANALYSIS_DIR = _REPO_ROOT / "analysis" / "2026-09-06 14-54-11"
# Viewer-exported GT for splat on that session (death interval omitted).
_GT_SPLAT_WINDOWS: list[tuple[float, float, bool]] = [
    (8.0, 9.0, False),
    (10.0, 11.0, False),
    (16.5, 17.5, False),
    (38.0, 39.0, False),
    (48.0, 49.0, True),
    (54.0, 55.0, False),
    (56.0, 57.0, False),
    (100.5, 101.5, True),
]


def _timer_config() -> TimerDetectorConfig:
    return TimerDetectorConfig(
        roi=(0.0, 0.0, 1.0, 1.0),
        template_dir=".",
        min_usable_confidence=0.5,
    )


def _splat_config(**overrides: object) -> SplatDetectorConfig:
    data: dict[str, object] = {
        "template_dir": _TEMPLATE_DIR,
        "debounce_seconds": 0.0,
    }
    data.update(overrides)
    return SplatDetectorConfig(**data)  # type: ignore[arg-type]


def _banner(fingerprint: str, slot_y: float = 0.5) -> SplatBannerInstance:
    """One this-frame banner instance for episode tests."""
    return SplatBannerInstance(fingerprint=fingerprint, slot_y=slot_y)


def _splat_events(
    snapshots: list[GameStateSnapshot],
    **overrides: object,
) -> list[float]:
    """SPLAT start times from ``infer_events``."""
    config = EventFusionConfig(**overrides)  # type: ignore[arg-type]
    events = infer_events(snapshots, config)
    return [e.start_time for e in events if e.event_type is GameEventType.SPLAT]


def _blank_1080() -> np.ndarray:
    return np.full((1080, 1920, 3), 180, dtype=np.uint8)


def _load_snap_1080(name: str) -> np.ndarray:
    path = _ASSETS / name
    if not path.exists():
        pytest.skip(f"missing evidence snap: {path}")
    image = cv2.imread(str(path))
    assert image is not None
    return cv2.resize(image, (1920, 1080), interpolation=cv2.INTER_AREA)


def _result(
    timestamp: float,
    index: int,
    *,
    timer: tuple[str, float, float] | None = None,
    splat: SplatReading | None = None,
    splat_confidence: float = 0.9,
) -> VisionFrameResult:
    detections: list[DetectorResult] = []
    if timer is not None:
        display, seconds, confidence = timer
        detections.append(
            DetectorResult(
                id=f"timer:{index}",
                detector_name="timer",
                detector_version="timer@test",
                confidence=confidence,
                reading=TimerReading(display=display, seconds_remaining=seconds),
            )
        )
    if splat is not None:
        detections.append(
            DetectorResult(
                id=f"splat:{index}",
                detector_name="splat",
                detector_version="splat@test",
                confidence=splat_confidence,
                reading=splat,
            )
        )
    return VisionFrameResult(
        frame_id=f"frame:{index}",
        timestamp=timestamp,
        source="cadence",
        source_frame_index=index,
        detections=detections,
    )


def test_splat_detector_loads_language_template_dir() -> None:
    """Each language pack lives under template_dir/{language}/."""
    detector = SplatDetector(_splat_config(), language=VisionLanguage.EN)
    en_names = {
        path.name
        for path in (_TEMPLATE_DIR / "en").glob("*")
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"}
    }
    assert en_names == {"en_skull_01.png", "en_splatted_01.png"}
    assert detector._template_dir == _TEMPLATE_DIR / "en"
    assert len(detector._icon_templates) == 1
    assert len(detector._text_templates) == 1

    ja = SplatDetector(_splat_config(), language=VisionLanguage.JA)
    assert ja._template_dir == _TEMPLATE_DIR / "ja"
    assert len(ja._icon_templates) == 1
    assert len(ja._text_templates) == 1


def test_splat_detector_loads_skull_subdirectory(tmp_path: Path) -> None:
    """Language packs may keep icons under template_dir/{lang}/skull/."""
    lang_dir = tmp_path / "en"
    skull = lang_dir / "skull"
    skull.mkdir(parents=True)
    src = _TEMPLATE_DIR / "en" / "en_skull_01.png"
    (skull / src.name).write_bytes(src.read_bytes())
    detector = SplatDetector(
        _splat_config(template_dir=tmp_path),
        language=VisionLanguage.EN,
    )
    assert len(detector._icon_templates) == 1
    assert len(detector._text_templates) == 0


def test_splat_detector_confirms_user_snap() -> None:
    detector = SplatDetector(_splat_config())
    image = _load_snap_1080(
        "vlcsnap-2026-09-03-21h18m56s379-b44b62b5-0f8a-487c-9f77-1d0b94fc199a.jpg"
    )
    reading, score = detector.detect(image, timestamp=1.0)
    assert reading is not None
    assert reading.kind == "splat"
    assert reading.detected
    assert (
        reading.skull_score >= 0.80 or reading.text_score >= 0.70
    )
    assert reading.victim_name is None
    assert reading.victim_name_confidence == 0.0
    assert score >= 0.5


def test_blank_frame_is_not_a_splat() -> None:
    detector = SplatDetector(_splat_config())
    reading, _ = detector.detect(_blank_1080(), timestamp=0.0)
    assert reading is not None
    assert not reading.detected


def test_white_square_in_banner_is_not_a_splat() -> None:
    """A bright blob in the ROI must not trip without matching kill-banner crops."""
    detector = SplatDetector(_splat_config())
    image = _blank_1080()
    image[918:1058, 672:1248] = (20, 20, 20)
    image[980:1012, 740:772] = (255, 255, 255)
    reading, _ = detector.detect(image, timestamp=1.0)
    assert reading is not None
    assert not reading.detected


def _cadence_frames_in_window(
    folder: Path, t0: float, t1: float
) -> list[tuple[float, Path]]:
    """Cadence JPEGs whose filename timestamp falls in ``[t0, t1]``."""
    matches: list[tuple[float, Path]] = []
    for path in folder.glob("*.jpg"):
        timestamp = float(path.stem.split("_")[1])
        if t0 <= timestamp <= t1:
            matches.append((timestamp, path))
    matches.sort()
    return matches


@pytest.mark.skipif(
    not (_ANALYSIS_DIR / "cadence_frames").is_dir(),
    reason="analysis cadence frames for GT session are not present",
)
def test_ground_truth_splat_windows() -> None:
    """Real local-kill banners fire; kill-feed skulls in the same ROI do not."""
    detector = SplatDetector(_splat_config(debounce_seconds=0.01))
    frames_dir = _ANALYSIS_DIR / "cadence_frames"
    for t0, t1, expect_splat in _GT_SPLAT_WINDOWS:
        frames = _cadence_frames_in_window(frames_dir, t0, t1)
        if not frames and (_ANALYSIS_DIR / "debug_snapshots").is_dir():
            frames = _cadence_frames_in_window(
                _ANALYSIS_DIR / "debug_snapshots", t0, t1
            )
        assert frames, f"no frames in GT window {t0}-{t1}"
        hits = 0
        for _, path in frames:
            image = cv2.imread(str(path))
            assert image is not None
            reading, _ = detector._observe(image)
            if reading.detected:
                hits += 1
        if expect_splat:
            assert hits >= 1, f"missed real splat in {t0}-{t1}"
        else:
            assert hits == 0, f"false splat in {t0}-{t1} ({hits} hits)"


def test_splat_detector_debounces_repeat_positives() -> None:
    detector = SplatDetector(_splat_config(debounce_seconds=1.75))
    image = _load_snap_1080(
        "vlcsnap-2026-09-03-21h18m56s379-b44b62b5-0f8a-487c-9f77-1d0b94fc199a.jpg"
    )
    first, _ = detector.detect(image, timestamp=10.0)
    suppressed, _ = detector.detect(image, timestamp=10.5)
    later, _ = detector.detect(image, timestamp=12.0)
    assert first is not None and first.detected
    assert suppressed is None
    assert later is not None and later.detected


def test_splat_detector_reports_every_frame_when_debounce_is_zero() -> None:
    """Incident policy is not detector cooldown; default debounce is 0."""
    detector = SplatDetector(_splat_config())
    image = _load_snap_1080(
        "vlcsnap-2026-09-03-21h18m56s379-b44b62b5-0f8a-487c-9f77-1d0b94fc199a.jpg"
    )
    first, _ = detector.detect(image, timestamp=10.0)
    again, _ = detector.detect(image, timestamp=10.5)
    assert first is not None and first.detected
    assert again is not None and again.detected
    assert first.instances
    assert again.instances
    assert first.instances[0].fingerprint == again.instances[0].fingerprint


def test_fusion_sets_transient_player_splatted() -> None:
    results = [
        _result(0.0, 0, splat=SplatReading(detected=False)),
        _result(
            1.0,
            1,
            splat=SplatReading(
                detected=True,
                skull_score=0.9,
                adjacent_color_score=0.5,
                instances=[_banner("aa" * 8)],
            ),
        ),
        _result(1.5, 2, splat=SplatReading(detected=False)),
        _result(4.0, 3, splat=SplatReading(detected=False)),
    ]
    snapshots = fuse_game_state(
        results,
        _timer_config(),
        StateFusionConfig(max_hold_duration=2.0),
        DeathDetectorConfig(),
        _splat_config(),
    )
    assert snapshots[0].player_splatted is None
    assert snapshots[1].player_splatted is True
    assert snapshots[1].splat_instances == [_banner("aa" * 8)]
    assert snapshots[2].player_splatted is True  # hold
    assert snapshots[3].player_splatted is None  # expired
    assert "splat:1" in snapshots[1].evidence_ids
    assert snapshots[2].splat_instances == []  # hold is not episode evidence


def test_fusion_does_not_touch_player_alive() -> None:
    results = [
        _result(
            1.0,
            1,
            splat=SplatReading(
                detected=True,
                skull_score=0.9,
                adjacent_color_score=0.5,
            ),
        ),
    ]
    snapshots = fuse_game_state(
        results,
        _timer_config(),
        StateFusionConfig(),
        DeathDetectorConfig(),
        _splat_config(),
    )
    assert snapshots[0].player_splatted is True
    assert snapshots[0].player_alive is None


def test_same_fingerprint_persists_as_one_splat() -> None:
    fp = "aaaaaaaaaaaaaaaa"
    snaps = [
        GameStateSnapshot(timestamp=56.5, splat_instances=[_banner(fp)]),
        GameStateSnapshot(timestamp=59.5, splat_instances=[_banner(fp)]),
        GameStateSnapshot(timestamp=62.5, splat_instances=[_banner(fp)]),
        GameStateSnapshot(timestamp=63.5),
        GameStateSnapshot(timestamp=64.0),
        GameStateSnapshot(timestamp=64.5),
    ]
    assert _splat_events(snaps) == pytest.approx([56.5])


def test_slot_shift_same_fingerprint_is_one_splat() -> None:
    fp = "aaaaaaaaaaaaaaaa"
    snaps = [
        GameStateSnapshot(timestamp=10.0, splat_instances=[_banner(fp, 0.8)]),
        GameStateSnapshot(timestamp=10.5, splat_instances=[_banner(fp, 0.5)]),
        GameStateSnapshot(timestamp=11.0, splat_instances=[_banner(fp, 0.2)]),
    ]
    assert _splat_events(snaps) == pytest.approx([10.0])


def test_identical_fingerprints_at_two_slots_are_two_splats() -> None:
    fp = "aaaaaaaaaaaaaaaa"
    snaps = [
        GameStateSnapshot(
            timestamp=10.0,
            splat_instances=[_banner(fp, 0.3), _banner(fp, 0.7)],
        ),
    ]
    assert _splat_events(snaps) == pytest.approx([10.0, 10.0])


def test_three_repeated_observations_one_splat_event() -> None:
    """Repeated detector hits for one banner → exactly one SPLAT GameEvent."""
    snaps = [
        GameStateSnapshot(timestamp=10.0, splat_instances=[_banner("aa" * 8, 0.8)]),
        GameStateSnapshot(timestamp=10.5, splat_instances=[_banner("aa" * 8, 0.8)]),
        GameStateSnapshot(timestamp=11.0, splat_instances=[_banner("aa" * 8, 0.8)]),
    ]
    assert _splat_events(snaps) == pytest.approx([10.0])


def test_two_distinct_splats_close_together_via_stack() -> None:
    """Two genuine stacked banners stay two SPLAT GameEvents."""
    snaps = [
        GameStateSnapshot(
            timestamp=10.0,
            splat_instances=[_banner("aa" * 8, 0.3), _banner("bb" * 8, 0.75)],
        ),
        GameStateSnapshot(
            timestamp=10.5,
            splat_instances=[_banner("aa" * 8, 0.3), _banner("bb" * 8, 0.75)],
        ),
    ]
    assert _splat_events(snaps) == pytest.approx([10.0, 10.0])


def test_persistent_banner_fingerprint_drift_is_one_splat() -> None:
    """Same occupied slot with drifting aHash stays one episode (not a debounce)."""
    # Distances well above splat_fingerprint_max_distance=10.
    snaps = [
        GameStateSnapshot(
            timestamp=10.0, splat_instances=[_banner("80c0dededc9c0000", 0.8)]
        ),
        GameStateSnapshot(
            timestamp=10.5, splat_instances=[_banner("9dddd1c1c1818183", 0.8)]
        ),
        GameStateSnapshot(
            timestamp=11.0, splat_instances=[_banner("9cdcd8dcdc9c8081", 0.8)]
        ),
        GameStateSnapshot(
            timestamp=11.5, splat_instances=[_banner("80c0dcdcdc9c8080", 0.8)]
        ),
    ]
    assert fingerprint_distance("80c0dededc9c0000", "9dddd1c1c1818183") > 10
    assert _splat_events(snaps) == pytest.approx([10.0])


def test_banner_absent_then_new_splat_is_two_events() -> None:
    """Disappearance (absent streak) then a new row opens a second episode."""
    snaps = [
        GameStateSnapshot(timestamp=10.0, splat_instances=[_banner("aa" * 8, 0.8)]),
        GameStateSnapshot(timestamp=10.5),
        GameStateSnapshot(timestamp=11.0),
        GameStateSnapshot(timestamp=11.5, splat_instances=[_banner("bb" * 8, 0.8)]),
    ]
    assert _splat_events(snaps) == pytest.approx([10.0, 11.5])


def test_bolero_2985_reopen_is_same_episode() -> None:
    """2026-07-07 23-14-50: Bolero banner 293.5→298.5 must not re-open at 298.5.

    Frame 298.5 still shows ``Splatted Bolero!``; the bogus GameEvent was fusion
    treating aHash drift (d=25) as a new instance. Detector evidence remains.
    """
    # Fingerprints copied from analysis/2026-07-07 23-14-50 vision_manifest.
    sequence = [
        (293.5, "787878787070f0e0"),
        (294.0, "01c8dc1c9c9c0001"),
        (294.5, "00c8dc9c9c9c0001"),
        (295.0, "00585c5cdc1c0000"),
        (295.5, "00d8dc5cdc1c0000"),
        (296.0, "00585c5cdc1d0000"),
        (296.5, "00585c5cdc9c0000"),
        (297.0, "80c8dc9c9c9c8080"),
        (297.5, "80c8dc9c9c9c8080"),
        (298.0, "80c8dc9c9c9c0000"),
        (298.5, "03031f1f1f1f0303"),
    ]
    snaps = [
        GameStateSnapshot(
            timestamp=ts, splat_instances=[_banner(fp, 0.799)]
        )
        for ts, fp in sequence
    ]
    assert fingerprint_distance("80c8dc9c9c9c0000", "03031f1f1f1f0303") > 10
    assert _splat_events(snaps) == pytest.approx([293.5])


def test_absent_streak_then_new_fingerprint_is_second_splat() -> None:
    snaps = [
        GameStateSnapshot(timestamp=56.5, splat_instances=[_banner("aa" * 8)]),
        GameStateSnapshot(timestamp=63.5),
        GameStateSnapshot(timestamp=64.0),
        GameStateSnapshot(timestamp=80.0, splat_instances=[_banner("bb" * 8)]),
    ]
    assert _splat_events(snaps) == pytest.approx([56.5, 80.0])


def test_drifting_fingerprint_same_slot_is_one_splat_not_two() -> None:
    """Replaces the old 'any new fingerprint = new splat' half-second rule."""
    snaps = [
        GameStateSnapshot(timestamp=10.0, splat_instances=[_banner("aa" * 8, 0.5)]),
        GameStateSnapshot(timestamp=10.5, splat_instances=[_banner("bb" * 8, 0.5)]),
    ]
    assert _splat_events(snaps) == pytest.approx([10.0])


def test_three_distinct_fingerprints_same_frame_are_three_splats() -> None:
    snaps = [
        GameStateSnapshot(
            timestamp=20.0,
            splat_instances=[
                _banner("aa" * 8, 0.2),
                _banner("bb" * 8, 0.5),
                _banner("cc" * 8, 0.8),
            ],
        ),
    ]
    assert _splat_events(snaps) == pytest.approx([20.0, 20.0, 20.0])


def test_player_splatted_hold_does_not_keep_episode_open() -> None:
    """Held player_splatted without instances is not banner evidence."""
    fp = "aaaaaaaaaaaaaaaa"
    snaps = [
        GameStateSnapshot(
            timestamp=1.0,
            player_splatted=True,
            splat_instances=[_banner(fp)],
        ),
        GameStateSnapshot(timestamp=1.5, player_splatted=True),
        GameStateSnapshot(timestamp=2.0, player_splatted=True),
        GameStateSnapshot(
            timestamp=2.5,
            player_splatted=True,
            splat_instances=[_banner(fp)],
        ),
    ]
    assert _splat_events(snaps) == pytest.approx([1.0, 2.5])


def test_fingerprint_distance_is_hamming() -> None:
    assert fingerprint_distance("0" * 16, "0" * 16) == 0
    assert fingerprint_distance("0" * 16, "8" + "0" * 15) == 1


def test_episode_fuser_prefers_nearby_slot_for_twins() -> None:
    fuser = SplatEpisodeFuser(EventFusionConfig())
    first = fuser.step([_banner("aa" * 8, 0.2), _banner("aa" * 8, 0.8)])
    assert len(first.opened) == 2
    moved = fuser.step([_banner("aa" * 8, 0.25), _banner("aa" * 8, 0.82)])
    assert moved.opened == []


def test_slot_continuity_updates_fingerprint() -> None:
    """Occupied-slot continuation refreshes the open episode fingerprint."""
    fuser = SplatEpisodeFuser(EventFusionConfig())
    opened = fuser.step([_banner("aa" * 8, 0.8)])
    assert len(opened.opened) == 1
    cont = fuser.step([_banner("ff" * 8, 0.81)])
    assert cont.opened == []
    again = fuser.step([_banner("ff" * 8, 0.80)])
    assert again.opened == []


def test_infer_events_emits_splat_per_new_instance() -> None:
    snapshots = [
        GameStateSnapshot(timestamp=1.0),
        GameStateSnapshot(
            timestamp=2.0,
            splat_instances=[_banner("aa" * 8)],
            evidence_ids=["splat:1"],
        ),
        GameStateSnapshot(timestamp=2.5, splat_instances=[_banner("aa" * 8)]),
        GameStateSnapshot(timestamp=5.0),
        GameStateSnapshot(timestamp=5.5),
        GameStateSnapshot(
            timestamp=6.0,
            splat_instances=[_banner("bb" * 8)],
            evidence_ids=["splat:2"],
        ),
    ]
    assert _splat_events(snapshots) == pytest.approx([2.0, 6.0])


def test_triple_banner_snap_finds_multiple_instances() -> None:
    """Stacked local-kill banners are distinct instances, not one blob."""
    detector = SplatDetector(_splat_config(), language=VisionLanguage.JA)
    image = _load_snap_1080(
        "vlcsnap-2026-09-07-14h17m05s252-2fbd04bf-a810-4c25-aba3-191def4c739d.jpg"
    )
    reading, _ = detector.detect(image, timestamp=1.0)
    assert reading is not None and reading.detected
    assert len(reading.instances) == 3
    fps = {item.fingerprint for item in reading.instances}
    assert len(fps) == 3


def test_death_detector_unchanged_on_blank() -> None:
    """Splat work must not alter DeathDetector behavior on a blank frame."""
    reading, _ = DeathDetector(DeathDetectorConfig()).detect(_blank_1080(), timestamp=0.0)
    assert reading is not None
    assert not reading.detected
