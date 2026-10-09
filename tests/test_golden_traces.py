"""Golden traces B, C and D: every layer from stored readings to the public page.

Fixtures are trimmed real manifests written by ``tools/diagnostics/trace_fixture.py``.
Each trace runs the production path into ``tmp_path``: analysis pipeline
(scenarios + ScenarioContext), the ``coach-inputs`` command (CoachInput,
coaching unit, CoachLlmView) and the public ``load_result``. Each test
crosses layer boundaries so a value can't be right in one layer and wrong in
the next.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from public_site.models import CoachingMoment
from public_site.results import load_result
from typer.testing import CliRunner

from splatoon3_ai_coach.analysis.pipeline import write_scenarios
from splatoon3_ai_coach.cli import app
from splatoon3_ai_coach.coach.artifacts import (
    PrototypeIndex,
    PrototypeIndexEntry,
    read_coaching_index,
    write_assessment_artifacts,
    write_prototype_index,
)
from splatoon3_ai_coach.coach.claim_validation import validate_assessment
from splatoon3_ai_coach.coach.llm_client import CoachingAssessment
from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import VisionLanguage
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.media.vision_manifest import load_vision_manifest

FIXTURES = Path(__file__).parent / "fixtures" / "traces"
MODEL = "gpt-oss:20b"


@dataclass(frozen=True)
class Trace:
    """All layers for one death episode, built by production code."""

    fixture: dict[str, Any]
    manifest: dict[str, Any]
    context: dict[str, Any]
    coach_input: dict[str, Any]
    coaching: dict[str, Any]
    view: dict[str, Any]
    moment: CoachingMoment

    def active_factors(self) -> list[str]:
        return [f["factor_id"] for f in self.coaching["factors"] if f["active"]]

    def public_text(self) -> str:
        m = self.moment
        lines = [m.heading or "", m.until_active_again or "", m.recovery_context or ""]
        lines += m.context + m.statements + [mark.offset_label or "" for mark in m.marks]
        return "\n".join(lines)


def _build(tmp: Path, name: str, death: float, *, with_model_output: bool) -> Trace:
    fixture = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    analysis = tmp / name
    analysis.mkdir()
    (analysis / "vision_manifest.json").write_text(
        json.dumps(fixture["manifest"]), encoding="utf-8"
    )
    manifest = load_vision_manifest(analysis)
    config = load_config(default_config_path())
    config.vision.language = VisionLanguage(manifest.analysis.language)
    write_scenarios(manifest.game_events, config, analysis, manifest=manifest)
    result = CliRunner().invoke(app, ["coach-inputs", str(analysis)])
    assert result.exit_code == 0, result.stdout

    scenario_id = f"death_episode:{death:.3f}"
    inputs = analysis / "coach_inputs"
    entry = read_coaching_index(inputs).by_scenario_id(scenario_id)
    assert entry is not None
    if with_model_output:
        _write_model_output(analysis, entry, fixture["model_output"])
    contexts = json.loads((analysis / "scenario_contexts.json").read_text())
    (moment,) = [m for m in load_result(analysis).moments if m.video_time == death]
    return Trace(
        fixture=fixture,
        manifest=fixture["manifest"],
        context=next(c for c in contexts if c["scenario_id"] == scenario_id),
        coach_input=_doc(inputs, entry.coach_input_json),
        coaching=_doc(inputs, entry.coaching_json),
        view=_doc(inputs, entry.llm_view_json),
        moment=moment,
    )


def _doc(folder: Path, name: str | None) -> dict[str, Any]:
    assert name is not None
    return json.loads((folder / name).read_text(encoding="utf-8"))


def _write_model_output(analysis: Path, entry: Any, output: dict[str, Any]) -> None:
    prototype = analysis / "coach_prototype"
    prototype.mkdir()
    written = write_assessment_artifacts(
        out_dir=prototype,
        safe_id=entry.safe_id,
        model_label=MODEL,
        assessment=CoachingAssessment.model_validate(output),
        raw_text=json.dumps(output),
        flags={"hit_count": 0},
        skipped_llm=False,
    )
    known = PrototypeIndexEntry.model_fields
    fields = {k: v for k, v in entry.model_dump().items() if k in known}
    index_entry = PrototypeIndexEntry(
        **fields, outputs=[written], primary_output_json=written.output_json
    )
    write_prototype_index(
        prototype, PrototypeIndex(primary_model=MODEL, entries=[index_entry])
    )


@pytest.fixture(scope="module")
def trace_b(tmp_path_factory: pytest.TempPathFactory) -> Trace:
    return _build(tmp_path_factory.mktemp("b"), "9d47_b", 72.5, with_model_output=True)


@pytest.fixture(scope="module")
def trace_c(tmp_path_factory: pytest.TempPathFactory) -> Trace:
    return _build(tmp_path_factory.mktemp("c"), "enprev_c", 37.5, with_model_output=False)


@pytest.fixture(scope="module")
def trace_d(tmp_path_factory: pytest.TempPathFactory) -> Trace:
    return _build(tmp_path_factory.mktemp("d"), "enprev_d", 95.0, with_model_output=False)


def _lifecycle_transitions(manifest: dict[str, Any]) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    for snap in manifest["state_snapshots"]:
        if not out or out[-1][1] != snap["player_lifecycle"]:
            out.append((snap["timestamp"], snap["player_lifecycle"]))
    return out


def _events(
    manifest: dict[str, Any], start: float, end: float
) -> list[tuple[str, float]]:
    return [
        (e["event_type"], e["start_time"])
        for e in manifest["game_events"]
        if start <= e["start_time"] <= end
    ]


def _special_reading(manifest: dict[str, Any], t: float) -> dict[str, Any]:
    frame = next(f for f in manifest["frame_results"] if f["timestamp"] == t)
    det = next(d for d in frame["detections"] if d["detector_name"] == "special_gauge")
    return det["reading"]


# --- Trace B: 9d47 death 72.5 (normal playback, live respawn map) ------------


def test_trace_b_readings_and_lifecycle(trace_b: Trace) -> None:
    assert _lifecycle_transitions(trace_b.manifest) == [
        (66.0, "alive"),
        (72.5, "dead"),
        (76.0, "countdown"),
        (80.0, "respawned"),
        (80.5, "awaiting_control"),
        (82.0, "alive"),
    ]
    assert _events(trace_b.manifest, 66.0, 84.0) == [
        ("death", 72.5),
        ("map_overlay", 75.5),
        ("map_overlay", 77.0),
        ("map_overlay", 78.0),
        ("map_overlay", 79.0),
        ("respawn", 80.0),
        ("map_overlay", 80.0),
        ("active_again", 82.0),
    ]
    assert _special_reading(trace_b.manifest, 80.5)["ready"] is True
    assert _special_reading(trace_b.manifest, 81.0)["ready"] is True


def test_trace_b_map_not_observed_is_a_valid_negative(trace_b: Trace) -> None:
    ctx_map = trace_b.context["map"]
    assert ctx_map["map_check_status"] == "not_observed"
    assert ctx_map["map_check_before_death"] is False
    assert ctx_map["map_coverage"]["status"] == "sufficient"
    assert ctx_map["map_coverage"]["respawns_with_overlay"] == 6
    assert ctx_map["episode_map_observability"]["status"] == "sufficient"
    assert ctx_map["map_checked_while_dead"] is True
    assert trace_b.coach_input["primary_context"]["map"]["map_check_status"] == (
        "not_observed"
    )
    assert "death_map_overlay_before_false" in trace_b.active_factors()
    block = trace_b.view["map"]["map_check_before_death"]
    assert block["status"] == "not_observed"
    assert block["coverage"]["match"] == "sufficient"
    assert "No map check observed before death" in trace_b.moment.context
    assert trace_b.moment.recovery_context == (
        "The player checked the map during recovery."
    )


def test_trace_b_special_not_ready_with_last_ready(trace_b: Trace) -> None:
    """82.0/82.5 s read ready from the prompt alone (prompt 0.83/0.65) at fill
    0.38, with the dial visibly about a third lit and no full ring since the
    respawn, so they are uncorroborated. 71.0 s (fill 0.90) stays last ready.
    """
    special = trace_b.context["special"]
    nearest = special["nearest_before_anchor"]
    assert (nearest["video_time"], nearest["state"]) == (72.5, "not_ready")
    assert round(nearest["fill_fraction"], 2) == 0.62
    last = special["last_ready_before_anchor"]
    assert (last["video_time"], round(last["fill_fraction"], 2)) == (71.0, 0.90)
    by_time = {r["video_time"]: r for r in special["observations"]}
    for t in (80.5, 81.0, 81.5):
        assert by_time[t]["lifecycle"] == "awaiting_control"
        assert by_time[t]["coaching_valid"] is False
        assert by_time[t]["coaching_invalid_reason"] == "lifecycle"
    for t in (82.0, 82.5):
        assert by_time[t]["lifecycle"] == "alive"
        assert round(by_time[t]["fill_fraction"], 2) == 0.38
        assert by_time[t]["coaching_valid"] is False
        assert by_time[t]["coaching_invalid_reason"] == "uncorroborated_prompt_ready"
    assert 82.0 not in [o["video_time"] for o in special["ready_onsets"]]
    assert "death_special_ready" not in trace_b.active_factors()
    assert trace_b.view["special"]["last_ready"]["video_time"] == 71.0
    assert trace_b.view["special"]["state_at_anchor"] == "not_ready"
    text = trace_b.public_text()
    assert "Special was not ready just before the death." in trace_b.moment.context
    assert "special was used" not in text.lower()


def test_trace_b_clock_and_durations(trace_b: Trace) -> None:
    assert trace_b.view["clock"]["display"] == "4:10"
    assert trace_b.coach_input["playback_clock"]["status"] == "normal"
    assert trace_b.coach_input["durations"]["death_to_active_again"]["value"] == 9.5
    assert trace_b.moment.heading == "Death — 4:10 remaining"
    assert trace_b.moment.until_active_again == "9.5s out of play"


def test_trace_b_gpt_oss_text_is_blocked(trace_b: Trace) -> None:
    """"This lack of map awareness may have contributed to the splat" is a
    hedged causal claim (R1); the public page shows only the evidence lines."""
    assessment = CoachingAssessment.model_validate(trace_b.fixture["model_output"])
    report = validate_assessment(assessment.model_dump(mode="json"), trace_b.view)
    assert report["validation_status"] == "blocked_unsupported_claim"
    assert report["blocked"] is True
    hits = report["claim_rule_hits"]
    rules = {hit["rule"] for hit in hits if hit["counts_as_violation"]}
    assert "R1" in rules
    assert report["causal_claim_hits"]
    # Map-after-respawn advice is grounded here (map not_observed, coverage
    # sufficient), so the legacy map-advice rule yields to R10 templates;
    # "Review map usage before engaging" matches no template.
    assert report["unsupported_recommendation_hits"] == []
    checks = {(h["rule"], h["check"]) for h in report["integrity_hits"]}
    assert ("R10", "no_template") in checks
    assert ("R8", "internal_identifier") in checks
    (unresolved,) = report["unresolved_evidence_paths"]
    assert unresolved["path"] == "primary_context.death_episode.time"
    assert unresolved["reason"] == "unknown_key"
    assert trace_b.moment.assessment_status == "blocked_unsupported_claim"
    assert trace_b.moment.coaching_state == "blocked"
    assert trace_b.moment.assessment is None
    assert trace_b.moment.context


# --- Trace C: enprev death 37.5 (replay at ~1.5x, no live respawn map) -------


def test_trace_c_playback_rate_and_out_of_play(trace_c: Trace) -> None:
    playback = trace_c.coach_input["playback_clock"]
    assert playback["status"] == "rate_mismatch"
    assert playback["game_seconds_per_video_second"] == pytest.approx(1.5, abs=0.1)
    out = trace_c.coach_input["durations"]["death_to_active_again"]
    assert (out["clock"], out["value"], out["video_seconds"]) == ("game", 14.0, 9.0)
    assert trace_c.view["death"]["death_to_active_again"]["value"] == 14.0
    assert trace_c.view["clock"]["display"] == "4:34"
    assert trace_c.moment.heading == "Death — 4:34 remaining"
    assert trace_c.moment.until_active_again == "14s out of play (game clock)"
    assert "9.0s" not in trace_c.public_text()


def test_trace_c_special_not_observed_not_not_ready(trace_c: Trace) -> None:
    nearest = trace_c.context["special"]["nearest_before_anchor"]
    assert (nearest["video_time"], nearest["state"]) == (37.5, "not_observed")
    assert trace_c.context["special"]["state_at_anchor"] == "not_observed"
    assert trace_c.view["special"]["state_at_anchor"] == "not_observed"
    assert "death_special_ready" not in trace_c.active_factors()
    assert "Special: not ready" not in trace_c.public_text()
    assert "Special was not ready" not in trace_c.public_text()
    assert (
        "The special gauge was not visible just before the death."
        in trace_c.moment.context
    )


def test_trace_c_engagement_relation_and_splat_gap(trace_c: Trace) -> None:
    death = trace_c.context["death_episode"]
    assert trace_c.context["relations"]["preceded_by_engagement_id"] is None
    assert death["time_since_previous_splat"] == 4.5
    assert trace_c.view["death"]["time_since_previous_splat"]["video_seconds"] == 4.5
    assert trace_c.view["death"]["time_since_previous_splat"]["clock"] == "game"
    (splat,) = [m for m in trace_c.moment.marks if m.label == "splat"]
    assert (splat.video_time, splat.offset_clock) == (33.0, "game")
    assert splat.offset_label == "7s before (game clock)"


def test_trace_c_roster_follows_the_plausibility_rule(trace_c: Trace) -> None:
    """Allies 4→2 at 29.0, →1 at 31.5, →3 at 34.0, →4 at 36.5: every rise
    comes ≥3 video seconds after enough earlier drops, so the queue rule keeps
    the roster. The rule's actual output is asserted; it is not special-cased."""
    players = trace_c.context["players"]
    assert players["unreliable_intervals"] == []
    assert players["at_death"]["ally_alive_count"] == 4
    assert players["at_death"]["opponent_alive_count"] == 3


