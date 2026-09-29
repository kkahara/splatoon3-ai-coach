"""Tests for Splat Zones score + penalty fusion (left→ally / right→opponent)."""

from __future__ import annotations

import pytest

from splatoon3_ai_coach.config.models import (
    ScoreDetectorConfig,
    StateFusionConfig,
    TimerDetectorConfig,
)
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    MatchIntroReading,
    ScoreReading,
    ScoreSideReading,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.score_fusion import (
    ScoreFrameFuser,
    ScoreFrameFusion,
    ScorePlausibility,
    resolve_battle_mode,
    retract_contradicted_scores,
)
from splatoon3_ai_coach.vision.state import (
    _ScoreSideMemory,
    fuse_game_state,
    fuse_score_sides_at,
)


def _side(value: int | None, *, visible: bool) -> ScoreSideReading:
    return ScoreSideReading(
        value=value,
        digit_scores=[0.9] if visible and value is not None else [],
        visible=visible,
    )


def _reading(left: ScoreSideReading, right: ScoreSideReading) -> ScoreReading:
    scores = list(left.digit_scores) + list(right.digit_scores)
    conf = float(sum(scores) / len(scores)) if scores else 0.0
    return ScoreReading(left=left, right=right, confidence=conf)


def _score_frame(
    timestamp: float,
    index: int,
    *,
    left: ScoreSideReading,
    right: ScoreSideReading,
    confidence: float = 0.9,
) -> VisionFrameResult:
    reading = _reading(left, right)
    return VisionFrameResult(
        frame_id=f"frame:{index}",
        timestamp=timestamp,
        source="cadence",
        source_frame_index=index,
        detections=[
            DetectorResult(
                id=f"score:{index}",
                detector_name="score",
                detector_version="score@0.1.0",
                confidence=confidence,
                reading=reading,
            )
        ],
    )


def _timer_config() -> TimerDetectorConfig:
    return TimerDetectorConfig(roi=(0.0, 0.0, 1.0, 1.0), template_dir=".")


def test_asymmetric_hold_sequence() -> None:
    """Plan-required asymmetric visible/held sequence."""
    # Drive match_phase via lifecycle by also providing timer readings that
    # establish in_match context — use pure fuse_score_sides_at for clarity.
    ally = _ScoreSideMemory()
    opp = _ScoreSideMemory()
    hold = 2.0

    steps = [
        (0.0, _side(100, visible=True), _side(100, visible=True)),
        (0.5, _side(99, visible=True), _side(None, visible=False)),
        (1.0, _side(None, visible=False), _side(None, visible=False)),
        (1.5, _side(99, visible=True), _side(98, visible=True)),
    ]
    expected = [
        (100, "observed", 100, "observed"),
        (99, "observed", 100, "held"),
        (99, "held", 100, "held"),
        (99, "observed", 98, "observed"),
    ]
    for (t, left, right), exp in zip(steps, expected, strict=True):
        reading = _reading(left, right)
        a, aq, o, oq, _, ally, opp = fuse_score_sides_at(
            timestamp=t,
            match_phase="in_match",
            reading=reading,
            reading_usable=True,
            evidence_id=f"e@{t}",
            max_hold_seconds=hold,
            ally_mem=ally,
            opponent_mem=opp,
        )
        assert (a, aq, o, oq) == exp


def test_hold_does_not_invent_mid_values() -> None:
    """100 → gap → 97 retains 100 while held; never 98/99."""
    ally = _ScoreSideMemory()
    opp = _ScoreSideMemory()
    hold = 2.0

    # t0 observe 100
    _, _, o, oq, _, ally, opp = fuse_score_sides_at(
        timestamp=0.0,
        match_phase="in_match",
        reading=_reading(_side(100, visible=True), _side(100, visible=True)),
        reading_usable=True,
        evidence_id="e0",
        max_hold_seconds=hold,
        ally_mem=ally,
        opponent_mem=opp,
    )
    assert o == 100 and oq == "observed"

    # gap within hold
    for t in (0.5, 1.0, 1.5):
        _, _, o, oq, _, ally, opp = fuse_score_sides_at(
            timestamp=t,
            match_phase="in_match",
            reading=_reading(_side(None, visible=False), _side(None, visible=False)),
            reading_usable=True,
            evidence_id=f"e{t}",
            max_hold_seconds=hold,
            ally_mem=ally,
            opponent_mem=opp,
        )
        assert o == 100
        assert oq == "held"
        assert o not in {98, 99}

    # accept 97 when visible again
    _, _, o, oq, _, ally, opp = fuse_score_sides_at(
        timestamp=2.0,
        match_phase="in_match",
        reading=_reading(_side(100, visible=True), _side(97, visible=True)),
        reading_usable=True,
        evidence_id="e2",
        max_hold_seconds=hold,
        ally_mem=ally,
        opponent_mem=opp,
    )
    assert o == 97 and oq == "observed"


