"""Design-only probe of existing roster fusion/projection; no new representation.

Run from the repository root with .venv/bin/python. Prints synthetic results;
never decodes video, reads analysis artifacts, or writes production data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from splatoon3_ai_coach.analysis.player_count_series import observations_from_snapshots
from splatoon3_ai_coach.analysis.scenario_context import (
    DeathEpisodeContext,
    ScenarioContext,
)
from splatoon3_ai_coach.analysis.scenario_evidence import build_players_evidence
from splatoon3_ai_coach.analysis.scenario_models import Scenario
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.coach.death_importance import pre_death_roster
from splatoon3_ai_coach.config.models import (
    PlayerCountDetectorConfig,
    StateFusionConfig,
    TimerDetectorConfig,
)
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.vision.ids import make_frame_id, make_result_id
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    PlayerCountReading,
    TimerReading,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.state import fuse_game_state


@dataclass(frozen=True)
class Case:
    """Synthetic recording interval and whether the middle is readable."""

    name: str
    first: float
    resumed: float
    death: float
    hidden_middle: bool


def frame(timestamp: float, hidden: bool) -> VisionFrameResult:
    """Make timestamped roster/timer readings without running a detector."""
    analysis_id = "synthetic-roster-gap-design"
    frame_index = int(timestamp * 60)
    roster = PlayerCountReading(
        roster_visible=not hidden,
        ally_dead_slots=() if hidden else (1,),
        opponent_dead_slots=() if hidden else (1,),
    )
    remaining = 280 - int(timestamp)
    timer = TimerReading(
        display=f"{remaining // 60}:{remaining % 60:02d}", seconds_remaining=remaining
    )
    detections = []
    for name, reading, confidence in (
        ("player_count", roster, 0.0 if hidden else 0.95),
        ("timer", timer, 0.95),
    ):
        version = f"{name}@synthetic"
        detections.append(
            DetectorResult(
                id=make_result_id(
                    analysis_id, frame_index, timestamp, name, version, reading
                ),
                detector_name=name,
                detector_version=version,
                confidence=confidence,
                reading=reading,
            )
        )
    return VisionFrameResult(
        frame_id=make_frame_id(analysis_id, frame_index, timestamp),
        timestamp=timestamp,
        source="cadence",
        source_frame_index=frame_index,
        source_pts=frame_index,
        source_time_base_num=1,
        source_time_base_den=60,
        detections=detections,
    )


def probe(case: Case) -> dict[str, object]:
    """Exercise unchanged production functions and assert their current behavior."""
    times = [
        case.first + index / 2
        for index in range(int((case.resumed + 0.5 - case.first) * 2) + 1)
    ]
    frames = [
        frame(t, case.hidden_middle and case.first + 0.5 < t < case.resumed)
        for t in times
    ]
    snapshots = fuse_game_state(
        frames,
        TimerDetectorConfig(roi=(0, 0, 1, 1), template_dir=default_config_path().parent),
        StateFusionConfig(),
        player_count_config=PlayerCountDetectorConfig(confirm_readings=2, hold_seconds=1),
    )
    players = build_players_evidence(
        snapshots,
        window_start=case.first,
        window_end=case.death + 2,
        anchor=case.death,
        death_time=case.death,
        max_gap_seconds=1,
        min_respawn_seconds=3,
        plausibility_window_seconds=3,
    )
    assert players is not None and players.at_death is not None
    scenario = Scenario(
        scenario_id=f"death_episode:{case.death:.3f}",
        scenario_type="death_episode",
        start_time=case.death,
        end_time=case.death,
        confidence=1,
        outcome="incomplete",
        event_ids=[],
    )
    coach_input = CoachInput(
        primary_scenario=scenario,
        primary_context=ScenarioContext(
            scenario_id=scenario.scenario_id,
            players=players,
            death_episode=DeathEpisodeContext(death_time=case.death),
        ),
    )
    selected = pre_death_roster(coach_input, offset_seconds=0.5)
    assert selected is not None
    unknown = [
        s.timestamp
        for s in snapshots
        if s.ally_alive_count is None or s.opponent_alive_count is None
    ]
    observed_times = [obs.video_time for obs in observations_from_snapshots(snapshots)]
    assert [p.video_time for p in players.trajectory] == [case.first]
    assert selected.video_time == case.first
    assert players.at_death.video_time == case.resumed + 0.5
    assert len(unknown) == (
        int((case.resumed - case.first - 1) * 2) if case.hidden_middle else 0
    )
    assert all(
        s.ally_alive_count == s.opponent_alive_count == 3
        for s in snapshots
        if s.timestamp in observed_times
    )
    return {
        "case": case.name,
        "input": {
            "first": case.first,
            "last_initial": case.first + 0.5,
            "resumed": case.resumed,
            "last_resumed": case.resumed + 0.5,
            "death": case.death,
            "hidden_middle": case.hidden_middle,
        },
        "frame_count": len(frames),
        "usable_paired_snapshot_times": observed_times,
        "unknown_snapshot_times": unknown,
        "legacy_players": players.model_dump(mode="json"),
        "legacy_pre_death_sample": selected.model_dump(mode="json"),
        "legacy_pre_death_sample_age_video_seconds": case.death - selected.video_time,
        "source_roster_result_count": len(frames),
        "scope": (
            "synthetic existing-code behavior; "
            "no new detector or proposed schema implemented"
        ),
    }


def main() -> None:
    """Print deterministic JSON for the required gap experiment and its control."""
    cases = [
        Case("long_gap_expanded_window", 0, 21, 22, True),
        Case("gap_inside_default_8_second_lookback", 10, 17, 18, True),
        Case("readable_control_inside_default_lookback", 10, 17, 18, False),
    ]
    results = [probe(c) for c in cases]
    assert results[1]["legacy_players"] == results[2]["legacy_players"]
    print(
        json.dumps(
            {
                "schema_version": 1,
                "results": results,
                "gap_and_control_legacy_players_equal": True,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
