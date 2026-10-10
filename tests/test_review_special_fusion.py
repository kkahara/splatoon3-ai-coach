"""Review SPECIAL_USE fusion semantics (contract: review/SPECIAL_USE_FUSION.md)."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import get_args

import pytest
from typer.testing import CliRunner

from splatoon3_ai_coach.cli.app import app
from splatoon3_ai_coach.review import special_fusion
from splatoon3_ai_coach.review.models import (
    AxisCalibrationEvidence,
    CoverageReport,
    CursorObservation,
    CursorSlotObservation,
    PlayerDeathEpisodeEvidence,
    ReviewTimelineDataset,
    ReviewTimelineSample,
    SlotState,
    SpecialUseEvent,
    TeamIdentity,
    TimelineEventMarker,
)
from splatoon3_ai_coach.review.special_fusion import (
    FACTS_FILENAME,
    LABEL_CONFIRMED_SPECIALS,
    OutsidePlayableInterval,
    SpecialUseFact,
    SpecialUseFacts,
    derive_special_use_facts,
    px_to_seconds,
)

# slope -1920 makes one px one second: remaining = 300 - x.
AXIS = AxisCalibrationEvidence(
    slope=-1920.0,
    intercept=300.0,
    point_count=2,
    mean_absolute_error_seconds=0,
    max_absolute_error_seconds=0,
)
A, D = SlotState.alive, SlotState.dead


def _lamps(
    states: dict[float, SlotState], slot: str = "top_1"
) -> list[ReviewTimelineSample]:
    return [
        ReviewTimelineSample(
            sample_id=f"s{i}",
            capture_index=i,
            image_path=f"s{i}.png",
            image_sha256="0" * 64,
            clock_status="ok",
            cursor=CursorObservation(x=x),
            slots=[CursorSlotObservation(slot_id=slot, state=state, sample_id=f"s{i}")],
        )
        for i, (x, state) in enumerate(sorted(states.items()))
    ]


def _special(
    g: float, special_id: str = "ink_storm", slot: str = "top_1"
) -> SpecialUseEvent:
    return SpecialUseEvent(
        slot_id=slot,
        team_row=slot.split("_")[0],
        special_id=special_id,
        elapsed_estimate=300.0 - g,
        x_norm=max(g, 0.0) / 1920,
        center_x=g,
        score=0.9,
        runner_up_margin=0.5,
    )


def _labels(displays: list[str]) -> list[ReviewTimelineSample]:
    """Clocked frames without roster reads (``-M:SS`` countdown, ``+M:SS`` overtime)."""
    return [
        ReviewTimelineSample(
            sample_id=f"c{i}",
            capture_index=1000 + i,
            image_path=f"c{i}.png",
            image_sha256="0" * 64,
            clock_status="ok",
            displayed_clock=d,
            elapsed_seconds=-int(d[1:].split(":")[1]) - 60 * int(d[1:].split(":")[0]),
        )
        for i, d in enumerate(displays)
    ]


def _dataset(
    lamps: dict[float, SlotState],
    specials: list[SpecialUseEvent],
    *,
    ally_row: str | None = "top",
    markers: list[TimelineEventMarker] = (),
    episodes: list[PlayerDeathEpisodeEvidence] = (),
    labels: list[str] = (),
    axis: AxisCalibrationEvidence | None = AXIS,
) -> ReviewTimelineDataset:
    return ReviewTimelineDataset(
        source_recording_id="rec",
        samples=_lamps(lamps) + _labels(list(labels)),
        special_events=specials,
        match_history_events=list(markers),
        death_episodes=list(episodes),
        axis_calibration=axis,
        team_identity=TeamIdentity(ally_row=ally_row),
        coverage=CoverageReport(sample_count=len(lamps), clocked_sample_count=len(lamps)),
    )


def _one(ds: ReviewTimelineDataset) -> SpecialUseFact:
    facts = derive_special_use_facts(ds).facts
    assert len(facts) == 1
    return facts[0]


def _death(
    g: float, slot: str = "top_1", marker_id: str = "death-1"
) -> TimelineEventMarker:
    return TimelineEventMarker(
        marker_id=marker_id,
        team_row=slot.split("_")[0],
        kind="death",
        x_norm=g / 1920,
        elapsed_estimate=300.0 - g,
        player_slot_id=slot,
    )


def test_special_use_slot_alive_is_integration_valid() -> None:
    fact = _one(_dataset({90: A, 110: A}, [_special(100)]))
    assert fact.slot_state_at_use == "alive"
    assert fact.slot_state_reason is None
    assert fact.integration_valid is True
    assert fact.integration_blockers == []


def test_use_inside_own_transition_bracket_is_unknown() -> None:
    fact = _one(_dataset({90: A, 110: D}, [_special(100)]))
    assert (fact.slot_state_at_use, fact.slot_state_reason) == (
        "unknown",
        "near_roster_transition",
    )
    assert fact.own_transition_distance_s == 0.0
    assert fact.integration_blockers == ["slot_state_unknown"]


def test_special_use_slot_dead_is_kept_with_every_observation_field() -> None:
    fact = _one(_dataset({90: D, 110: D}, [_special(100)]))
    assert fact.slot_state_at_use == "dead"
    assert fact.integration_valid is False
    assert fact.integration_blockers == ["slot_dead_at_use"]
    assert (fact.slot_id, fact.special_id, fact.score, fact.runner_up_margin) == (
        "top_1",
        "ink_storm",
        0.9,
        0.5,
    )
    assert fact.remaining_estimate == 200.0
    assert fact.glyph_x == 100.0


def test_no_usable_lamp_frames_in_search_range() -> None:
    fact = _one(_dataset({10: A, 300: A}, [_special(100)]))
    assert (fact.slot_state_at_use, fact.slot_state_reason) == (
        "unknown",
        "no_lamp_frames",
    )
    assert fact.integration_blockers == ["slot_state_unknown"]


def test_stray_lamp_reads_keep_the_algorithm_result() -> None:
    # A slot that looks disconnected but has stray reads: no classifier overrides it.
    fact = _one(_dataset({95: A, 105: A}, [_special(100)]))
    assert fact.slot_state_at_use == "alive"
    assert fact.integration_valid is True


# Clock direction. Every value below is REMAINING time (the dataset field is
# misleadingly named ``elapsed_estimate``): later in the match = smaller value.
# Synthetic axis: remaining = 300 - x, so x = 300 - remaining.
def _x(remaining: float) -> float:
    return 300.0 - remaining


def _use_at_100(**kwargs: object) -> SpecialUseFact:
    return _one(_dataset({_x(105): A, _x(95): A}, [_special(_x(100))], **kwargs))


def test_direction_death_at_95_after_use_at_100_is_five_seconds_ahead() -> None:
    fact = _use_at_100(markers=[_death(_x(95), marker_id="d95")])
    assert fact.remaining_estimate == 100.0
    assert fact.seconds_to_next_own_death == 5.0
    assert fact.next_own_death_marker_id == "d95"


def test_direction_death_at_105_is_a_previous_death_and_ignored() -> None:
    fact = _use_at_100(markers=[_death(_x(105), marker_id="d105")])
    assert fact.seconds_to_next_own_death is None
    assert fact.next_own_death_marker_id is None


def test_direction_nearest_later_own_death_wins_other_slots_ignored() -> None:
    markers = [
        _death(_x(105), marker_id="d105"),
        _death(_x(80), marker_id="d80"),
        _death(_x(95), marker_id="d95"),
        _death(_x(98), slot="top_2", marker_id="other-slot"),
    ]
    fact = _use_at_100(markers=markers)
    assert (fact.next_own_death_marker_id, fact.seconds_to_next_own_death) == ("d95", 5.0)


def test_direction_recovery_at_120_before_use_at_100_is_twenty_seconds_ago() -> None:
    episodes = [PlayerDeathEpisodeEvidence(slot_id="top_1", next_alive_elapsed=120)]
    fact = _use_at_100(episodes=episodes)
    assert fact.seconds_since_own_recovery_observed == 20.0


def test_direction_recovery_at_90_is_a_future_recovery_and_ignored() -> None:
    episodes = [PlayerDeathEpisodeEvidence(slot_id="top_1", next_alive_elapsed=90)]
    fact = _use_at_100(episodes=episodes)
    assert fact.seconds_since_own_recovery_observed is None


def test_direction_latest_earlier_own_recovery_wins_other_slots_ignored() -> None:
    episodes = [
        PlayerDeathEpisodeEvidence(slot_id="top_1", next_alive_elapsed=150),
        PlayerDeathEpisodeEvidence(slot_id="top_1", next_alive_elapsed=120),
        PlayerDeathEpisodeEvidence(slot_id="top_1", next_alive_elapsed=90),
        PlayerDeathEpisodeEvidence(slot_id="top_2", next_alive_elapsed=105),
    ]
    fact = _use_at_100(episodes=episodes)
    assert fact.seconds_since_own_recovery_observed == 20.0


def test_without_own_death_or_recovery_fields_are_none() -> None:
    fact = _one(_dataset({90: A, 110: A}, [_special(100)], markers=[_death(50)]))
    assert fact.seconds_to_next_own_death is None
    assert fact.next_own_death_marker_id is None
    assert fact.seconds_since_own_recovery_observed is None
    assert fact.own_transition_distance_s is None


@pytest.mark.parametrize(
    ("special_id", "status", "blockers"),
    [
        ("unknown", "unknown_type", ["unknown_type"]),
        ("zipcaster", "unconfirmed_type", []),
        ("reefslider", "unconfirmed_type", []),
        ("killer_wail_5.1", "label_confirmed_type", []),
    ],
)
def test_identity_status(special_id: str, status: str, blockers: list[str]) -> None:
    fact = _one(_dataset({90: A, 110: A}, [_special(100, special_id)]))
    assert fact.identity_status == status
    assert fact.integration_blockers == blockers


def test_dead_slot_and_unknown_type_report_both_blockers() -> None:
    fact = _one(_dataset({90: D, 110: D}, [_special(100, "unknown")]))
    assert fact.integration_blockers == ["slot_dead_at_use", "unknown_type"]


def test_label_confirmed_list_is_frozen() -> None:
    assert len(LABEL_CONFIRMED_SPECIALS) == 12
    assert not LABEL_CONFIRMED_SPECIALS & {
        "reefslider",
        "splattercolor_screen",
        "super_chump",
        "tenta_missiles",
        "zipcaster",
    }


COUNTDOWN = ["-0:03", "-0:02", "-0:01"]
OVERTIME_TO_25 = [f"+0:{s:02d}" for s in range(1, 26)]


def test_overtime_use_inside_observed_overtime_has_overtime_phase() -> None:
    ds = _dataset({310: A, 330: A}, [_special(_x(-20))], labels=OVERTIME_TO_25)
    fact = _one(ds)
    assert fact.remaining_estimate == -20.0
    assert fact.phase == "overtime"


@pytest.mark.parametrize("remaining", [300.0, 150.0, 0.0])
def test_regulation_bounds_inclusive(remaining: float) -> None:
    g = _x(remaining)
    fact = _one(_dataset({g - 10: A, g + 10: A}, [_special(g)]))
    assert fact.phase == "regulation"


def test_special_use_in_countdown_is_refused() -> None:
    # Countdown labels are negative like overtime; without observed overtime
    # a negative use cannot be classified and must not become a fact.
    ds = _dataset({300: A, 305: A}, [_special(_x(-2))], labels=COUNTDOWN)
    with pytest.raises(OutsidePlayableInterval, match="no overtime label"):
        derive_special_use_facts(ds)


def test_special_use_before_go_is_refused() -> None:
    ds = _dataset({0: A, 10: A}, [_special(_x(302))], labels=COUNTDOWN)
    with pytest.raises(OutsidePlayableInterval, match="before GO"):
        derive_special_use_facts(ds)


def test_special_use_past_the_last_overtime_label_is_refused() -> None:
    labels = [f"+0:{s:02d}" for s in range(1, 11)]
    ok = _dataset({305: A, 315: A}, [_special(_x(-10.9))], labels=labels)
    assert _one(ok).phase == "overtime"
    late = _dataset({305: A, 315: A}, [_special(_x(-11.1))], labels=labels)
    with pytest.raises(OutsidePlayableInterval, match="past the last overtime"):
        derive_special_use_facts(late)


def test_one_refused_use_refuses_the_whole_artifact(tmp_path: Path) -> None:
    ds = _dataset(
        {90: A, 110: A, 300: A, 305: A},
        [_special(100), _special(_x(-2))],
        labels=COUNTDOWN,
    )
    (tmp_path / "review_timeline.json").write_text(ds.model_dump_json(), "utf-8")
    result = CliRunner().invoke(app, ["review", "special-facts", str(tmp_path)])
    assert result.exit_code == 1
    assert not (tmp_path / FACTS_FILENAME).exists()


# Own-transition distance. Lamp brackets are (x_a, x_b] between consecutive
# known frames with different states; one px is one second on AXIS.
BRACKET = {100: A, 110: A, 120: D, 130: D}


@pytest.mark.parametrize(
    ("g", "seconds"),
    [(105, 5.0), (115, 0.0), (120, 0.0), (125, 5.0), (110, 0.0)],
)
def test_transition_distance_geometry(g: float, seconds: float) -> None:
    # (110, 120] is open on the left: x=110 is the bracket edge, distance 0.
    fact = _one(_dataset(BRACKET, [_special(g)]))
    assert fact.own_transition_distance_s == seconds


def test_transition_distance_nearest_of_several_wins() -> None:
    lamps = {100: A, 110: D, 150: D, 160: A}
    assert _one(_dataset(lamps, [_special(140)])).own_transition_distance_s == 10.0
    assert _one(_dataset(lamps, [_special(120)])).own_transition_distance_s == 10.0


def test_transition_distance_without_transition_is_none() -> None:
    fact = _one(_dataset({90: A, 110: A}, [_special(100)]))
    assert fact.own_transition_distance_s is None


def test_transition_distance_without_axis_is_none_but_state_stands() -> None:
    fact = _one(_dataset(BRACKET, [_special(105)], axis=None))
    assert fact.own_transition_distance_s is None
    assert fact.slot_state_at_use == "alive"


def test_seconds_per_px_uses_the_axis_mapping() -> None:
    half = AXIS.model_copy(update={"slope": -960.0})
    facts = derive_special_use_facts(_dataset(BRACKET, [_special(105)], axis=half))
    assert facts.seconds_per_px == 0.5
    assert facts.facts[0].own_transition_distance_s == 2.5
    assert px_to_seconds(half, 10) == abs(half.elapsed_at(10 / 1920) - half.elapsed_at(0))


def test_frame_width_other_than_1920_is_refused() -> None:
    ds = _dataset({90: A, 110: A}, [_special(100)])
    ds.samples[0].cursor.x_norm = 90 / 1280
    with pytest.raises(ValueError, match="x_norm"):
        derive_special_use_facts(ds)


IMPORTS = sorted(
    Path(__file__)
    .resolve()
    .parents[1]
    .glob("analysis/review_validation/heldout2/09-*/review_timeline.json")
)


@pytest.mark.skipif(not IMPORTS, reason="gate D imports not present")
def test_real_imports_glyph_clock_is_the_axis_mapping() -> None:
    for path in IMPORTS:
        ds = ReviewTimelineDataset.model_validate_json(path.read_text("utf-8"))
        axis = ds.axis_calibration
        assert axis is not None
        for e in ds.special_events:
            assert axis.elapsed_at(e.x_norm) == pytest.approx(e.elapsed_estimate)
        facts = derive_special_use_facts(ds)
        assert facts.seconds_per_px == pytest.approx(abs(axis.slope) / 1920)


@pytest.mark.parametrize(
    ("ally_row", "slot", "side"),
    [
        (None, "top_1", "unknown"),
        ("top", "top_1", "ally"),
        ("bottom", "top_1", "opponent"),
    ],
)
def test_side_from_ally_row(ally_row: str | None, slot: str, side: str) -> None:
    fact = _one(_dataset({90: A, 110: A}, [_special(100, slot=slot)], ally_row=ally_row))
    assert fact.side == side


def test_facts_follow_dataset_order_and_index() -> None:
    ds = _dataset({90: A, 110: A, 190: A, 210: A}, [_special(200), _special(100)])
    facts = derive_special_use_facts(ds).facts
    assert [f.special_index for f in facts] == [0, 1]
    assert [f.glyph_x for f in facts] == [200.0, 100.0]


FORBIDDEN = re.compile(
    r"user|wasted|should|good|bad|trade|won|lost|caused|fight|fact_valid|quality",
    re.IGNORECASE,
)


def _vocabulary() -> list[str]:
    words = list(SpecialUseFact.model_fields) + list(SpecialUseFacts.model_fields)
    for alias in (
        special_fusion.SlotStateValue,
        special_fusion.SlotStateReason,
        special_fusion.IdentityStatus,
        special_fusion.IntegrationBlocker,
    ):
        words += list(get_args(alias))
    for name in ("side", "phase", "team_row"):
        words += list(get_args(SpecialUseFact.model_fields[name].annotation))
    return words


def test_contract_vocabulary_has_no_forbidden_wording() -> None:
    hits = [w for w in _vocabulary() if FORBIDDEN.search(w)]
    assert hits == []


def test_fusion_emits_no_game_events_and_imports_only_review() -> None:
    tree = ast.parse(Path(special_fusion.__file__).read_text("utf-8"))
    modules = [
        n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    ]
    package = [m for m in modules if m.startswith("splatoon3_ai_coach")]
    assert package and all(m.startswith("splatoon3_ai_coach.review.") for m in package)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {"GameEvent", "GameEventType", "SPECIAL_USED", "SPECIAL_READY"}


def test_cli_writes_facts_and_never_rewrites_the_timeline(tmp_path: Path) -> None:
    ds = _dataset({90: A, 110: A}, [_special(100)])
    timeline = tmp_path / "review_timeline.json"
    timeline.write_text(ds.model_dump_json(), "utf-8")
    before = timeline.read_bytes()
    result = CliRunner().invoke(app, ["review", "special-facts", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert timeline.read_bytes() == before
    payload = json.loads((tmp_path / FACTS_FILENAME).read_text("utf-8"))
    assert payload["clock"] == "review_remaining"
    assert len(payload["source_dataset_sha256"]) == 64
    assert payload["facts"][0]["integration_valid"] is True
