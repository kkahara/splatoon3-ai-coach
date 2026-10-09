"""Pre-registered SPECIAL_USE held-out gate (``review_sync_boundary_sheet gate``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from review_sync_boundary_sheet import (
    bias_test,
    read_known_failures,
    read_recheck,
    run_gate,
    transition_direction,
)

AFTER = [0.12, 1.19]
BEFORE = [-1.19, -0.12]
IN = [-0.4, 0.6]


def _case(
    case_id: str, x0: str, bracket: list[float] = AFTER, transition: str = "alive_to_dead"
) -> dict[str, Any]:
    a, b = bracket
    distance = 0.0 if a < 0 <= b else min(abs(a), abs(b))
    return {
        "case_id": case_id,
        "recording": "rec",
        "slot": "top_1",
        "corpus": "flip",
        "transition_type": transition,
        "transition_bracket_s_relative_to_special": bracket,
        "distance_to_transition_s": distance,
        "pipeline_state_x0": x0,
    }


def _batch(
    cases: list[dict[str, Any]], labels: dict[str, str], status: str = "reviewed"
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    entries = [
        {"case_id": i, "reviewed_state_at_special": s, "basis": "roster_frames"}
        for i, s in labels.items()
    ]
    return "batch", cases, {"status": status, "cases": entries}


def _recheck(**states: str) -> dict[str, dict[str, Any]]:
    return {
        i: {"case_id": i, "reviewed_state_at_special": s, "reviewer_note": "frames"}
        for i, s in states.items()
    }


def _row(result: dict[str, Any], case_id: str) -> dict[str, Any]:
    return next(r for r in result["cases"] if r["case_id"] == case_id)


# --- label merge ---------------------------------------------------------------


def test_recheck_overrides_final_and_keeps_raw() -> None:
    batch = _batch(
        [_case("B1", "alive"), _case("B2", "alive")], {"B1": "dead", "B2": "alive"}
    )
    result = run_gate([batch], _recheck(B1="alive"), {})
    merged, untouched = _row(result, "B1"), _row(result, "B2")
    assert merged["raw_reviewed"] == "dead"
    assert merged["recheck_reviewed"] == "alive"
    assert merged["reviewed"] == "alive"
    assert merged["label_source"] == "recheck"
    assert untouched["recheck_reviewed"] is None
    assert untouched["reviewed"] == "alive"
    assert untouched["label_source"] == "raw"
    assert result["raw"]["timing_failures"] == ["B1"]
    assert result["final"]["timing_failures"] == []


# --- direction -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("transition", "bracket", "reviewed", "x0", "expected"),
    [
        ("alive_to_dead", AFTER, "dead", "alive", "lag"),
        ("dead_to_alive", AFTER, "alive", "dead", "lag"),
        ("alive_to_dead", BEFORE, "alive", "dead", "lead"),
        ("dead_to_alive", BEFORE, "dead", "alive", "lead"),
        ("alive_to_dead", AFTER, "alive", "alive", "none"),
        ("alive_to_dead", BEFORE, "dead", "dead", "none"),
        ("alive_to_dead", AFTER, "indeterminate", "alive", "none"),
        ("alive_to_dead", BEFORE, "indeterminate", "dead", "none"),
        ("alive_to_dead", BEFORE, "dead", "alive", "none"),
        ("alive_to_dead", AFTER, "alive", "dead", "none"),
        ("dead_to_alive", AFTER, "dead", "alive", "none"),
        ("dead_to_alive", BEFORE, "alive", "dead", "none"),
        ("alive_to_dead", IN, "dead", "alive", "none"),
        ("alive_to_dead", IN, "alive", "dead", "none"),
    ],
)
def test_direction(
    transition: str, bracket: list[float], reviewed: str, x0: str, expected: str
) -> None:
    assert transition_direction(transition, bracket, reviewed, x0) == expected


@pytest.mark.parametrize("transition", ["alive_to_dead", "dead_to_alive"])
def test_direction_inverts_when_bracket_side_and_states_swap(transition: str) -> None:
    before, after = transition.split("_to_")
    mirrored = [-AFTER[1], -AFTER[0]]
    assert transition_direction(transition, AFTER, after, before) == "lag"
    assert transition_direction(transition, mirrored, before, after) == "lead"


def test_gate_rows_carry_direction() -> None:
    batch = _batch(
        [_case("B1", "alive", AFTER), _case("B2", "dead", BEFORE)],
        {"B1": "dead", "B2": "alive"},
    )
    result = run_gate([batch], {}, {})
    assert _row(result, "B1")["direction"] == "lag"
    assert _row(result, "B2")["direction"] == "lead"
    assert result["final"]["directions"] == {"lag": 1, "lead": 1}


# --- known failure ---------------------------------------------------------------


def test_known_failure_is_reported_but_not_timing() -> None:
    batch = _batch(
        [_case("B38", "alive", BEFORE, "dead_to_alive"), _case("B2", "alive")],
        {"B38": "indeterminate", "B2": "alive"},
    )
    result = run_gate([batch], {}, {"B38": "disconnected_slot"})
    row = _row(result, "B38")
    assert row["failure_kind"] == "non_timing"
    assert row["known_cause"] == "disconnected_slot"
    assert row["direction"] == "none"
    assert result["final"]["non_timing_failures"] == ["B38"]
    assert result["final"]["timing_failures"] == []
    assert result["verdict"]["bias"]["timing_failures"] == 0
    assert result["verdict"]["verdict"] == "PASS"


def test_unlisted_failure_counts_as_timing() -> None:
    batch = _batch(
        [_case("B38", "alive", BEFORE, "dead_to_alive")], {"B38": "indeterminate"}
    )
    result = run_gate([batch], {}, {})
    assert _row(result, "B38")["failure_kind"] == "timing"
    assert result["verdict"]["verdict"] == "FAIL"


# --- bias ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("directions", "found"),
    [
        ([], False),
        (["lag"], False),
        (["lag", "lag"], False),
        (["lag", "lag", "lag"], True),
        (["lag", "lag", "lead"], False),
        (["lag", "lag", "lag", "lag", "lead"], True),
        (["lag", "lag", "lag", "lead"], False),
        (["lag", "lag", "lag", "lag", "none"], True),
        (["lag", "lag", "lag", "none"], False),
        (["none", "none", "none"], False),
    ],
)
def test_bias_rule(directions: list[str], found: bool) -> None:
    assert bias_test(directions)["systematic_bias"] is found


@pytest.mark.parametrize(
    "directions",
    [["lag"] * 3, ["lag"] * 4 + ["lead"], ["lag", "lag", "lead"], ["lag"] * 3 + ["lead"]],
)
def test_bias_is_symmetric(directions: list[str]) -> None:
    swapped = ["lead" if d == "lag" else "lag" for d in directions]
    result, mirror = bias_test(directions), bias_test(swapped)
    assert result["systematic_bias"] is mirror["systematic_bias"]
    assert result["dominant_share"] == mirror["dominant_share"]
    if result["systematic_bias"]:
        assert (result["bias_direction"], mirror["bias_direction"]) == ("lag", "lead")


def test_exact_eighty_percent_is_bias() -> None:
    result = bias_test(["lead"] * 8 + ["lag"] * 2)
    assert result["dominant_share"] == 0.8
    assert result["systematic_bias"] is True


# --- verdict ------------------------------------------------------------------------


def test_clean_set_passes() -> None:
    batch = _batch(
        [_case("B1", "alive"), _case("B2", "dead", BEFORE)], {"B1": "alive", "B2": "dead"}
    )
    verdict = run_gate([batch], {}, {})["verdict"]
    assert verdict["verdict"] == "PASS"
    assert verdict["condition_2_timing_failures"] == []
    assert verdict["empirical_x_timing_attributable"]["empirical_x_s"] == 0.0


def test_one_timing_failure_fails() -> None:
    batch = _batch(
        [_case("B1", "alive"), _case("B2", "alive")], {"B1": "dead", "B2": "alive"}
    )
    verdict = run_gate([batch], {}, {})["verdict"]
    assert verdict["verdict"] == "FAIL"
    assert verdict["condition_1_timing_failures"] == ["B1"]
    assert verdict["condition_2_timing_failures"] == ["B1"]


def test_isolated_failures_fail_without_tuning() -> None:
    cases = [
        _case("B1", "alive", AFTER),
        _case("B2", "dead", BEFORE),
        _case("B3", "alive"),
    ]
    batch = _batch(cases, {"B1": "dead", "B2": "alive", "B3": "indeterminate"})
    verdict = run_gate([batch], {}, {})["verdict"]
    assert verdict["verdict"] == "FAIL"
    assert verdict["reason"] == "isolated_timing_failures"
    assert verdict["bias"]["systematic_bias"] is False
    assert "unchanged" in verdict["action"]
    assert verdict["condition_2_timing_failures"] == ["B1", "B2"]


def test_systematic_bias_fails() -> None:
    cases = [_case(f"B{i}", "alive", AFTER) for i in range(3)]
    batch = _batch(cases, {f"B{i}": "dead" for i in range(3)})
    verdict = run_gate([batch], {}, {})["verdict"]
    assert verdict["verdict"] == "FAIL"
    assert verdict["reason"] == "systematic_bias"
    assert verdict["bias"]["bias_direction"] == "lag"
    assert "new recordings" in verdict["action"]


def test_in_bracket_unknown_is_expected_uncertainty() -> None:
    batch = _batch(
        [_case("B1", "unknown", IN), _case("B2", "unknown", IN)],
        {"B1": "indeterminate", "B2": "dead"},
    )
    result = run_gate([batch], {}, {})
    assert result["verdict"]["verdict"] == "PASS"
    assert result["final"]["x0_unknown_in_bracket"] == 2
    assert all(r["failure_kind"] is None for r in result["cases"])


# --- input safety ----------------------------------------------------------------------


def _write(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(data), "utf-8")
    return path


def test_pending_recheck_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "r.yaml",
        {
            "status": "draft",
            "cases": [{"case_id": "B1", "reviewed_state_at_special": None}],
        },
    )
    with pytest.raises(SystemExit, match="status: reviewed"):
        read_recheck(path)


def test_recheck_without_state_or_note_is_rejected(tmp_path: Path) -> None:
    blank = {"case_id": "B1", "reviewed_state_at_special": None, "reviewer_note": "x"}
    with pytest.raises(SystemExit, match="alive / dead / indeterminate"):
        read_recheck(
            _write(tmp_path / "a.yaml", {"status": "reviewed", "cases": [blank]})
        )
    silent = {"case_id": "B1", "reviewed_state_at_special": "dead", "reviewer_note": ""}
    with pytest.raises(SystemExit, match="basis note"):
        read_recheck(
            _write(tmp_path / "b.yaml", {"status": "reviewed", "cases": [silent]})
        )


def test_duplicate_recheck_ids_are_rejected(tmp_path: Path) -> None:
    entry = {"case_id": "B1", "reviewed_state_at_special": "dead", "reviewer_note": "x"}
    path = _write(tmp_path / "r.yaml", {"status": "reviewed", "cases": [entry, entry]})
    with pytest.raises(SystemExit, match="duplicate"):
        read_recheck(path)


def test_duplicate_case_ids_are_rejected() -> None:
    within = _batch([_case("B1", "alive"), _case("B1", "alive")], {"B1": "alive"})
    with pytest.raises(SystemExit, match="duplicate"):
        run_gate([within], {}, {})
    one = _batch([_case("B1", "alive")], {"B1": "alive"})
    with pytest.raises(SystemExit, match="duplicate case B1 across batches"):
        run_gate([one, one], {}, {})


def test_duplicate_known_failures_are_rejected(tmp_path: Path) -> None:
    entry = {"case_id": "B38", "cause": "disconnected_slot"}
    path = _write(tmp_path / "k.yaml", {"known_failures": [entry, entry]})
    with pytest.raises(SystemExit, match="duplicate"):
        read_known_failures(path)


def test_unknown_recheck_and_known_ids_are_rejected() -> None:
    batch = _batch([_case("B1", "alive")], {"B1": "alive"})
    with pytest.raises(SystemExit, match="recheck IDs not in any batch: Z9"):
        run_gate([batch], _recheck(Z9="dead"), {})
    with pytest.raises(SystemExit, match="known failure IDs not in any batch: Z9"):
        run_gate([batch], {}, {"Z9": "disconnected_slot"})


def test_unreviewed_or_mismatched_batch_is_rejected() -> None:
    draft = _batch([_case("B1", "alive")], {"B1": "alive"}, status="draft")
    with pytest.raises(SystemExit, match="status: reviewed"):
        run_gate([draft], {}, {})
    missing = _batch([_case("B1", "alive"), _case("B2", "alive")], {"B1": "alive"})
    with pytest.raises(SystemExit, match="cases and labels differ: B2"):
        run_gate([missing], {}, {})
