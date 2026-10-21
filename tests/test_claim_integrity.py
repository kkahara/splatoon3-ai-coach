"""Claim-integrity rules R5, R7, R8, R9: one passing and one failing case each."""

from __future__ import annotations

from typing import Any

from splatoon3_ai_coach.coach.claim_integrity import claim_integrity_hits
from splatoon3_ai_coach.coach.claim_validation import validate_assessment


def _view(**overrides: Any) -> dict[str, Any]:
    view: dict[str, Any] = {
        "claim_basis": [
            {"claim_id": "death_final_30s", "level": "A"},
            {
                "claim_id": "process_map_after_respawn",
                "level": "B",
                "coachable": True,
                "rec_template_ids": ["rec_map_after_respawn"],
            },
        ],
        "importance": {"active_factor_ids": ["death_final_30s"]},
        "special": {"state_at_anchor": "not_observed"},
        "map": {"map_check_before_death": {"status": "not_observed"}},
        "clock": {"seconds_remaining": 7, "display": "0:07"},
    }
    view.update(overrides)
    return view


def _checks(view: dict[str, Any] | None = None, **fields: Any) -> set[str]:
    assessment = {"assessment": "", "recommendations": [], "limitations": [], **fields}
    hits = claim_integrity_hits(assessment, _view() if view is None else view)
    return {f"{hit['rule']}:{hit['check']}" for hit in hits}


def test_r5_special_state_words_must_match_the_view() -> None:
    hidden = "The special gauge was not visible just before the death."
    assert _checks(assessment=hidden) == set()
    assert "R5:special_state" in _checks(assessment="Your special was not ready.")
    ready_view = _view(special={"state_at_anchor": "ready"})
    assert _checks(ready_view, assessment="Your special was still charged.") == set()
    not_ready = "Your special was not ready."
    assert "R5:special_state" in _checks(ready_view, assessment=not_ready)


def test_r5_map_absence_needs_not_observed_and_never_recent() -> None:
    assert _checks(assessment="No map check was observed before this death.") == set()
    unknown = _view(map={"map_check_before_death": {"status": "unknown"}})
    assert "R5:map_absence" in _checks(unknown, assessment="You did not check the map.")
    observed = _view(map={"map_check_before_death": {"status": "observed"}})
    assert "R5:map_recency" in _checks(
        observed, assessment="You checked the map just before the death."
    )


def test_r12_video_seconds_never_in_player_text() -> None:
    assert _checks(assessment="You were out of play for 9.0 seconds.") == set()
    assert _checks(assessment="That was 14 game-clock seconds.") == set()
    worded = "Your previous death was 18.0 video seconds earlier."
    assert "R12:video_seconds_wording" in _checks(assessment=worded)
    assert "R12:video_seconds_wording" in _checks(
        recommendations=["Wait a few video-seconds before re-engaging."]
    )
    assert _checks(limitations=["Durations are in video seconds."]) == set()
    no_view = claim_integrity_hits({"assessment": worded}, None)
    assert {hit["rule"] for hit in no_view} == {"R12"}


def _r14(**fields: Any) -> set[str]:
    return {check for check in _checks(**fields) if check.startswith("R14:")}


def test_r14_recommendations_stay_out_of_the_assessment() -> None:
    rec = "After being splatted, take a moment to reset before re-engaging."
    facts = (
        "Your previous death was 14.5 seconds earlier. Back-to-back deaths keep "
        "you out of play for longer. In the last 30 seconds, time spent "
        "respawning is a large share of the time left."
    )
    assert _r14(assessment=facts, recommendations=[rec]) == set()
    assert _r14(recommendations=[rec]) == set()
    repeated = f"{facts} {rec}"
    assert "R14:recommendation_repeated" in _checks(
        assessment=repeated, recommendations=[rec]
    )
    statement_rec = "In the last 30 seconds, a respawn uses up part of the time left."
    assert "R14:recommendation_repeated" in _checks(
        assessment=f"This death happened with 0:20 left. {statement_rec}",
        recommendations=[statement_rec],
    )
    for advice in (
        "Weigh whether a fight is worth the respawn time before committing.",
        "After a splat, decide where you are going next before you move up.",
        "In the last 30 seconds, remember that respawn time uses part of the time.",
        "You should refill your ink first.",
    ):
        assert "R14:advice_in_assessment" in _checks(assessment=advice), advice
    claim = [{"claim_id": "death_final_30s", "level": "A", "sentence": rec}]
    assert "R14:recommendation_repeated" in _checks(claims=claim, recommendations=[rec])


def test_r5_clock_window_needs_its_factor() -> None:
    assert _checks(assessment="This death came in the final 30 seconds.") == set()
    assert "R5:clock_window" in _checks(assessment="It was in the first 30 seconds.")


def test_r7_claims_must_be_in_basis_without_upgrading() -> None:
    ok = [{"claim_id": "death_final_30s", "level": "A", "sentence": "Final 30 seconds."}]
    assert _checks(claims=ok, selected_claim_ids=["death_final_30s"]) == set()
    assert "R7:unknown_claim" in _checks(selected_claim_ids=["death_while_outnumbered"])
    upgraded = [{"claim_id": "process_map_after_respawn", "level": "A"}]
    assert "R7:level_upgrade" in _checks(claims=upgraded)


def test_r7_views_without_claim_basis_are_not_checked() -> None:
    legacy = _view()
    del legacy["claim_basis"]
    assert _checks(legacy, selected_claim_ids=["anything"]) == set()


