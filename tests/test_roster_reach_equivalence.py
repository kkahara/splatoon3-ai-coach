"""Tests for the roster reach production-equivalence check (tools/diagnostics)."""

from __future__ import annotations

import roster_reach_equivalence as equivalence

from splatoon3_ai_coach.config.models import PlayerCountDetectorConfig


def test_same_score_within_tolerance_only() -> None:
    assert equivalence.same_score(0.0291, 0.0291 + 5e-7)
    assert not equivalence.same_score(0.0291, 0.0301)


def test_same_score_accepts_only_the_production_ceiling_case() -> None:
    assert equivalence.same_score(1.0, 1.27)
    assert equivalence.clamped(1.0, 1.27)
    assert not equivalence.same_score(0.99, 1.27)
    assert not equivalence.same_score(1.0, 0.5)
    assert not equivalence.clamped(0.5, 0.5)


def test_zeroed_keeps_everything_but_extensions() -> None:
    config = PlayerCountDetectorConfig()
    zero = equivalence.zeroed(config)
    assert set(equivalence.extensions(zero).values()) == {0}
    assert zero.ally_slots == config.ally_slots
    assert zero.sqdiff_match_threshold == config.sqdiff_match_threshold
    assert equivalence.extensions(config) == {
        "a1": 16,
        "a2": 0,
        "a3": 0,
        "a4": 0,
        "o1": 0,
        "o2": 0,
        "o3": 8,
        "o4": 0,
    }
