"""Match-phase fusion: intro → opening countdown → in-match."""

from splatoon3_ai_coach.config.models import LifecycleFusionConfig
from splatoon3_ai_coach.vision.lifecycle import LifecycleFuser, LifecycleObservation
from splatoon3_ai_coach.vision.match_phase import MatchPhaseFuser


def _cfg(**overrides: float | bool | tuple[int, ...]) -> LifecycleFusionConfig:
    """Lifecycle config with a short in-match hold for tests."""
    values: dict[str, float | bool | tuple[int, ...]] = {
        "match_context_hold_seconds": 5.0,
        "death_requires_match_context": True,
        "opening_clock_seconds": (180, 300),
    }
    values.update(overrides)
    return LifecycleFusionConfig(**values)  # type: ignore[arg-type]


def test_hud_without_timer_is_intro() -> None:
    fuser = MatchPhaseFuser(_cfg())
    assert fuser.step(1.0, None, hud_evidence=True) == "intro"
    assert fuser.step(1.0, None, hud_evidence=False) == "out_of_match"


def test_frozen_five_minutes_is_opening_countdown() -> None:
    fuser = MatchPhaseFuser(_cfg())
    assert fuser.step(1.0, 300.0, hud_evidence=True) == "opening_countdown"
    assert fuser.step(1.5, 300.0, hud_evidence=True) == "opening_countdown"


def test_clock_tick_enters_in_match() -> None:
    fuser = MatchPhaseFuser(_cfg())
    fuser.step(1.0, 300.0, hud_evidence=True)
    assert fuser.step(2.0, 299.0, hud_evidence=True) == "in_match"


def test_mid_match_three_minutes_stays_in_match() -> None:
    """180s after GO is still in-match, not a second opening freeze."""
    fuser = MatchPhaseFuser(_cfg())
    fuser.step(1.0, 200.0)
    assert fuser.step(2.0, 180.0) == "in_match"


def test_intro_hud_does_not_become_active_or_alive() -> None:
    fuser = LifecycleFuser(_cfg())
    result = None
    for index in range(5):
        result = fuser.step(
            float(index),
            LifecycleObservation(
                death_detected=False,
                countdown_present=None,
                active_detected=True,
                timer_seconds=None,
            ),
        )
    assert result is not None
    assert result.match_phase == "intro"
    assert result.player_lifecycle == "unknown"
    assert result.active_gameplay is False


def test_death_blocked_during_opening_countdown() -> None:
    fuser = LifecycleFuser(_cfg())
    fuser.step(
        1.0,
        LifecycleObservation(
            death_detected=False,
            countdown_present=None,
            active_detected=True,
            timer_seconds=300.0,
        ),
    )
    result = fuser.step(
        1.5,
        LifecycleObservation(
            death_detected=True,
            countdown_present=None,
            active_detected=True,
            timer_seconds=300.0,
        ),
    )
    assert result.match_phase == "opening_countdown"
    assert result.player_lifecycle != "dead"