def test_r7_cited_values_must_equal_the_view() -> None:
    view = _view()
    good = {"assessment": "x", "evidence_used": ["clock.seconds_remaining = 7"]}
    bad = {"assessment": "x", "evidence_used": ["special.state_at_anchor = ready"]}
    assert validate_assessment(good, view)["integrity_hits"] == []
    checks = {h["check"] for h in validate_assessment(bad, view)["integrity_hits"]}
    assert checks == {"value_mismatch"}


def _value_checks(view: dict[str, Any], cited: str) -> set[str]:
    hits = validate_assessment({"assessment": "x", "evidence_used": [cited]}, view)
    return {h["check"] for h in hits["integrity_hits"]}


def test_r7_cited_value_formats_seen_in_model_output() -> None:
    view = _view(
        game_clock_samples=[
            {"label": "death", "seconds_remaining": 29, "display": "0:29"}
        ],
        durations={"time_since_previous_death": {"value": "14.5s"}},
        death={"death_to_active_again": {"value": 8.5}},
    )
    view["game_clock_samples"][0]["source_path"] = "game_clock_samples[label=death]"
    matching = [
        "game_clock_samples[0].seconds_remaining = 29",
        'game_clock_samples[0].display = "0:29"',
        "durations.time_since_previous_death.value = 14.5",
        "death.death_to_active_again.value = 8.5 seconds",
        "clock.seconds_remaining = 7 (source_path: game_clock_samples[label=death])",
        'clock.seconds_remaining = 7; clock.display = "0:07"',
    ]
    for cited in matching:
        assert _value_checks(view, cited) == set(), cited
    wrong = [
        "game_clock_samples[0].seconds_remaining = 28",
        'game_clock_samples[0].display = "0:28"',
        "durations.time_since_previous_death.value = 15.5",
        "death.death_to_active_again.value = 9 seconds",
        "clock.seconds_remaining = 8 (source_path: game_clock_samples[label=death])",
    ]
    for cited in wrong:
        assert _value_checks(view, cited) == {"value_mismatch"}, cited
    for cited in (
        "game_clock_samples[label=death].seconds_remaining = 29",
        'game_clock_samples[label=death].display = "0:28"',
    ):
        report = validate_assessment({"assessment": "x", "evidence_used": [cited]}, view)
        assert [r["text"] for r in report["unresolved_evidence_paths"]] == [cited]
        assert report["integrity_hits"] == []


def test_r8_no_internal_identifiers_in_player_text() -> None:
    assert _checks(assessment="Two deaths 18.0s apart.") == set()
    assert "R8:internal_identifier" in _checks(
        assessment="This was flagged by death_while_ahead_in_numbers."
    )
    assert "R8:internal_identifier" in _checks(
        recommendations=["See special.state_at_anchor."]
    )


def test_r9_unknown_must_match_the_view() -> None:
    assert _checks(limitations=["Special state was not visible."]) == set()
    visible = _view(special={"state_at_anchor": "not_ready"})
    assert "R9:false_unknown_special" in _checks(
        visible, limitations=["Special readiness is not known."]
    )
    assert "R9:false_unknown_clock" in _checks(
        limitations=["The match phase cannot be determined."]
    )
    no_clock = _view(clock=None)
    phase = ["The match phase cannot be determined."]
    assert _checks(no_clock, limitations=phase) == set()


def test_integrity_hits_badge_but_do_not_block() -> None:
    report = validate_assessment({"assessment": "Your special was not ready."}, _view())
    assert report["validation_status"] == "contains_unverified_wording"
    assert report["blocked"] is False


def test_direct_unhedged_a_level_sentence_passes_every_rule() -> None:
    """Over-caution guard: plain observed facts need no hedge."""
    assessment = {
        "assessment": (
            "This death came in the final 30 seconds, with 0:07 left on the timer. "
            "No map check was observed before this death."
        ),
        "recommendations": [
            "After you respawn, take a quick look at the map before rejoining."
        ],
        "claims": [{"claim_id": "death_final_30s", "level": "A", "sentence": "x"}],
    }
    report = validate_assessment(assessment, _view())
    assert report["validation_status"] == "validated", report


def test_r10_recommendations_need_a_grounded_template() -> None:
    grounded = ["After you respawn, take a quick look at the map before rejoining."]
    assert _checks(recommendations=grounded) == set()
    assert "R10:no_template" in _checks(recommendations=["Play safer when outnumbered."])
    no_map = _view(claim_basis=[{"claim_id": "death_final_30s", "level": "A"}])
    assert "R10:ungrounded_template" in _checks(no_map, recommendations=grounded)


def test_r11_nothing_notable_fails_when_a_coachable_claim_exists() -> None:
    coachable = _view(
        claim_basis=[{"claim_id": "death_final_30s", "level": "A", "coachable": True}]
    )
    supported = "This death came in the final 30 seconds."
    assert _checks(coachable, assessment=supported) == set()
    missed = "No coaching claim is supported for this death."
    assert "R11:missed_supported_claim" in _checks(coachable, assessment=missed)
    timer = {"claim_id": "timer_sample_exists", "level": "A", "coachable": False}
    nothing = _view(claim_basis=[timer])
    assert _checks(nothing, assessment=missed) == set()


def test_r11_no_recommendation_message_is_not_a_missed_claim() -> None:
    coachable = _view(
        claim_basis=[{"claim_id": "death_final_30s", "level": "A", "coachable": True}]
    )
    text = "No recommendation supported by the available evidence."
    assert _checks(coachable, assessment=text) == set()
