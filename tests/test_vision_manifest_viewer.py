"""Tests for the read-only vision manifest viewer."""

from __future__ import annotations

import json
from pathlib import Path

from vision_manifest_viewer.html import render_html, write_html
from vision_manifest_viewer.loader import load_manifest_view
from vision_manifest_viewer.model import ObservationView
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
                    }
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
    assert "respawn" in view.default_rois
    assert view.default_rois["respawn"].x1 == 0.78
    assert len(view.transitions) >= 1
    assert any(
        t.event_type == "respawn" or t.to_phase == "respawned"
        for t in view.transitions
    )
    assert len(view.detector_lanes) == 3
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
    assert "RESPAWN" in labels
    assert "ACTIVE AGAIN" in labels
    assert "RESPAWN UI PRESENT" in labels


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
    assert "Death diagnostic" in html or "death_diagnostics" in html
    assert "Detector → Lifecycle → Event" in html
    assert "Detector+/event" in html or "filter-dpen" in html
    assert "Ground truth" in html
    assert "filter-status" in html
    assert "Export GT JSON" in html
    assert "Diagnostic timeline" in html
    assert "minGapPx" in html or "life-mark" in html
    out = write_html(view, tmp_path / "viewer.html")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "Near misses" in text or "near" in text
    assert "countdown_confirmed_this_death_episode" in text
    assert "death_diagnostics" in text
    assert "renderFilterStatus" in text
    assert "Only positive" in text
    assert "stem" in text  # staggered lifecycle label connector


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
        LifecycleFusionConfig(countdown_present_min_observations=2),
    )
    assert snaps[-1].player_lifecycle == "countdown"
    assert snaps[-1].countdown_confirmed_this_death_episode is True
