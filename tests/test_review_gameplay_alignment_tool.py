"""Synthetic tests for tools/diagnostics/review_gameplay_alignment.py."""

from __future__ import annotations

from pathlib import Path

import review_gameplay_alignment as tool

from splatoon3_ai_coach.media.vision_manifest import save_vision_manifest
from splatoon3_ai_coach.review.models import TeamIdentity, TimelineEventMarker
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    GameEvent,
    GameEventReason,
    GameEventType,
    PlayerCountReading,
    VisionFrameResult,
)
from tests.test_alignment import C_TRUE, _config, _manifest, _review


def _glyph(marker_id: str, row: str, lo: float, hi: float) -> dict:
    return {
        "marker_id": marker_id,
        "team_row": row,
        "video_interval": (lo, hi),
        "status": "aligned",
    }


# --- association rule ----------------------------------------------------------


def test_association_none_one_several() -> None:
    bracket = (100.0, 100.5)
    far = _glyph("far", "top", 200.0, 200.4)
    near = _glyph("a", "top", 103.0, 103.4)
    other = _glyph("b", "top", 96.0, 96.4)
    assert tool.associate(bracket, ["top"], [far], 5.0)["status"] == "unassociated"
    one = tool.associate(bracket, ["top"], [far, near], 5.0)
    assert (one["status"], one["marker_id"]) == ("used", "a")
    both = tool.associate(bracket, ["top"], [near, other], 5.0)
    assert both["status"] == "ambiguous"
    assert "residual_interval" not in both


def test_association_ignores_other_rows_and_unmapped_glyphs() -> None:
    bracket = (100.0, 100.5)
    glyphs = [
        _glyph("bottom", "bottom", 100.0, 100.4),
        {"marker_id": "u", "team_row": "top", "video_interval": None},
        _glyph("top", "top", 101.0, 101.4),
    ]
    out = tool.associate(bracket, ["top"], glyphs, 5.0)
    assert (out["status"], out["marker_id"]) == ("used", "top")


def test_without_ally_row_local_death_is_not_associated() -> None:
    out = tool.associate((100.0, 100.5), [], [_glyph("a", "top", 100, 100.4)], 5.0)
    assert out["status"] == "no_ally_row"


def test_roster_without_ally_row_is_ambiguous_when_both_rows_qualify() -> None:
    glyphs = [_glyph("t", "top", 100.0, 100.4), _glyph("b", "bottom", 101.0, 101.4)]
    out = tool.associate((100.0, 100.5), ["top", "bottom"], glyphs, 5.0)
    assert out["status"] == "ambiguous"


def test_residual_is_gameplay_minus_review() -> None:
    out = tool.residual((101.0, 101.5), _glyph("a", "top", 100.0, 100.4))
    assert out["residual_interval"] == (0.6, 1.5)
    assert out["residual_midpoint"] > 0 and out["contains_zero"] is False
    overlap = tool.residual((100.0, 100.5), _glyph("a", "top", 100.1, 100.4))
    assert overlap["contains_zero"] is True


# --- gameplay references -------------------------------------------------------


def _roster_frame(
    t: float, ally: tuple[int, ...], visible: bool = True
) -> VisionFrameResult:
    reading = PlayerCountReading(
        ally_dead_slots=ally, opponent_dead_slots=(), roster_visible=visible
    )
    return VisionFrameResult(
        frame_id=f"r{t}",
        timestamp=t,
        source="cadence",
        detections=[
            DetectorResult(
                id=f"pc:{t}",
                detector_name="player_count",
                detector_version="t",
                confidence=0.9,
                reading=reading,
            )
        ],
    )


def _with_frames(frames: list[VisionFrameResult]):
    return _manifest(t_start=0.0, t_end=0.0).model_copy(
        update={"frame_results": frames, "game_events": []}
    )


def test_roster_onset_needs_two_persistent_visible_frames() -> None:
    frames = [
        _roster_frame(10.0, ()),
        _roster_frame(10.5, (2,)),
        _roster_frame(11.0, (2,), visible=False),
        _roster_frame(11.5, (2,)),
        _roster_frame(20.0, ()),
        _roster_frame(20.5, (3,)),
        _roster_frame(21.0, ()),
    ]
    onsets, too_wide = tool.roster_x_onsets(_with_frames(frames), 3.0)
    assert onsets == [("ally", (10.0, 10.5))]
    assert too_wide == 0