def test_trace_c_coaching_withholds_roster_while_capability_is_off(
    trace_c: Trace,
) -> None:
    """The analysis keeps 4v3, but coach.roster_evidence defaults to off."""
    assert trace_c.coach_input["roster_evidence"] is False
    assert trace_c.coach_input["primary_context"]["players"] is None
    assert trace_c.coach_input["player_count_window"] == []
    assert trace_c.view["roster"] is None
    assert trace_c.view["roster_evidence"] is False
    assert trace_c.view["death"]["numbers_state_at_death"] is None
    codes = [limit["code"] for limit in trace_c.view["evidence_limits"]]
    assert "roster_evidence_disabled" in codes
    assert not any("oster" in line for line in trace_c.moment.context)
    factors = trace_c.active_factors()
    assert not any("_numbers" in f or "outnumbered" in f for f in factors)


def test_trace_c_map_unknown_on_replay(trace_c: Trace) -> None:
    ctx_map = trace_c.context["map"]
    assert ctx_map["map_check_status"] == "unknown"
    assert ctx_map["map_check_unknown_reason"] == "match_coverage_insufficient"
    assert ctx_map["map_check_before_death"] is None
    assert "death_map_overlay_before_false" not in trace_c.active_factors()
    assert "No map check observed" not in trace_c.public_text()