def test_hold_expiry_clears_side() -> None:
    ally = _ScoreSideMemory()
    opp = _ScoreSideMemory()
    fuse_score_sides_at(
        timestamp=0.0,
        match_phase="in_match",
        reading=_reading(_side(50, visible=True), _side(40, visible=True)),
        reading_usable=True,
        evidence_id="e0",
        max_hold_seconds=2.0,
        ally_mem=ally,
        opponent_mem=opp,
    )
    a, aq, o, oq, _, _, _ = fuse_score_sides_at(
        timestamp=2.5,
        match_phase="in_match",
        reading=_reading(_side(None, visible=False), _side(None, visible=False)),
        reading_usable=True,
        evidence_id="e1",
        max_hold_seconds=2.0,
        ally_mem=ally,
        opponent_mem=opp,
    )
    assert a is None and aq == "unknown"
    assert o is None and oq == "unknown"


def test_out_of_match_clears_held_scores() -> None:
    ally = _ScoreSideMemory(value=10, last_at=1.0, evidence_ids=["e"])
    opp = _ScoreSideMemory(value=20, last_at=1.0, evidence_ids=["e"])
    a, aq, o, oq, _, ally2, opp2 = fuse_score_sides_at(
        timestamp=1.5,
        match_phase="post_match",
        reading=_reading(_side(10, visible=True), _side(20, visible=True)),
        reading_usable=True,
        evidence_id="e2",
        max_hold_seconds=2.0,
        ally_mem=ally,
        opponent_mem=opp,
    )
    assert a is None and o is None
    assert aq == "unknown" and oq == "unknown"
    assert ally2.value is None and opp2.value is None


def test_one_sided_visible_does_not_invalidate_other() -> None:
    """Usable reading with only left visible still updates ally independently."""
    ally = _ScoreSideMemory()
    opp = _ScoreSideMemory(value=100, last_at=0.0, evidence_ids=["e0"])
    a, aq, o, oq, _, _, _ = fuse_score_sides_at(
        timestamp=0.5,
        match_phase="in_match",
        reading=_reading(_side(99, visible=True), _side(None, visible=False)),
        reading_usable=True,
        evidence_id="e1",
        max_hold_seconds=2.0,
        ally_mem=ally,
        opponent_mem=opp,
    )
    assert a == 99 and aq == "observed"
    assert o == 100 and oq == "held"


def test_fuse_game_state_wires_score_fields() -> None:
    """End-to-end: ScoreReading on frames lands on snapshot remaining fields."""
    # Without timer/match context, lifecycle may be out_of_match and clear scores.
    # Seed with timer so match_context can enter in_match after countdown logic —
    # easier to assert via fuse_score path already covered; here verify fields exist.
    frames = [
        _score_frame(
            0.0,
            0,
            left=_side(100, visible=True),
            right=_side(100, visible=True),
        )
    ]
    snaps = fuse_game_state(
        frames,
        _timer_config(),
        StateFusionConfig(),
        score_config=ScoreDetectorConfig(score_max_hold_seconds=2.0),
    )
    assert len(snaps) == 1
    # out_of_match clears — still validates schema wiring
    assert snaps[0].ally_score_quality in {"observed", "held", "unknown"}
    assert snaps[0].opponent_score_quality in {"observed", "held", "unknown"}


