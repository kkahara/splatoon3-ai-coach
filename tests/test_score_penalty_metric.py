"""Tests for the Stage 0 joint score + penalty metric classes."""

from __future__ import annotations

from score_penalty_metric import SideState, classify_step


def _s(main: int | None, main_q: str, pen: int | None, pen_q: str) -> SideState:
    return SideState(main, main_q, pen, pen_q)


def _classes(prev: SideState, cur: SideState) -> dict[str, str]:
    return dict(classify_step(prev, cur, 0.5))


def test_main_rise_is_confirmed_violation() -> None:
    got = _classes(
        _s(1, "observed", None, "not_shown"), _s(62, "observed", None, "not_shown")
    )
    assert got["main_rise"] == "confirmed_violation"
    assert got["work_remaining_no_unexplained_rise"] == "confirmed_violation"


def test_main_tick_with_penalty_absent_is_consistent() -> None:
    got = _classes(
        _s(62, "observed", None, "not_shown"), _s(61, "observed", None, "not_shown")
    )
    assert got == {
        "main_tick_needs_penalty_absent": "consistent",
        "work_remaining_no_unexplained_rise": "consistent",
    }


def test_main_tick_while_penalty_observed_is_disagreement_not_blame() -> None:
    got = _classes(_s(62, "observed", 10, "observed"), _s(61, "observed", 10, "observed"))
    assert got["main_tick_needs_penalty_absent"] == "detector_disagreement"


def test_main_tick_with_held_penalty_is_insufficient_evidence() -> None:
    got = _classes(_s(62, "observed", 10, "held"), _s(61, "observed", None, "unknown"))
    assert got["main_tick_needs_penalty_absent"] == "insufficient_evidence"


def test_penalty_clearing_into_main_tick_is_consistent() -> None:
    got = _classes(
        _s(83, "observed", 1, "observed"), _s(82, "observed", None, "not_shown")
    )
    assert got["main_tick_needs_penalty_absent"] == "consistent"


def test_large_penalty_vanishing_during_tick_is_insufficient() -> None:
    """A +12 that disappears while the main ticks may be a missed read."""
    got = _classes(
        _s(83, "observed", 12, "observed"), _s(82, "observed", None, "not_shown")
    )
    assert got["main_tick_needs_penalty_absent"] == "insufficient_evidence"


def test_penalty_tick_with_main_hold_is_consistent() -> None:
    got = _classes(_s(83, "observed", 13, "observed"), _s(83, "observed", 12, "observed"))
    assert got["penalty_tick_needs_main_hold"] == "consistent"


def test_penalty_tick_without_main_is_insufficient() -> None:
    got = _classes(
        _s(None, "rejected_implausible", 13, "observed"),
        _s(None, "unknown", 12, "observed"),
    )
    assert got["penalty_tick_needs_main_hold"] == "insufficient_evidence"


def test_penalty_jump_is_confirmed_violation() -> None:
    got = _classes(_s(41, "observed", 39, "observed"), _s(41, "observed", 3, "observed"))
    assert got["penalty_jump"] == "confirmed_violation"


def test_penalty_appearing_is_allowed_work_rise() -> None:
    got = _classes(
        _s(83, "observed", None, "not_shown"), _s(83, "observed", 14, "observed")
    )
    assert got["penalty_appears_needs_main_hold"] == "consistent"
    assert got["work_remaining_no_unexplained_rise"] == "consistent"


def test_not_shown_is_not_zero_outside_work_check() -> None:
    """Without both counters known, no work_remaining judgement is made."""
    got = _classes(_s(83, "observed", None, "not_shown"), _s(83, "held", None, "unknown"))
    assert "work_remaining_no_unexplained_rise" not in got
