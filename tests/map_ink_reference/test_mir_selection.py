"""Frame population, intervals and the frozen selection formulas."""

from __future__ import annotations

import pytest
from map_ink_reference.errors import IsolationViolation, MalformedInput
from map_ink_reference.plan import (
    build_frame_plan,
    eligible_frame_ids,
    stage_cue_frame_ids,
    stage_frame_ids,
)
from map_ink_reference.population import (
    eligible_intervals,
    even_rank_indices,
    reduce_manifest,
    select_stage1,
    select_stage2,
)
from mir_helpers import frame_spec, interval_specs, manifest_from_specs


def _intervals(specs, **kw):
    return eligible_intervals(reduce_manifest(manifest_from_specs(specs, **kw)))


def test_even_rank_indices_match_sealed_formula() -> None:
    assert even_rank_indices(10, 8) == [0, 1, 3, 4, 5, 6, 8, 9]
    assert even_rank_indices(10, 5) == [1, 3, 5, 7, 9]
    assert even_rank_indices(3, 3) == [0, 1, 2]
    assert even_rank_indices(5, 0) == []
    with pytest.raises(MalformedInput):
        even_rank_indices(2, 3)


def test_eligibility_requires_all_four_conditions() -> None:
    specs = [
        frame_spec(0.0),
        frame_spec(0.5, roster=True),  # roster visible
        frame_spec(1.0, phase="intro"),  # not in_match
        frame_spec(1.5),
        frame_spec(2.0),  # at MATCH_END -> excluded
    ]
    specs[1]["map"] = True
    intervals = _intervals(specs, match_end=2.0)
    assert [[f.frame_id for f in i.frames] for i in intervals] == [["f00000"], ["f00003"]]


def test_missing_roster_reading_is_not_eligible() -> None:
    manifest = manifest_from_specs([frame_spec(0.0)])
    manifest["frame_results"][0]["detections"].pop()
    assert eligible_intervals(reduce_manifest(manifest)) == []


def test_intervals_split_on_non_exact_cadence_step() -> None:
    specs = [frame_spec(0.0), frame_spec(0.5), frame_spec(1.5), frame_spec(2.0)]
    intervals = _intervals(specs)
    assert [len(i.frames) for i in intervals] == [2, 2]


def test_long_intervals_first_then_singles_and_middle_frame() -> None:
    specs = interval_specs([1, 4, 1, 3, 1, 2, 1, 1, 1, 1, 1])
    intervals = _intervals(specs)
    chosen = select_stage1(intervals)
    assert [len(i.frames) for i in chosen[:3]] == [4, 3, 2]
    assert all(len(i.frames) == 1 for i in chosen[3:])
    four = chosen[0]
    assert four.middle_frame is four.frames[1]  # floor((4-1)/2)
    assert chosen[1].middle_frame is chosen[1].frames[1]  # floor((3-1)/2)
    assert chosen[2].middle_frame is chosen[2].frames[0]  # floor((2-1)/2)


def test_single_selection_uses_even_rank_over_singles() -> None:
    specs = interval_specs([2, 2, 2] + [1] * 10)
    intervals = _intervals(specs)
    singles = [i for i in intervals if not i.is_long]
    chosen = select_stage1(intervals)
    expected = [singles[k] for k in (1, 3, 5, 7, 9)]
    assert chosen[3:] == expected


def test_stage2_takes_remaining_with_same_procedure() -> None:
    specs = interval_specs([2] * 12)
    intervals = _intervals(specs)
    stage1 = select_stage1(intervals)
    stage2 = select_stage2(intervals, stage1)
    assert len(stage2) == 4
    assert not {id(i) for i in stage1} & {id(i) for i in stage2}


def test_plan_lists_population_cues_and_stage_frames() -> None:
    specs = interval_specs([3] * 10)
    plan = build_frame_plan("r", manifest_from_specs(specs), manifest_sha256="x")
    assert len(stage_frame_ids(plan, 1)) == 8
    assert len(stage_frame_ids(plan, 2)) == 2
    assert len(eligible_frame_ids(plan)) == 30
    cues = stage_cue_frame_ids(plan, 1)
    assert len(cues) == 8
    planned = {f["frame_id"] for f in plan["frames"]}
    assert set(eligible_frame_ids(plan)) | set(cues) <= planned


def test_cue_frame_skips_non_alive_lifecycle() -> None:
    specs = [
        frame_spec(0.0),
        frame_spec(0.5, eligible=False, lifecycle="dead"),
        frame_spec(1.0, eligible=False),
    ]
    specs += interval_specs([1] * 8, start=1.5)
    plan = build_frame_plan("r", manifest_from_specs(specs), manifest_sha256="x")
    assert plan["intervals"][0]["cue_frame_id"] == "f00002"


def test_too_few_intervals_fail_closed() -> None:
    with pytest.raises(MalformedInput):
        build_frame_plan(
            "r", manifest_from_specs(interval_specs([1] * 5)), manifest_sha256="x"
        )


@pytest.mark.parametrize("key", ["team_color_calibration", "map_observations"])
def test_manifest_with_forbidden_keys_is_rejected(key: str) -> None:
    manifest = manifest_from_specs(interval_specs([1] * 8), extra={key: {}})
    with pytest.raises(IsolationViolation):
        reduce_manifest(manifest)


def test_malformed_manifests_fail_closed() -> None:
    good = manifest_from_specs(interval_specs([1] * 8))
    for mutate in (
        lambda m: m.pop("video_identity"),
        lambda m: m["frame_results"][0].pop("source_pts"),
        lambda m: m["game_events"].extend(
            [{"event_type": "MATCH_END", "start_time": 1.0}] * 2
        ),
        lambda m: m["state_snapshots"].append(dict(m["state_snapshots"][0])),
        lambda m: m["frame_results"].reverse(),
        lambda m: m["state_snapshots"].pop(0),
    ):
        manifest = manifest_from_specs(interval_specs([1] * 8))
        mutate(manifest)
        with pytest.raises(MalformedInput):
            build_frame_plan("r", manifest, manifest_sha256="x")
    assert build_frame_plan("r", good, manifest_sha256="x")["recording_id"] == "r"