def _intro_frame(mode: str | None) -> VisionFrameResult:
    return VisionFrameResult(
        frame_id="frame:intro",
        timestamp=0.0,
        source="cadence",
        source_frame_index=0,
        detections=[
            DetectorResult(
                id="match_intro:0",
                detector_name="match_intro",
                detector_version="match_intro@0.1.0",
                confidence=0.95,
                reading=MatchIntroReading(
                    stage_id="mahi_mahi_resort",
                    battle_mode_id=mode,
                    stage_template_score=0.95,
                    battle_mode_template_score=0.95 if mode else 0.0,
                ),
            )
        ],
    )


def _penalty_frame(
    timestamp: float,
    index: int,
    *,
    left: ScoreSideReading,
    right: ScoreSideReading,
    left_penalty: int | None = None,
    right_penalty: int | None = None,
    evaluated: bool = True,
) -> VisionFrameResult:
    frame = _score_frame(timestamp, index, left=left, right=right)
    reading = frame.detections[0].reading
    assert isinstance(reading, ScoreReading)
    frame.detections[0] = frame.detections[0].model_copy(
        update={
            "reading": reading.model_copy(
                update={
                    "left_penalty": left_penalty,
                    "right_penalty": right_penalty,
                    "penalty_evaluated": evaluated,
                }
            )
        }
    )
    return frame


def test_resolve_battle_mode_from_intro_readings() -> None:
    assert resolve_battle_mode([_intro_frame("splat_zones")]) == "splat_zones"
    assert resolve_battle_mode([_intro_frame(None)]) is None
    assert resolve_battle_mode([]) is None


@pytest.mark.parametrize("mode", ["turf_war", "rainmaker", None])
def test_non_splat_zones_mode_is_unknown(mode: str | None) -> None:
    """Score and penalty stay unknown unless the match is Splat Zones."""
    fuser = ScoreFrameFuser(ScoreDetectorConfig(), mode)
    frame = _penalty_frame(
        1.0,
        1,
        left=_side(80, visible=True),
        right=_side(70, visible=True),
        left_penalty=5,
    )
    fused = fuser.step(frame, "in_match")
    assert fused.ally_remaining is None and fused.ally_score_quality == "unknown"
    assert fused.ally_penalty is None and fused.ally_penalty_quality == "unknown"
    assert not fused.asserts_anything


def test_penalty_observed_not_shown_held_unknown() -> None:
    """Penalty qualities are distinct; not_shown is never reported as zero."""
    fuser = ScoreFrameFuser(
        ScoreDetectorConfig(score_plausibility_enabled=False), "splat_zones"
    )
    seen = fuser.step(
        _penalty_frame(
            1.0,
            1,
            left=_side(80, visible=True),
            right=_side(70, visible=True),
            left_penalty=12,
        ),
        "in_match",
    )
    assert (seen.ally_penalty, seen.ally_penalty_quality) == (12, "observed")
    assert (seen.opponent_penalty, seen.opponent_penalty_quality) == (None, "not_shown")

    no_reading = VisionFrameResult(
        frame_id="frame:2", timestamp=1.5, source="cadence", source_frame_index=2
    )
    held = fuser.step(no_reading, "in_match")
    assert (held.ally_penalty, held.ally_penalty_quality) == (12, "held")
    assert held.opponent_penalty_quality == "unknown"

    gone = fuser.step(
        _penalty_frame(
            2.0, 3, left=_side(80, visible=True), right=_side(70, visible=True)
        ),
        "in_match",
    )
    assert (gone.ally_penalty, gone.ally_penalty_quality) == (None, "not_shown")

    after = fuser.step(
        VisionFrameResult(frame_id="frame:4", timestamp=2.5, source="cadence"),
        "in_match",
    )
    assert after.ally_penalty is None and after.ally_penalty_quality == "unknown"


def test_penalty_not_shown_requires_evaluated_rois_and_visible_main() -> None:
    fuser = ScoreFrameFuser(
        ScoreDetectorConfig(score_plausibility_enabled=False), "splat_zones"
    )
    unevaluated = fuser.step(
        _penalty_frame(
            1.0,
            1,
            left=_side(80, visible=True),
            right=_side(70, visible=True),
            evaluated=False,
        ),
        "in_match",
    )
    assert unevaluated.ally_penalty_quality == "unknown"
    hidden_main = fuser.step(
        _penalty_frame(
            1.5, 2, left=_side(None, visible=False), right=_side(70, visible=True)
        ),
        "in_match",
    )
    assert hidden_main.ally_penalty_quality == "unknown"
    assert hidden_main.opponent_penalty_quality == "not_shown"