def test_roster_onset_with_wide_bracket_is_excluded_and_counted() -> None:
    frames = [
        _roster_frame(10.0, ()),
        _roster_frame(15.0, (1,)),
        _roster_frame(15.5, (1,)),
    ]
    onsets, too_wide = tool.roster_x_onsets(_with_frames(frames), 3.0)
    assert (onsets, too_wide) == ([], 1)


def test_local_death_brackets_stop_at_match_end() -> None:
    events = [
        GameEvent(
            start_time=t,
            event_type=GameEventType.DEATH,
            reason=GameEventReason.ALIVE_TO_DEAD,
            confidence=1.0,
        )
        for t in (50.0, 400.0)
    ] + [
        GameEvent(
            start_time=320.5,
            event_type=GameEventType.MATCH_END,
            reason=GameEventReason.CLOCK_ZERO,
            confidence=1.0,
        )
    ]
    manifest = _manifest().model_copy(update={"game_events": events})
    assert tool.local_death_brackets(manifest, 2.0) == [(49.5, 50.0)]


# --- delta = 0 rule -------------------------------------------------------------


def _used(mid: float, half: float = 0.4) -> dict:
    return {
        "status": "used",
        "residual_midpoint": mid,
        "residual_half_width": half,
        "contains_zero": abs(mid) <= half,
    }


def test_summarize_direction_against_uncertainty() -> None:
    assert tool.summarize([_used(0.1)] * 3)["direction"] == "within_uncertainty"
    assert tool.summarize([_used(1.0)] * 3)["direction"] == "positive"
    assert tool.summarize([_used(-1.0)] * 3)["direction"] == "negative"
    assert tool.summarize([_used(1.0)] * 2)["direction"] == "insufficient"
    out = tool.summarize([{"status": "ambiguous"}, {"status": "unassociated"}])
    assert out["direction"] == "insufficient" and out["used"] == 0


def _pair(label: str, roster: str, local: str) -> dict:
    return {
        "label": label,
        "roster_x": {"summary": {"direction": roster}},
        "local_death": {"summary": {"direction": local}},
    }


def test_delta_change_needs_replication_in_every_match_and_reference() -> None:
    both = [_pair("a", "positive", "positive"), _pair("b", "positive", "positive")]
    assert tool.delta_test(both)["nonzero_delta_supported"] is True
    one = [_pair("a", "positive", "positive")]
    assert tool.delta_test(one)["decision"] == "delta_stays_0"
    refs_disagree = [_pair("a", "positive", "within_uncertainty"), both[1]]
    assert tool.delta_test(refs_disagree)["decision"] == "delta_stays_0"
    matches_disagree = [both[0], _pair("b", "negative", "negative")]
    assert tool.delta_test(matches_disagree)["decision"] == "delta_stays_0"
    insufficient = [both[0], both[1], _pair("c", "insufficient", "positive")]
    assert tool.delta_test(insufficient)["decision"] == "delta_stays_0"


# --- end to end -----------------------------------------------------------------


def test_measure_pair_true_alignment_gives_residuals_containing_zero(
    tmp_path: Path,
) -> None:
    r_true = 150.3
    death_t = C_TRUE - r_true
    ds = _review().model_copy(
        update={
            "team_identity": TeamIdentity(ally_row="top", source="cli"),
            "match_history_events": [
                TimelineEventMarker(
                    marker_id="m1",
                    team_row="top",
                    kind="death",
                    x_norm=(300.0 - r_true) / 300.0,
                    elapsed_estimate=r_true,
                )
            ],
        }
    )
    manifest = _manifest()
    death = GameEvent(
        start_time=round(death_t + 0.25, 1),
        event_type=GameEventType.DEATH,
        reason=GameEventReason.ALIVE_TO_DEAD,
        confidence=1.0,
    )
    manifest = manifest.model_copy(update={"game_events": [*manifest.game_events, death]})
    review_dir, gameplay_dir = tmp_path / "r", tmp_path / "g"
    review_dir.mkdir()
    (review_dir / "review_timeline.json").write_text(ds.model_dump_json(), "utf-8")
    save_vision_manifest(manifest, gameplay_dir)

    out = tool.measure_pair(review_dir, gameplay_dir, _config(), label="p")
    assert out["calibration"]["fit_status"] == "calibrated"
    (item,) = out["local_death"]["items"]
    assert item["status"] == "used" and item["contains_zero"] is True
    assert item["nearest_observed_game_clock"] is not None