# --- Trace D: enprev death 95.0 (map coverage insufficient) ------------------


def test_trace_d_map_unknown_everywhere(trace_d: Trace) -> None:
    assert _events(trace_d.manifest, 88.0, 108.0) == [
        ("death", 95.0),
        ("respawn", 100.0),
        ("active_again", 104.5),
    ]
    ctx_map = trace_d.context["map"]
    assert ctx_map["map_coverage"]["status"] == "insufficient"
    assert ctx_map["map_coverage"]["respawns_with_overlay"] == 0
    assert ctx_map["map_check_status"] == "unknown"
    assert ctx_map["map_check_before_death"] is None
    assert ctx_map["episode_map_observability"]["segment_start"] == 46.5
    assert trace_d.coach_input["primary_context"]["map"]["map_check_status"] == "unknown"
    assert "death_map_overlay_before_false" not in trace_d.active_factors()
    block = trace_d.view["map"]["map_check_before_death"]
    assert (block["status"], block["unknown_reason"]) == (
        "unknown",
        "match_coverage_insufficient",
    )
    codes = [limit["code"] for limit in trace_d.view["evidence_limits"]]
    assert "map_check_unobservable" in codes
    assert "No map check observed" not in trace_d.public_text()
    assert "Map-check evidence was unavailable for this video." in trace_d.moment.context


def test_trace_d_bounded_marks_use_game_clock(trace_d: Trace) -> None:
    marks = trace_d.moment.marks
    for mark in marks:
        assert abs(mark.video_time - 95.0) <= 15.0
    (splat,) = [m for m in marks if m.label == "splat"]
    assert (splat.video_time, splat.offset_clock) == (84.0, "game")
    assert splat.offset_label == "17s before (game clock)"
    assert trace_d.moment.until_active_again == "15s out of play (game clock)"


def test_ring_corroboration_keeps_c_and_d_last_ready(
    trace_c: Trace, trace_d: Trace
) -> None:
    """Full-ring readings (fill 0.90) before each death still name last ready."""
    for trace, expected in ((trace_c, 35.5), (trace_d, 92.0)):
        last = trace.context["special"]["last_ready_before_anchor"]
        assert (last["video_time"], round(last["fill_fraction"], 2)) == (expected, 0.90)
