"""Tests for the read-only vision manifest viewer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from vision_manifest_viewer.cli import build_parser, main as viewer_main
from vision_manifest_viewer.html import render_html, write_html
from vision_manifest_viewer.loader import load_manifest_view
from vision_manifest_viewer.model import ObservationView
from vision_manifest_viewer.server import (
    REVIEW_VIDEO_PATH,
    resolve_review_video,
    review_video_content_type,
)
from vision_manifest_viewer.timeline import (
    build_death_episodes,
    build_lifecycle_segments,
)


def _write_manifest(tmp_path: Path) -> Path:
    """Minimal vision manifest covering a death → countdown → respawn episode."""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    # Tiny valid JPEG so image_relpath resolves.
    jpg = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
        b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
        b"\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x14"
        b"\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xaa\xff\xd9"
    )
    (frames_dir / "death.jpg").write_bytes(jpg)
    (frames_dir / "cd.jpg").write_bytes(jpg)

    manifest = {
        "schema_version": 1,
        "video_identity": "abc123",
        "extraction_manifest_path": "frames",
        "analysis": {
            "analysis_id": "a",
            "package_version": "0.2.0",
            "pipeline_version": "3.0.0",
            "detector_versions": {},
            "extraction_manifest_sha256": "x",
            "vision_config_sha256": "y",
            "video_identity": "abc123",
        },
        "frame_results": [
            {
                "frame_id": "f0",
                "timestamp": 10.0,
                "source": "cadence",
                "source_frame_index": 100,
                "frame_path": "frames/death.jpg",
                "detections": [
                    {
                        "id": "d0",
                        "detector_name": "death",
                        "detector_version": "death@test",
                        "confidence": 0.9,
                        "reading": {
                            "kind": "death",
                            "detected": True,
                            "ouch_detected": True,
                            "ouch_white_score": 0.5,
                            "banner_detected": True,
                            "banner_dark_score": 0.8,
                        },
                    },
                    {
                        "id": "s0",
                        "detector_name": "splat",
                        "detector_version": "splat@test",
                        "confidence": 0.88,
                        "reading": {
                            "kind": "splat",
                            "detected": True,
                            "skull_score": 0.88,
                            "text_score": 0.7,
                            "adjacent_color_score": 0.6,
                        },
                    },
                ],
            },
            {
                "frame_id": "f1",
                "timestamp": 11.0,
                "source": "cadence",
                "source_frame_index": 110,
                "frame_path": "frames/cd.jpg",
                "detections": [
                    {
                        "id": "c1",
                        "detector_name": "respawn",
                        "detector_version": "respawn@test",
                        "confidence": 0.91,
                        "reading": {
                            "kind": "respawn",
                            "detected": True,
                            "confidence": 0.91,
                            "template_score": 0.91,
                            "countdown_value": 4,
                            "evidence_type": "template",
                            "presence_score": 0.91,
                            "dark_frac": 0.42,
                            "bright_frac": 0.18,
                            "p95": 0.9,
                            "yellow_frac": 0.11,
                            "mean_sat": 0.64,
                            "mean_val": 0.71,
                        },
                    }
                ],
            },
            {
                "frame_id": "f2",
                "timestamp": 12.0,
                "source": "cadence",
                "source_frame_index": 120,
                "frame_path": "frames/cd.jpg",
                "detections": [
                    {
                        "id": "c2",
                        "detector_name": "respawn",
                        "detector_version": "respawn@test",
                        "confidence": 0.7,
                        "reading": {
                            "kind": "respawn",
                            "detected": False,
                            "confidence": 0.7,
                            "template_score": 0.1,
                            "presence_score": 0.1,
                            "dark_frac": 0.0,
                            "bright_frac": 0.0,
                            "p95": 0.2,
                            "yellow_frac": 0.0,
                            "mean_sat": 0.0,
                            "mean_val": 0.3,
                        },
                    }
                ],
            },
        ],
        "state_snapshots": [
            {
                "timestamp": 10.0,
                "player_alive": False,
                "player_lifecycle": "dead",
                "countdown_confirmed_this_death_episode": False,
                "quality": "observed",
            },
            {
                "timestamp": 11.0,
                "player_alive": False,
                "player_lifecycle": "countdown",
                "countdown_present": True,
                "countdown_confirmed_this_death_episode": True,
                "quality": "observed",
            },
            {
                "timestamp": 12.0,
                "player_alive": False,
                "player_lifecycle": "respawned",
                "countdown_present": False,
                "countdown_confirmed_this_death_episode": True,
                "quality": "observed",
            },
            {
                "timestamp": 13.0,
                "player_alive": True,
                "player_lifecycle": "alive",
                "countdown_confirmed_this_death_episode": False,
                "quality": "observed",
            },
        ],
        "game_events": [
            {"start_time": 10.0, "event_type": "death", "confidence": 1.0},
            {"start_time": 10.0, "event_type": "splat", "confidence": 1.0},
            {"start_time": 12.0, "event_type": "respawn", "confidence": 1.0},
            {"start_time": 13.0, "event_type": "active_again", "confidence": 1.0},
        ],
    }
    path = tmp_path / "vision_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_load_manifest_view_builds_episodes_and_rois(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path)
    view = load_manifest_view(path, config_path=Path("configs/default.yaml"))
    assert view.summary.frame_count == 3
    assert view.summary.death_detections == 1
    assert view.summary.countdown_observations == 2
    assert len(view.episodes) == 1
    episode = view.episodes[0]
    assert episode.has_countdown
    assert episode.has_respawn
    assert episode.has_active_again
    assert episode.completeness_score == 4
    assert any(o.image_relpath == "frames/cd.jpg" for o in view.observations)
    assert "death" in view.default_rois
    assert view.default_rois["death"].x1 == pytest.approx(0.0104167, abs=1e-6)
    assert view.default_rois["death"].y1 == pytest.approx(0.8148148, abs=1e-6)
    assert "respawn" in view.default_rois
    assert view.default_rois["respawn"].x1 == pytest.approx(0.840)
    assert view.default_rois["respawn"].y1 == pytest.approx(0.825)
    assert "map_overlay" in view.default_rois
    assert view.default_rois["map_overlay"].x1 == pytest.approx(0.0208)
    assert view.default_rois["map_overlay"].y1 == pytest.approx(0.0324)
    assert len(view.transitions) >= 1
    assert any(
        t.event_type == "respawn" or t.to_phase == "respawned"
        for t in view.transitions
    )
    assert len(view.detector_lanes) == 4
    assert {lane.detector for lane in view.detector_lanes} == {
        "death",
        "respawn",
        "active_gameplay",
        "special_gauge",
    }
    assert episode.latch_points
    assert view.death_diagnostics
    assert any(d.decision == "confirmed" for d in view.death_diagnostics)


def test_respawn_why_panel_requires_latch(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path)
    view = load_manifest_view(path)
    respawn = next(t for t in view.transitions if t.to_phase == "respawned")
    assert respawn.player_alive is False
    assert any("countdown_confirmed" in c.label for c in respawn.checks)
    assert "NOT yet" in respawn.meaning


def test_lifecycle_segments_collapse_phases(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    segments = build_lifecycle_segments(raw["state_snapshots"])
    phases = [s.phase for s in segments]
    assert phases == ["dead", "countdown", "respawned", "alive"]


def test_markers_include_events_and_positive_obs(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path)
    view = load_manifest_view(path)
    labels = {m.label for m in view.markers}
    assert "DEATH" in labels
    assert "SPLAT" in labels
    assert "RESPAWN" in labels
    assert "ACTIVE AGAIN" in labels
    assert "RESPAWN UI PRESENT" in labels


def test_lifecycle_marks_include_splat_events(tmp_path: Path) -> None:
    """SPLAT events appear on the lifecycle strip, not only as gallery chips."""
    path = _write_manifest(tmp_path)
    view = load_manifest_view(path)
    splat_marks = [m for m in view.lifecycle_marks if m.event_label == "SPLAT"]
    assert splat_marks
    assert splat_marks[0].phase_label == "splat"
    assert splat_marks[0].timestamp == pytest.approx(10.0)
    assert any(t.event_type == "splat" for t in view.transitions)
    html = render_html(view)
    assert "splat-badge" in html
    assert "renderSplatLane" in html


def test_episode_warns_when_death_without_countdown() -> None:
    events = [{"start_time": 5.0, "event_type": "death", "confidence": 1.0}]
    observations = [
        ObservationView(
            id="d",
            timestamp=5.0,
            detector="death",
            category="death",
            positive=True,
            confidence=0.9,
        )
    ]
    episodes = build_death_episodes(events, observations, [])
    assert len(episodes) == 1
    assert episodes[0].warning is not None


def test_render_html_embeds_payload_and_write(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path)
    view = load_manifest_view(path)
    html = render_html(view)
    assert "VISION MANIFEST VIEWER" in html
    assert "Scenarios / Coaching Evidence" in html
    assert "function renderScenarios(" in html
    assert "Video time:" in html
    assert "Scenario Review" in html
    assert 'id="scenario-review" hidden' in html
    assert ">Previous scenario<" in html
    assert ">Next scenario<" in html
    assert "/review-video" in html
    assert "REVIEW_PREROLL_DEFAULT" not in html
    assert "video.currentTime = at" in html
    assert "No scenario_contexts.json" in html
    assert "Death diagnostic" in html or "death_diagnostics" in html
    assert "Detector → Lifecycle → Event" in html
    assert "Ground truth" in html
    assert "Export GT JSON" in html
    # FP/miss are inferred per frame, not toggled through filter checkboxes.
    assert "FALSE POSITIVE" in html
    assert "MISS" in html
    assert "CORRECT REJECT" in html
    assert "function gtVerdict(" in html
    assert "function gtSubjectPositive(" in html
    # real_active_gameplay validates the fused state, not HUD evidence.
    assert "o.active_gameplay === true" in html
    assert "Select a cadence tile" in html
    assert "selected cadence frame only" in html
    assert "does not retrain detectors" in html
    assert "t1:o.timestamp+0.5" not in html
    assert "t1:o.timestamp," in html
    assert "Real death" in html
    assert "DeathReading.detected" in html
    assert "GT_LABEL_MEANINGS" in html
    assert "label_meanings: GT_LABEL_MEANINGS" in html
    assert "function deathEventNear(" in html
    assert "function splatEventNear(" in html
    assert "function gtRealPositive(" in html
    assert "function chipGtBadge(" in html
    assert "function renderSplatLane(" in html
    assert "splat-badge" in html
    assert "splat-chip" in html
    assert "first alive→dead" in html
    assert "first rising edge" in html
    assert "Damage/recovery" in html
    assert "Opening countdown" in html
    assert "gt-channel" in html
    assert "real_map_overlay" in html
    assert "not_a_map_overlay" in html
    assert "detector:o.detector" in html or "detector:gtDetector" in html
    assert "!r.positive" in html
    assert "life-mark.dead .dot" in html
    assert "life-mark.awaiting-control .dot" in html
    assert "phase_label" in html
    assert ".ph[hidden]" in html
    assert "life-mark" in html
    assert "stem" in html
    assert "function preferredReading(" in html
    assert "function ensureCanonicalObservations(" in html
    assert "function chipReadings(" in html
    assert '["timer","death","splat","respawn","active_gameplay","map_overlay"]' in html

    assert "function isTileSelected(" in html
    assert "function frameKey(" in html
    assert "detector===\"death\") || tile.readings[0]" not in html
    assert "o.detector === \"timer\"" in html
    assert "box-shadow:0 0 0 2px var(--accent)" in html
    assert "o.frame_id" in html
    assert "function groupTiles(" in html
    assert "function renderPager(" in html
    assert "id=\"gallery-window\"" in html
    assert "function fmtTime(" in html
    assert "function parseTime(" in html
    assert "function fmtMmSs(" in html
    assert "Diagnostic timeline" not in html
    assert "Detector timeline" not in html
    assert "Only positive" not in html
    assert "Only frames with images" not in html
    assert "Show death episodes" not in html
    assert "Evidence gallery" not in html
    assert "filter-status" not in html
    assert "gallery-page-hint" not in html
    assert "Interval start" not in html
    assert "Interval end" not in html
    assert "Mark interval" not in html
    assert "Why? " not in html
    assert "Required evidence" not in html
    out = write_html(view, tmp_path / "viewer.html")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "countdown_confirmed_this_death_episode" in text
    assert "death_diagnostics" in text
    assert "id=\"filter-fp\"" not in text
    assert "id=\"filter-miss\"" not in text
    assert "id=\"gt-verdict\"" in text
    assert "id=\"gt-panel\"" in text
    # Verdict sits above the marking UI in the middle column.
    assert text.index("id=\"gt-verdict\"") < text.index("id=\"gt-panel\"")
    # Three-column detail: frame, GT evaluation, compacted readings.
    assert "minmax(360px,1.6fr)" in text
    assert "max-height:450px" in text
    assert "flag-fp" in text and "flag-miss" in text
    assert "stem" in text  # staggered lifecycle label connector


def test_map_overlay_present_is_positive(tmp_path: Path) -> None:
    """Map overlay uses ``present``, not ``detected``, as the positive cue."""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    jpg = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
        b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
        b"\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x14"
        b"\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xaa\xff\xd9"
    )
    (frames_dir / "map.jpg").write_bytes(jpg)
    manifest = {
        "schema_version": 1,
        "video_identity": "map1",
        "extraction_manifest_path": "frames",
        "analysis": {
            "analysis_id": "a",
            "package_version": "0.2.0",
            "pipeline_version": "3.0.0",
            "detector_versions": {},
            "extraction_manifest_sha256": "x",
            "vision_config_sha256": "y",
            "video_identity": "map1",
        },
        "frame_results": [
            {
                "frame_id": "m0",
                "timestamp": 1.0,
                "source": "cadence",
                "source_frame_index": 10,
                "frame_path": "frames/map.jpg",
                "detections": [
                    {
                        "id": "on",
                        "detector_name": "map_overlay",
                        "detector_version": "map_overlay@test",
                        "confidence": 0.8,
                        "reading": {"kind": "map_overlay", "present": True},
                    }
                ],
            },
            {
                "frame_id": "m1",
                "timestamp": 2.0,
                "source": "cadence",
                "source_frame_index": 20,
                "frame_path": "frames/map.jpg",
                "detections": [
                    {
                        "id": "off",
                        "detector_name": "map_overlay",
                        "detector_version": "map_overlay@test",
                        "confidence": 0.2,
                        "reading": {"kind": "map_overlay", "present": False},
                    }
                ],
            },
        ],
        "state_snapshots": [],
        "game_events": [],
    }
    path = tmp_path / "vision_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    view = load_manifest_view(path)
    by_id = {o.id: o for o in view.observations if o.detector == "map_overlay"}
    assert by_id["on"].positive is True
    assert by_id["off"].positive is False


def test_detector_positive_event_negative_flag(tmp_path: Path) -> None:
    """Sticky-dead death reading without a new DEATH event is flagged."""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    jpg = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
        b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
        b"\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x14"
        b"\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xaa\xff\xd9"
    )
    (frames_dir / "a.jpg").write_bytes(jpg)
    manifest = {
        "schema_version": 1,
        "video_identity": "sticky",
        "extraction_manifest_path": "frames",
        "analysis": {
            "analysis_id": "a",
            "package_version": "0.2.0",
            "pipeline_version": "3.0.0",
            "detector_versions": {},
            "extraction_manifest_sha256": "x",
            "vision_config_sha256": "y",
            "video_identity": "sticky",
        },
        "frame_results": [
            {
                "frame_id": "f0",
                "timestamp": 10.0,
                "source": "cadence",
                "source_frame_index": 1,
                "frame_path": "frames/a.jpg",
                "detections": [
                    {
                        "id": "d0",
                        "detector_name": "death",
                        "detector_version": "t",
                        "confidence": 0.9,
                        "reading": {
                            "kind": "death",
                            "detected": True,
                            "ouch_detected": True,
                            "ouch_white_score": 0.5,
                            "banner_detected": False,
                            "banner_dark_score": 0.9,
                        },
                    }
                ],
            },
            {
                "frame_id": "f1",
                "timestamp": 20.0,
                "source": "cadence",
                "source_frame_index": 2,
                "frame_path": "frames/a.jpg",
                "detections": [
                    {
                        "id": "d1",
                        "detector_name": "death",
                        "detector_version": "t",
                        "confidence": 0.91,
                        "reading": {
                            "kind": "death",
                            "detected": True,
                            "ouch_detected": True,
                            "ouch_white_score": 0.09,
                            "banner_detected": False,
                            "banner_dark_score": 0.85,
                        },
                    }
                ],
            },
        ],
        "state_snapshots": [
            {
                "timestamp": 10.0,
                "player_alive": False,
                "player_lifecycle": "dead",
                "quality": "observed",
            },
            {
                "timestamp": 20.0,
                "player_alive": False,
                "player_lifecycle": "dead",
                "quality": "observed",
            },
        ],
        "game_events": [
            {"start_time": 10.0, "event_type": "death", "confidence": 1.0},
        ],
    }
    path = tmp_path / "vision_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    view = load_manifest_view(path)
    suppressed = [d for d in view.death_diagnostics if d.decision == "suppressed"]
    assert suppressed
    assert any(
        "detector_positive_event_negative" in d.flags for d in suppressed
    )
    assert view.summary.detector_positive_event_negative >= 1


def test_death_rejection_rule_lines(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["frame_results"].append(
        {
            "frame_id": "f_dark",
            "timestamp": 14.0,
            "source": "cadence",
            "source_frame_index": 140,
            "frame_path": "frames/cd.jpg",
            "detections": [
                {
                    "id": "d_dark",
                    "detector_name": "death",
                    "detector_version": "t",
                    "confidence": 0.4,
                    "reading": {
                        "kind": "death",
                        "detected": False,
                        "ouch_detected": False,
                        "ouch_white_score": 0.02,
                        "banner_detected": False,
                        "banner_dark_score": 0.95,
                    },
                }
            ],
        }
    )
    raw["state_snapshots"].append(
        {
            "timestamp": 14.0,
            "player_alive": True,
            "player_lifecycle": "alive",
            "quality": "observed",
        }
    )
    path.write_text(json.dumps(raw), encoding="utf-8")
    view = load_manifest_view(path)
    rejected = next(d for d in view.death_diagnostics if d.timestamp == 14.0)
    assert rejected.decision == "rejected"
    assert any(not c.ok and "Ouch" in c.label for c in rejected.rule_lines)
    assert any(c.ok and "Banner" in c.label for c in rejected.rule_lines)


def test_fuse_exposes_episode_latch() -> None:
    from splatoon3_ai_coach.config.models import (
        RespawnDetectorConfig,
        DeathDetectorConfig,
        LifecycleFusionConfig,
        StateFusionConfig,
        TimerDetectorConfig,
    )
    from splatoon3_ai_coach.vision.models import (
        RespawnReading,
        DeathReading,
        DetectorResult,
        TimerReading,
        VisionFrameResult,
    )
    from splatoon3_ai_coach.vision.state import fuse_game_state

    frames = [
        VisionFrameResult(
            frame_id="f0",
            timestamp=1.0,
            source="cadence",
            detections=[
                DetectorResult(
                    id="d",
                    detector_name="death",
                    detector_version="t",
                    confidence=0.9,
                    reading=DeathReading(detected=True),
                )
            ],
        ),
        VisionFrameResult(
            frame_id="f1",
            timestamp=1.1,
            source="cadence",
            detections=[
                DetectorResult(
                    id="c1",
                    detector_name="respawn",
                    detector_version="t",
                    confidence=0.9,
                    reading=RespawnReading(detected=True, confidence=0.9, presence_score=0.7, template_score=0.8, evidence_type="template"),
                )
            ],
        ),
        VisionFrameResult(
            frame_id="f2",
            timestamp=1.2,
            source="cadence",
            detections=[
                DetectorResult(
                    id="c2",
                    detector_name="respawn",
                    detector_version="t",
                    confidence=0.9,
                    reading=RespawnReading(detected=True, confidence=0.9, presence_score=0.7, template_score=0.8, evidence_type="template"),
                ),
                DetectorResult(
                    id="t",
                    detector_name="timer",
                    detector_version="t",
                    confidence=0.9,
                    reading=TimerReading(display="3:00", seconds_remaining=180),
                ),
            ],
        ),
    ]
    snaps = fuse_game_state(
        frames,
        TimerDetectorConfig(roi=(0, 0, 1, 1), template_dir="."),
        StateFusionConfig(),
        DeathDetectorConfig(),
        None,
        RespawnDetectorConfig(),
        None,
        LifecycleFusionConfig(
            countdown_present_min_observations=2,
            death_requires_match_context=False,
        ),
    )
    assert snaps[-1].player_lifecycle == "countdown"
    assert snaps[-1].countdown_confirmed_this_death_episode is True


def test_load_without_scenario_json_is_empty(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path)
    view = load_manifest_view(path)
    assert view.scenario_evidence == []
    assert view.review_video_url == ""


def test_load_joins_scenario_contexts(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path)
    (tmp_path / "scenarios.json").write_text(
        json.dumps(
            [
                {
                    "scenario_id": "death_episode:196.500",
                    "scenario_type": "death_episode",
                    "start_time": 196.5,
                    "end_time": 207.5,
                    "outcome": "recovered",
                    "event_ids": [
                        "death:196.500:alive_to_dead:-",
                        "respawn:204.000:skip_countdown_control:-",
                    ],
                    "confidence": 1.0,
                    "context": {},
                },
                {
                    "scenario_id": "engagement:192.000",
                    "scenario_type": "engagement",
                    "start_time": 192.0,
                    "end_time": 194.0,
                    "outcome": "fragged",
                    "event_ids": ["splat:192.000:-:aabbccddeeff0011"],
                    "confidence": 1.0,
                    "context": {},
                },
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "scenario_contexts.json").write_text(
        json.dumps(
            [
                {
                    "scenario_id": "death_episode:196.500",
                    "timeline": {
                        "duration": 11.0,
                        "time_since_previous_death": None,
                        "time_to_next_death": 20.0,
                    },
                    "map": {
                        "map_check_count": 4,
                        "map_check_before_death": False,
                        "map_checks_during_death_episode": 2,
                        "map_checked_while_dead": True,
                    },
                    "combat": None,
                    "death_episode": {
                        "death_to_respawn": 7.5,
                        "death_to_active_again": 9.0,
                        "respawn_to_active_again": 1.5,
                        "complete": True,
                        "respawn_reason": "skip_countdown_control",
                    },
                },
                {
                    "scenario_id": "engagement:192.000",
                    "timeline": {"duration": 2.0},
                    "map": None,
                    "combat": {
                        "splat_count": 2,
                        "time_to_first_splat": None,
                        "time_to_last_splat": 2.0,
                        "splat_death_gap": 2.5,
                        "trade_candidate": False,
                    },
                    "death_episode": None,
                },
            ]
        ),
        encoding="utf-8",
    )
    view = load_manifest_view(path)
    assert view.review_video_url == ""
    assert [item.scenario_id for item in view.scenario_evidence] == [
        "death_episode:196.500",
        "engagement:192.000",
    ]
    recovery = view.scenario_evidence[0]
    assert recovery.outcome == "recovered"
    assert recovery.start_time == pytest.approx(196.5)
    assert recovery.end_time == pytest.approx(207.5)
    assert recovery.recovery is not None
    assert recovery.recovery["death_to_respawn"] == pytest.approx(7.5)
    assert recovery.map is not None
    assert recovery.map["map_checks_during_death_episode"] == 2
    assert recovery.event_ids[0].startswith("death:196.500")
    assert recovery.following_death_id is None
    engagement = view.scenario_evidence[1]
    assert engagement.outcome == "fragged"
    assert engagement.recovery is None
    assert engagement.combat is not None
    assert engagement.combat["trade_candidate"] is False
    assert engagement.event_ids == ["splat:192.000:-:aabbccddeeff0011"]
    assert engagement.following_death_id is None

    html = render_html(view)
    assert "Scenarios / Coaching Evidence" in html
    assert "Scenario Review" in html
    assert "Scenario = ownership" in html
    assert "this UI does not show judgments" in html
    assert ">Review<" in html
    assert "function seekReviewVideo(" in html
    assert "video.currentTime = at" in html
    assert "Number(card.start_time || 0)" in html
    assert "reviewPreroll" not in html
    assert "function stepReview(" in html
    assert "function renderScenarios(" in html
    assert "function renderMembersBlock(" in html
    assert "function formatScenarioOutcome(" in html
    assert "Splat observations" in html
    assert "Trade-window flag" in html
    assert "Splat–death gap" in html
    assert "Any map overlay before death" in html
    assert "Associated death episode (temporal rule)" in html
    assert "Follows death episode (temporal)" in html
    assert "SPLAT members only" in html
    assert "Outcome: fragged (no following death in window; not fight quality)" in html
    assert "DEATH_EPISODE" in html or "death_episode:196.500" in html
    assert "skip_countdown_control" in html
    assert "VISION MANIFEST VIEWER" in html
    assert "Cadence frames" in html
    # JSON enum values stay in embedded data; display qualifies separately.
    assert '"outcome": "fragged"' in html or "outcome: \"fragged\"" in html or engagement.outcome == "fragged"


def test_join_passes_following_death_id_and_qualifies_died_outcome(tmp_path: Path) -> None:
    """Viewer-only following_death_id pass-through; outcome enums stay unchanged."""
    from vision_manifest_viewer.loader import _join_scenario_card

    path = _write_manifest(tmp_path)
    death_eid = "death:151.500:alive_to_dead:-"
    splat_eid = "splat:150.000:-:aabbccddeeff0011"
    (tmp_path / "scenarios.json").write_text(
        json.dumps(
            [
                {
                    "scenario_id": "engagement:150.000",
                    "scenario_type": "engagement",
                    "start_time": 150.0,
                    "end_time": 150.0,
                    "outcome": "died",
                    "event_ids": [splat_eid],
                    "confidence": 1.0,
                    "context": {"following_death_id": death_eid, "splat_count": 1},
                },
                {
                    "scenario_id": "death_episode:151.500",
                    "scenario_type": "death_episode",
                    "start_time": 151.5,
                    "end_time": 151.5,
                    "outcome": "incomplete",
                    "event_ids": [death_eid],
                    "confidence": 1.0,
                    "context": {},
                },
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "scenario_contexts.json").write_text(
        json.dumps(
            [
                {
                    "scenario_id": "engagement:150.000",
                    "timeline": {"duration": 0.0},
                    "map": None,
                    "combat": {
                        "splat_count": 1,
                        "splat_death_gap": 1.5,
                        "trade_candidate": True,
                    },
                    "death_episode": None,
                    "relations": {
                        "leads_to_death_episode_id": "death_episode:151.500",
                    },
                },
                {
                    "scenario_id": "death_episode:151.500",
                    "timeline": {"duration": 0.0},
                    "map": {
                        "map_check_before_death": True,
                        "seconds_since_map_check_before_death": 44.5,
                    },
                    "combat": None,
                    "death_episode": {"complete": False},
                    "relations": {
                        "preceded_by_engagement_id": "engagement:150.000",
                    },
                },
            ]
        ),
        encoding="utf-8",
    )
    card = _join_scenario_card(
        "engagement:150.000",
        {
            "scenario_id": "engagement:150.000",
            "relations": {"leads_to_death_episode_id": "death_episode:151.500"},
            "combat": {"trade_candidate": True},
        },
        {
            "engagement:150.000": {
                "scenario_id": "engagement:150.000",
                "scenario_type": "engagement",
                "outcome": "died",
                "event_ids": [splat_eid],
                "context": {"following_death_id": death_eid},
            }
        },
    )
    assert card.following_death_id == death_eid
    assert card.outcome == "died"
    assert card.event_ids == [splat_eid]

    view = load_manifest_view(path)
    eng = next(c for c in view.scenario_evidence if c.scenario_id.startswith("engagement:"))
    death = next(c for c in view.scenario_evidence if c.scenario_id.startswith("death_episode:"))
    assert eng.following_death_id == death_eid
    assert eng.outcome == "died"
    assert death.following_death_id is None

    html = render_html(view)
    assert "Members" in html
    assert splat_eid in html
    assert death_eid in html
    assert "SPLAT members only" in html
    assert "Following death (compat id)" in html
    assert "Associated death episode (temporal rule)" in html
    assert "Outcome: died (nearby death associated; DEATH not a member)" in html
    assert "Gap since last map before death (unbounded)" in html
    assert "Trade-window flag" in html
    assert eng.outcome == "died"  # enum unchanged on view model
    assert '"outcome": "died"' in html or eng.outcome == "died"


def test_review_video_route_maps_to_supplied_file(tmp_path: Path) -> None:
    video = tmp_path / "clip.mov"
    video.write_bytes(b"not-a-real-video")
    assert resolve_review_video(REVIEW_VIDEO_PATH, video) == video
    assert resolve_review_video("/review-video?ts=1", video) == video
    assert resolve_review_video("/vision_manifest_viewer.html", video) is None
    assert review_video_content_type(video) == "video/quicktime"
    assert review_video_content_type(tmp_path / "clip.mp4") == "video/mp4"


def test_review_video_url_embeds_when_set(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path)
    view = load_manifest_view(path)
    view.review_video_url = REVIEW_VIDEO_PATH
    html = render_html(view)
    assert REVIEW_VIDEO_PATH in html
    assert '"review_video_url": "/review-video"' in html or "/review-video" in html
    assert "Could not load the review video" in html


def test_cli_without_video_writes_file_url_html(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path)
    assert viewer_main([str(path), "--no-open"]) == 0
    html_path = tmp_path / "vision_manifest_viewer.html"
    assert html_path.is_file()
    text = html_path.read_text(encoding="utf-8")
    assert "Scenario Review" in text
    assert 'id="scenario-review" hidden' in text
    assert '"review_video_url": ""' in text or '"review_video_url":""' in text
    # Review buttons are gated on DATA.review_video_url at runtime.
    assert "DATA.review_video_url" in text


def test_cli_parser_accepts_video_and_port() -> None:
    args = build_parser().parse_args(
        ["manifest.json", "--video", "/tmp/clip.mov", "--port", "9000"]
    )
    assert args.video == Path("/tmp/clip.mov")
    assert args.port == 9000


def test_cli_missing_video_raises_clear_error(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path)
    missing = tmp_path / "missing.mov"
    with pytest.raises(SystemExit, match="video not found"):
        viewer_main([str(path), "--video", str(missing), "--no-open"])


def test_review_seek_maps_start_time_directly() -> None:
    """Contract: video.currentTime uses scenario.start_time with no pre-roll."""
    html_path = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "vision_manifest_viewer"
        / "html.py"
    )
    source = html_path.read_text(encoding="utf-8")
    assert "const at = Math.max(0, Number(card.start_time || 0));" in source
    assert "video.currentTime = at;" in source
    assert "video.pause();" in source
    assert "reviewPreroll" not in source


def test_scenario_order_is_context_file_order(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path)
    (tmp_path / "scenarios.json").write_text(
        json.dumps(
            [
                {
                    "scenario_id": "engagement:10.000",
                    "scenario_type": "engagement",
                    "start_time": 10.0,
                    "end_time": 12.0,
                    "outcome": "fragged",
                    "event_ids": [],
                    "confidence": 1.0,
                    "context": {},
                },
                {
                    "scenario_id": "death_episode:20.000",
                    "scenario_type": "death_episode",
                    "start_time": 20.0,
                    "end_time": 30.0,
                    "outcome": "recovered",
                    "event_ids": [],
                    "confidence": 1.0,
                    "context": {},
                },
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "scenario_contexts.json").write_text(
        json.dumps(
            [
                {
                    "scenario_id": "death_episode:20.000",
                    "timeline": {"duration": 10.0},
                    "map": {"map_check_count": 0},
                    "combat": {"splat_count": 0},
                    "death_episode": {
                        "death_to_respawn": 1.0,
                        "death_to_active_again": 2.0,
                        "respawn_reason": "countdown_complete",
                    },
                },
                {
                    "scenario_id": "engagement:10.000",
                    "timeline": {"duration": 2.0},
                    "map": {"map_check_count": 0},
                    "combat": {"splat_count": 1, "trade_candidate": False},
                },
            ]
        ),
        encoding="utf-8",
    )
    view = load_manifest_view(path)
    assert [c.scenario_id for c in view.scenario_evidence] == [
        "death_episode:20.000",
        "engagement:10.000",
    ]
    html = render_html(view)
    assert "stepReview(-1)" in html
    assert "stepReview(1)" in html
    assert "(state.reviewIndex + delta + cards.length) % cards.length" in html


def test_review_server_supports_byte_range(tmp_path: Path) -> None:
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    from vision_manifest_viewer.server import serve_review

    root = tmp_path / "site"
    root.mkdir()
    (root / "index.html").write_text("ok", encoding="utf-8")
    video = tmp_path / "clip.bin"
    video.write_bytes(bytes(range(256)))
    server = serve_review(root, video, port=0)
    port = int(server.server_address[1])
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = Request(
            f"http://127.0.0.1:{port}/review-video",
            headers={"Range": "bytes=10-19"},
        )
        with urlopen(req, timeout=2) as response:
            assert response.status == 206
            assert response.headers["Accept-Ranges"] == "bytes"
            assert response.headers["Content-Range"] == "bytes 10-19/256"
            assert response.headers["Content-Length"] == "10"
            assert response.read() == bytes(range(10, 20))

        with urlopen(f"http://127.0.0.1:{port}/review-video", timeout=2) as response:
            assert response.status == 200
            assert response.headers["Accept-Ranges"] == "bytes"
            assert response.headers["Content-Length"] == "256"

        bad = Request(
            f"http://127.0.0.1:{port}/review-video",
            headers={"Range": "bytes=500-600"},
        )
        try:
            urlopen(bad, timeout=2)
            raise AssertionError("expected 416")
        except HTTPError as exc:
            assert exc.code == 416
            assert exc.headers["Content-Range"] == "bytes */256"
            assert exc.headers["Accept-Ranges"] == "bytes"
    finally:
        server.shutdown()
        server.server_close()


def test_review_video_path_is_not_a_filesystem_browser(tmp_path: Path) -> None:
    """Only exact /review-video maps to the supplied file."""
    video = tmp_path / "clip.mov"
    video.write_bytes(b"secret-video")
    other = tmp_path / "other.bin"
    other.write_bytes(b"other")
    assert resolve_review_video("/review-video", video) == video
    assert resolve_review_video("/review-video/", video) == video
    assert resolve_review_video("/review-video/../other.bin", video) is None
    assert resolve_review_video("/review-video%2f..%2fother.bin", video) is None
    assert resolve_review_video("/other.bin", video) is None
    assert resolve_review_video("//review-video", video) is None


def test_parse_byte_range_helpers() -> None:
    from vision_manifest_viewer.server import parse_byte_range, pick_free_port

    assert parse_byte_range("bytes=0-9", 100) == (0, 9)
    assert parse_byte_range("bytes=90-", 100) == (90, 99)
    assert parse_byte_range("bytes=-10", 100) == (90, 99)
    assert parse_byte_range(None, 100) is None
    assert parse_byte_range("bytes=500-600", 100) is None
    assert parse_byte_range("bytes=9-2", 100) is None
    port = pick_free_port(None)
    assert isinstance(port, int) and port > 0


def _minimal_manifest_shell(
    *,
    video_identity: str = "viz1",
    frame_results: list[dict] | None = None,
) -> dict:
    """Bare vision manifest shell for viewer unit tests."""
    return {
        "schema_version": 1,
        "video_identity": video_identity,
        "extraction_manifest_path": "frames",
        "analysis": {
            "analysis_id": "a",
            "package_version": "0.2.0",
            "pipeline_version": "3.0.0",
            "detector_versions": {},
            "extraction_manifest_sha256": "x",
            "vision_config_sha256": "y",
            "video_identity": video_identity,
        },
        "frame_results": frame_results or [],
        "state_snapshots": [],
        "game_events": [],
    }


def test_map_ink_sidecar_loaded_pass_through(tmp_path: Path) -> None:
    """map_observations.json paint fields load unchanged into the viewer."""
    path = tmp_path / "vision_manifest.json"
    path.write_text(json.dumps(_minimal_manifest_shell()), encoding="utf-8")
    sidecar = [
        {
            "video_time": 12.5,
            "stage_id": "scorch_gorge",
            "battle_mode_id": "turf_war",
            "ally_classified_fraction": 0.42,
            "opponent_classified_fraction": 0.31,
            "classified_fraction": 0.73,
            "confidence": 0.8,
            "total_sample_pixels": 1000,
            "classified_pixels": 730,
            "ally_ink_pixels": 420,
            "opponent_ink_pixels": 310,
            "unclassified_pixels": 270,
            "regions": [
                {
                    "region_id": "R01",
                    "total_pixels": 100,
                    "classified_pixels": 70,
                    "ally_ink_pixels": 40,
                    "opponent_ink_pixels": 30,
                    "unclassified_pixels": 30,
                    "ally_classified_fraction": 0.4,
                    "opponent_classified_fraction": 0.3,
                    "confidence": 0.7,
                }
            ],
        }
    ]
    (tmp_path / "map_observations.json").write_text(
        json.dumps(sidecar), encoding="utf-8"
    )
    view = load_manifest_view(path)
    assert len(view.map_ink_timeline) == 1
    sample = view.map_ink_timeline[0]
    assert sample.video_time == 12.5
    assert sample.stage_id == "scorch_gorge"
    assert sample.ally_classified_fraction == 0.42
    assert sample.opponent_classified_fraction == 0.31
    assert sample.classified_fraction == 0.73
    assert sample.ally_ink_pixels == 420
    assert sample.regions[0].region_id == "R01"
    html = render_html(view)
    assert "map-ink" in html
    assert "renderMapInkLane" in html


def test_map_ink_missing_sidecar_is_empty(tmp_path: Path) -> None:
    """Missing map_observations.json yields an empty timeline."""
    path = tmp_path / "vision_manifest.json"
    path.write_text(json.dumps(_minimal_manifest_shell()), encoding="utf-8")
    view = load_manifest_view(path)
    assert view.map_ink_timeline == []
    assert view.match_identity is None


def test_match_identity_shown_in_header(tmp_path: Path) -> None:
    """match_identity.json stage/mode appear in the viewer header payload."""
    path = tmp_path / "vision_manifest.json"
    path.write_text(json.dumps(_minimal_manifest_shell()), encoding="utf-8")
    (tmp_path / "match_identity.json").write_text(
        json.dumps(
            {
                "stage_id": "museum_dalfonsino",
                "battle_mode_id": "tower_control",
                "stage_score": 0.95,
                "battle_mode_score": 0.99,
                "resolved": True,
                "resolved_at": 8.5,
                "intro_closed": True,
                "map_ink_enabled": True,
            }
        ),
        encoding="utf-8",
    )
    view = load_manifest_view(path)
    assert view.match_identity is not None
    assert view.match_identity.stage_id == "museum_dalfonsino"
    assert view.match_identity.battle_mode_id == "tower_control"
    assert view.match_identity.resolved is True
    html = render_html(view)
    assert 'id="match-info"' in html
    assert "Museum Dalfonsino" in html or "museum_dalfonsino" in html
    assert "Tower Control" in html or "tower_control" in html
    assert "match_identity" in html


def test_special_gauge_visibility_and_timeline_html(tmp_path: Path) -> None:
    """Special gauge: visible→positive; ready/ordinary/invisible ticks in HTML."""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    jpg = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
        b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
        b"\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x14"
        b"\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xaa\xff\xd9"
    )
    (frames_dir / "g.jpg").write_bytes(jpg)
    manifest = _minimal_manifest_shell(
        video_identity="sg1",
        frame_results=[
            {
                "frame_id": "a",
                "timestamp": 1.0,
                "source": "cadence",
                "frame_path": "frames/g.jpg",
                "detections": [
                    {
                        "id": "sg-vis",
                        "detector_name": "special_gauge",
                        "detector_version": "special_gauge@test",
                        "confidence": 0.8,
                        "reading": {
                            "kind": "special_gauge",
                            "visible": True,
                            "fill_fraction": 0.47,
                            "ready": False,
                            "dial_score": 0.7,
                            "lit_sector_fraction": 0.47,
                            "ready_prompt_score": 0.1,
                        },
                    }
                ],
            },
            {
                "frame_id": "b",
                "timestamp": 2.0,
                "source": "cadence",
                "frame_path": "frames/g.jpg",
                "detections": [
                    {
                        "id": "sg-ready",
                        "detector_name": "special_gauge",
                        "detector_version": "special_gauge@test",
                        "confidence": 0.9,
                        "reading": {
                            "kind": "special_gauge",
                            "visible": True,
                            "fill_fraction": 0.19,
                            "ready": True,
                            "dial_score": 0.7,
                            "lit_sector_fraction": 0.19,
                            "ready_prompt_score": 0.6,
                        },
                    }
                ],
            },
            {
                "frame_id": "c",
                "timestamp": 3.0,
                "source": "cadence",
                "frame_path": "frames/g.jpg",
                "detections": [
                    {
                        "id": "sg-inv",
                        "detector_name": "special_gauge",
                        "detector_version": "special_gauge@test",
                        "confidence": 0.2,
                        "reading": {
                            "kind": "special_gauge",
                            "visible": False,
                            "fill_fraction": None,
                            "ready": False,
                            "dial_score": 0.1,
                            "lit_sector_fraction": 0.0,
                            "ready_prompt_score": 0.0,
                        },
                    }
                ],
            },
        ],
    )
    path = tmp_path / "vision_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    view = load_manifest_view(path)
    by_id = {o.id: o for o in view.observations if o.detector == "special_gauge"}
    assert by_id["sg-vis"].positive is True
    assert by_id["sg-ready"].positive is True
    assert by_id["sg-inv"].positive is False
    assert "special_gauge" in view.default_rois
    html = render_html(view)
    assert "special-tick" in html
    assert ".special-tick.ready" in html
    assert "renderSpecialLane" in html
    assert "fill_fraction" in html
    assert "Special gauge" in html
    # Invisible samples must not appear as Special ticks in the payload path:
    # lane JS filters reading.visible; ensure invisible observation exists but
    # ready is only set on the ready sample.
    payload = json.loads(
        html.split('id="viewer-data" type="application/json">', 1)[1].split(
            "</script>", 1
        )[0]
    )
    special = [o for o in payload["observations"] if o["detector"] == "special_gauge"]
    assert len(special) == 3
    visible = [o for o in special if o["reading"].get("visible")]
    assert len(visible) == 2
    assert any(o["reading"].get("ready") for o in visible)
    assert any(not o["reading"].get("ready") for o in visible)