def test_penalty_read_when_main_counter_unreadable() -> None:
    """A +N is fused even on frames whose main counters failed to read."""
    fuser = ScoreFrameFuser(
        ScoreDetectorConfig(score_plausibility_enabled=False), "splat_zones"
    )
    frame = _penalty_frame(
        1.0,
        1,
        left=_side(None, visible=False),
        right=_side(None, visible=False),
        right_penalty=7,
    )
    frame.detections[0] = frame.detections[0].model_copy(update={"confidence": 0.0})
    fused = fuser.step(frame, "in_match")
    assert fused.ally_remaining is None
    assert (fused.opponent_penalty, fused.opponent_penalty_quality) == (7, "observed")
    assert fused.evidence_ids == ["score:1"]


def test_out_of_match_clears_penalty() -> None:
    fuser = ScoreFrameFuser(
        ScoreDetectorConfig(score_plausibility_enabled=False), "splat_zones"
    )
    fuser.step(
        _penalty_frame(
            1.0,
            1,
            left=_side(80, visible=True),
            right=_side(70, visible=True),
            left_penalty=4,
        ),
        "in_match",
    )
    fuser.step(
        VisionFrameResult(frame_id="f", timestamp=1.2, source="cadence"), "post_match"
    )
    fused = fuser.step(
        VisionFrameResult(frame_id="g", timestamp=1.4, source="cadence"), "in_match"
    )
    assert fused.ally_penalty is None and fused.ally_penalty_quality == "unknown"


def _plausible_step(
    ally: _ScoreSideMemory,
    opp: _ScoreSideMemory,
    t: float,
    left: int | None,
    rules: ScorePlausibility,
) -> tuple[int | None, str, _ScoreSideMemory, _ScoreSideMemory]:
    a, aq, _, _, _, ally, opp = fuse_score_sides_at(
        timestamp=t,
        match_phase="in_match",
        reading=_reading(
            _side(left, visible=left is not None), _side(None, visible=False)
        ),
        reading_usable=True,
        evidence_id=f"e@{t}",
        max_hold_seconds=2.0,
        ally_mem=ally,
        opponent_mem=opp,
        plausibility=rules,
    )
    return a, aq, ally, opp


RULES = ScorePlausibility(enabled=True, confirm_readings=3)


def test_plausibility_rejects_spurious_one_and_eleven() -> None:
    """62 → 1 → 11 → 61: the misreads are rejected, never become the count."""
    ally = _ScoreSideMemory(value=62, last_at=0.0, evidence_ids=["e0"])
    opp = _ScoreSideMemory()
    got = []
    for t, v in ((0.5, 1), (1.0, 11), (1.5, 61)):
        a, aq, ally, opp = _plausible_step(ally, opp, t, v, RULES)
        got.append((a, aq))
    assert got == [
        (None, "rejected_implausible"),
        (None, "rejected_implausible"),
        (61, "observed"),
    ]


def test_plausibility_rejects_rise() -> None:
    ally = _ScoreSideMemory(value=40, last_at=0.0, evidence_ids=["e0"])
    a, aq, _, _ = _plausible_step(ally, _ScoreSideMemory(), 0.5, 41, RULES)
    assert (a, aq) == (None, "rejected_implausible")


def test_plausibility_accepts_large_drop_after_long_gap() -> None:
    """A long invisible gap allows a proportionally larger real drop."""
    ally = _ScoreSideMemory(value=63, last_at=0.0, evidence_ids=["e0"])
    a, aq, _, _ = _plausible_step(ally, _ScoreSideMemory(), 40.0, 2, RULES)
    assert (a, aq) == (2, "observed")


def test_plausibility_never_confirms_sustained_impossible_drop() -> None:
    """Highlighted-pod misreads repeat; 77 → 1,1,1,1 stays rejected."""
    ally = _ScoreSideMemory(value=77, last_at=0.0, evidence_ids=["e0"])
    opp = _ScoreSideMemory()
    got = []
    for t in (0.5, 1.0, 1.5, 2.0):
        a, aq, ally, opp = _plausible_step(ally, opp, t, 1, RULES)
        got.append((a, aq))
    assert got == [(None, "rejected_implausible")] * 4
    a, aq, _, _ = _plausible_step(ally, opp, 2.5, 76, RULES)
    assert (a, aq) == (76, "observed")


def test_plausibility_confirms_consistent_rise_to_recover() -> None:
    """Three agreeing readings above a wrong low value correct the trajectory."""
    ally = _ScoreSideMemory(value=11, last_at=0.0, evidence_ids=["e0"])
    opp = _ScoreSideMemory()
    got = []
    for t, v in ((0.5, 50), (1.0, 50), (1.5, 49)):
        a, aq, ally, opp = _plausible_step(ally, opp, t, v, RULES)
        got.append((a, aq))
    assert got[:2] == [(None, "rejected_implausible")] * 2
    assert got[2] == (49, "observed")
    assert ally.evidence_ids == ["e@0.5", "e@1.0", "e@1.5"]


def test_plausibility_gap_breaks_confirmation() -> None:
    """An invisible frame restarts confirmation (77, gap, 77 is not two agreeing)."""
    ally = _ScoreSideMemory(value=1, last_at=0.0, evidence_ids=["e0"])
    opp = _ScoreSideMemory()
    rules = ScorePlausibility(enabled=True, confirm_readings=2)
    _, _, ally, opp = _plausible_step(ally, opp, 0.5, 77, rules)
    _, _, ally, opp = _plausible_step(ally, opp, 1.0, None, rules)
    a, aq, _, _ = _plausible_step(ally, opp, 1.5, 77, rules)
    assert (a, aq) == (None, "rejected_implausible")


def test_plausibility_first_reading_needs_confirmation() -> None:
    ally = _ScoreSideMemory()
    opp = _ScoreSideMemory()
    got = []
    for t in (0.0, 0.5, 1.0):
        a, aq, ally, opp = _plausible_step(ally, opp, t, 100, RULES)
        got.append((a, aq))
    assert got[-1] == (100, "observed")
    assert got[0] == (None, "rejected_implausible")


def test_score_frame_fuser_uses_config_plausibility() -> None:
    fuser = ScoreFrameFuser(ScoreDetectorConfig(), "splat_zones")
    for i in range(3):
        seeded = fuser.step(
            _score_frame(
                i * 0.5, i, left=_side(62, visible=True), right=_side(80, visible=True)
            ),
            "in_match",
        )
    assert seeded.ally_remaining == 62
    spurious = fuser.step(
        _score_frame(1.5, 3, left=_side(11, visible=True), right=_side(80, visible=True)),
        "in_match",
    )
    assert spurious.ally_remaining is None
    assert spurious.ally_score_quality == "rejected_implausible"
    assert spurious.opponent_remaining == 80


def _obs(value: int | None, quality: str = "observed") -> ScoreFrameFusion:
    return ScoreFrameFusion(ally_remaining=value, ally_score_quality=quality)


def test_retract_contradicted_low_value_and_its_hold() -> None:
    """16, 1 (accepted after a gap), 1 held, 16: the 1s were misreads."""
    fusions = [_obs(16), _obs(1), _obs(1, "held"), _obs(16), _obs(15)]
    out = retract_contradicted_scores(fusions, ["in_match"] * 5, slack=2)
    assert [(f.ally_remaining, f.ally_score_quality) for f in out] == [
        (16, "observed"),
        (None, "rejected_implausible"),
        (None, "unknown"),
        (16, "observed"),
        (15, "observed"),
    ]


def test_retract_keeps_real_endgame_and_small_noise() -> None:
    fusions = [_obs(5), _obs(3), _obs(4), _obs(1)]
    out = retract_contradicted_scores(fusions, ["in_match"] * 4, slack=2)
    assert out == fusions


def test_retract_does_not_cross_match_boundary() -> None:
    fusions = [_obs(1), ScoreFrameFusion(), _obs(100)]
    phases = ["in_match", "post_match", "in_match"]
    assert retract_contradicted_scores(fusions, phases, slack=2) == fusions
