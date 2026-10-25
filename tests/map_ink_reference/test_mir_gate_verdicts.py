"""Gate classification and bound, sign-flip test, mapping and verdict rules."""

from __future__ import annotations

from fractions import Fraction

import cv2
import numpy as np
import pytest
from map_ink_reference.comparison import (
    FrameOutcome,
    LabelledPoint,
    calibrated_classifier,
    class_map,
    frame_outcome,
    identity_holds,
    q_all,
    stage1_verdict,
    stage2_verdict,
)
from map_ink_reference.errors import MalformedInput, ProcedureOrderError
from map_ink_reference.gate import (
    AGREE,
    DISAGREE,
    EXCLUDED,
    classify_pair,
    clopper_pearson_upper,
    evaluate_gate,
    pair_labels,
    pooled_pairs,
    population,
    verify_queue_duplicates,
)
from map_ink_reference.mapping import (
    ALLY,
    OPPONENT,
    OTHER,
    consistent_own_swatch,
    reference_class,
    team_answers,
)
from map_ink_reference.statistics import sign_flip
from map_ink_reference.verdicts import (
    FAIL,
    INCONCLUSIVE,
    NOT_EVALUABLE,
    PASS,
    PRESERVED,
    PRESERVED_IMPROVED,
    REGRESSED,
    criterion4,
    look_verdict,
    verdict_4b,
)

from splatoon3_ai_coach.config.models import MapInkAnalyzerConfig


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("ink_A", "ink_A", AGREE),
        ("not_ink", "not_ink", AGREE),
        ("cant_tell", "cant_tell", EXCLUDED),
        ("cant_tell", "ink_B", DISAGREE),
        ("ink_A", "cant_tell", DISAGREE),
        ("ink_A", "ink_B", DISAGREE),
        ("ink_A", "not_ink", DISAGREE),
        (None, "ink_A", DISAGREE),
        ("ink_A", None, DISAGREE),
        (None, None, DISAGREE),
    ],
)
def test_frozen_pair_classification(a, b, expected) -> None:
    assert classify_pair(a, b) == expected


def test_pair_classification_rejects_unknown_labels() -> None:
    with pytest.raises(MalformedInput):
        classify_pair("ally", "ally")


def test_clopper_pearson_known_values() -> None:
    assert clopper_pearson_upper(0, 29) == pytest.approx(1 - 0.05 ** (1 / 29), abs=1e-9)
    assert clopper_pearson_upper(0, 29) <= 0.10
    assert clopper_pearson_upper(0, 28) > 0.10
    assert clopper_pearson_upper(1, 36) > 0.10
    assert clopper_pearson_upper(5, 5) == 1.0
    with pytest.raises(MalformedInput):
        clopper_pearson_upper(0, 0)


def test_gate_pass_fail_and_zero_denominator() -> None:
    agree = {f"p{i}": ("ink_A", "ink_A") for i in range(29)}
    assert evaluate_gate(agree).passed
    assert not evaluate_gate(dict(list(agree.items())[:28])).passed
    with_one = dict(
        agree, x=("ink_A", "ink_B"), **{f"q{i}": ("not_ink", "not_ink") for i in range(6)}
    )
    result = evaluate_gate(with_one)
    assert (result.numerator, result.denominator) == (1, 36) and not result.passed
    excluded = {f"p{i}": ("cant_tell", "cant_tell") for i in range(50)}
    result = evaluate_gate(excluded)
    assert result.denominator == 0 and result.excluded == 50 and not result.passed


def test_population_excludes_only_recordings_without_swatches() -> None:
    dups = ["a|f|0", "b|f|1", "c|f|2"]
    recs = {"a|f|0": "a", "b|f|1": "b", "c|f|2": "c"}
    assert population(dups, recs, ["a", "c"]) == ["a|f|0", "c|f|2"]


def test_pair_labels_missing_copy_counts_as_disagreement() -> None:
    queue = [
        {"item_id": "i1", "point_ref": "r1", "copy": 0},
        {"item_id": "i2", "point_ref": "r1", "copy": 1},
        {"item_id": "i3", "point_ref": "r2", "copy": 0},
        {"item_id": "i4", "point_ref": "r2", "copy": 1},
    ]
    refs = {"r1": "p1", "r2": "p2"}
    pairs = pair_labels(
        ["p1", "p2"], queue, {"i1": "ink_A", "i2": "ink_A", "i3": "ink_B"}, refs
    )
    assert pairs == {"p1": ("ink_A", "ink_A"), "p2": ("ink_B", None)}
    assert evaluate_gate(pairs).numerator == 1
    verify_queue_duplicates(["p1", "p2"], queue, refs)
    with pytest.raises(MalformedInput):
        verify_queue_duplicates(["p1"], queue, refs)


def test_pooled_pairs_keep_both_stages() -> None:
    pooled = pooled_pairs({"p": ("ink_A", "ink_A")}, {"q": ("ink_B", "ink_A")})
    assert set(pooled) == {"1|p", "2|q"}
    assert evaluate_gate(pooled).numerator == 1
    with pytest.raises(MalformedInput):
        pooled_pairs({"p": ("ink_A", "ink_A")}, {"p": ("ink_A", "ink_A")})


def test_sign_flip_exact_values() -> None:
    up = sign_flip([1] * 8)
    assert (up.t_obs, up.f_nz, up.p_reg, up.p_imp) == (
        8,
        8,
        Fraction(1),
        Fraction(1, 256),
    )
    down = sign_flip([-2, -1, 0, -1, -1, -1, -3, -1, -1])
    assert down.f_nz == 8 and down.p_reg == Fraction(1, 256)
    ties = sign_flip([1, -1])
    assert (ties.p_reg, ties.p_imp) == (Fraction(3, 4), Fraction(3, 4))
    empty = sign_flip([0, 0])
    assert (empty.f_nz, empty.p_reg, empty.p_imp) == (0, 1, 1)


def test_sign_flip_matches_brute_force() -> None:
    import itertools

    d = [3, -1, 2, 2, -4, 1]
    t = sum(d)
    totals = [
        sum(e * x for e, x in zip(signs, d, strict=True))
        for signs in itertools.product((1, -1), repeat=len(d))
    ]
    result = sign_flip(d)
    assert result.p_reg == Fraction(sum(x <= t for x in totals), 64)
    assert result.p_imp == Fraction(sum(x >= t for x in totals), 64)


def test_team_consistency_rule() -> None:
    assert consistent_own_swatch(["A", "A"]) == "A"
    assert consistent_own_swatch(["B", "B", "B", "B"]) == "B"
    assert consistent_own_swatch(["A", "B"]) is None
    assert consistent_own_swatch(["A", "cant_tell"]) is None
    assert consistent_own_swatch(["A", None]) is None
    assert consistent_own_swatch([]) is None
    records = [{"team": {"t": {"start": {"answer": "A"}, "end": {"answer": "A"}}}}]
    assert team_answers(records, "t") == ["A", "A"]
    assert team_answers(records, "missing") == [None, None]


def test_reference_mapping_after_sealing() -> None:
    assert reference_class("ink_A", "A") == ALLY
    assert reference_class("ink_B", "A") == OPPONENT
    assert reference_class("ink_A", "B") == OPPONENT
    assert reference_class("not_ink", "B") == OTHER
    assert reference_class("cant_tell", "A") is None
    with pytest.raises(MalformedInput):
        reference_class("ink_C", "A")


def _test(p_reg: float, p_imp: float):
    return type("T", (), {"p_reg": p_reg, "p_imp": p_imp})()


def test_look_precedence() -> None:
    assert look_verdict(False, True, None) == NOT_EVALUABLE
    assert look_verdict(True, True, None) == PRESERVED
    assert look_verdict(True, False, _test(0.005, 0.001)) == REGRESSED
    assert look_verdict(True, False, _test(0.0051, 0.025)) == PRESERVED_IMPROVED
    assert look_verdict(True, False, _test(0.5, 0.026)) == INCONCLUSIVE
    with pytest.raises(MalformedInput):
        look_verdict(True, False, None)


def test_verdict_4b_rules() -> None:
    ok = dict.fromkeys("abcde", PRESERVED)
    assert verdict_4b(ok, gate1_passed=True, n_applicable=5) == PASS
    assert verdict_4b(ok, gate1_passed=False, n_applicable=5) == NOT_EVALUABLE
    two_ne = dict(ok, a=NOT_EVALUABLE, b=NOT_EVALUABLE)
    assert verdict_4b(two_ne, gate1_passed=True, n_applicable=5) == NOT_EVALUABLE
    assert (
        verdict_4b(dict(ok, a=NOT_EVALUABLE), gate1_passed=True, n_applicable=5) == PASS
    )
    assert (
        verdict_4b(dict(two_ne, c=REGRESSED), gate1_passed=True, n_applicable=5) == FAIL
    )
    inc = dict(ok, a=INCONCLUSIVE)
    with pytest.raises(ProcedureOrderError):
        verdict_4b(inc, gate1_passed=True, n_applicable=5)
    assert (
        verdict_4b(inc, gate1_passed=True, gate2_passed=False, n_applicable=5)
        == NOT_EVALUABLE
    )
    assert (
        verdict_4b(
            inc,
            gate1_passed=True,
            stage2={"a": PRESERVED_IMPROVED},
            gate2_passed=True,
            n_applicable=5,
        )
        == PASS
    )
    assert (
        verdict_4b(
            inc,
            gate1_passed=True,
            stage2={"a": INCONCLUSIVE},
            gate2_passed=True,
            n_applicable=5,
        )
        == NOT_EVALUABLE
    )
    assert (
        verdict_4b(
            inc,
            gate1_passed=True,
            stage2={"a": REGRESSED},
            gate2_passed=True,
            n_applicable=5,
        )
        == FAIL
    )
    with pytest.raises(MalformedInput):
        verdict_4b(
            inc,
            gate1_passed=True,
            stage2={"b": PRESERVED},
            gate2_passed=True,
            n_applicable=5,
        )
    with pytest.raises(MalformedInput):
        verdict_4b(dict(list(ok.items())[:4]), gate1_passed=True, n_applicable=5)


def test_stage2_gate_failure_voids_stage1_regression() -> None:
    results = dict.fromkeys("abcd", PRESERVED) | {"e": REGRESSED, "a": INCONCLUSIVE}
    assert (
        verdict_4b(results, gate1_passed=True, gate2_passed=False, n_applicable=5)
        == NOT_EVALUABLE
    )


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (PASS, PASS, PASS),
        (PASS, FAIL, FAIL),
        (FAIL, NOT_EVALUABLE, FAIL),
        (PASS, NOT_EVALUABLE, NOT_EVALUABLE),
        (NOT_EVALUABLE, PASS, NOT_EVALUABLE),
    ],
)
def test_criterion4_composition(a, b, expected) -> None:
    assert criterion4(a, b) == expected


def _classifier(ally_h: float, opp_h: float):
    payload = {
        "ally_h": ally_h,
        "opponent_h": opp_h,
        "separation_degrees": 90.0,
        "calibrated_at": 1.0,
    }
    return calibrated_classifier(MapInkAnalyzerConfig(), payload)


def _hsv_image(hues: list[int]) -> np.ndarray:
    hsv = np.uint8([[[h, 220, 220] for h in hues]])
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def test_class_map_and_identity() -> None:
    image = _hsv_image([30, 120, 75])
    v3 = _classifier(30, 120)
    same = _classifier(30, 120)
    shifted = _classifier(60, 120)
    assert class_map(image, v3).tolist() == [[0, 1, 2]]
    mask = np.ones((1, 3), dtype=bool)
    assert q_all([image], lambda img: mask, same, v3) == (0, 3)
    assert identity_holds(*q_all([image], lambda img: mask, same, v3))
    differing, total = q_all([image], lambda img: mask, shifted, v3)
    assert total == 3 and differing > 0 and not identity_holds(differing, total)
    assert not identity_holds(0, 0)
    with pytest.raises(MalformedInput):
        calibrated_classifier(MapInkAnalyzerConfig(), None)


def test_frame_outcome_counts_discordance() -> None:
    a1 = np.array([[0, 1, 2, 0]], dtype=np.uint8)
    v3 = np.array([[0, 0, 2, 1]], dtype=np.uint8)
    points = [
        LabelledPoint("p0", "f", 0, 0, ("ink_A",)),  # ally: both right
        LabelledPoint("p1", "f", 1, 0, ("ink_B", "ink_B")),  # opp: A1 right
        LabelledPoint("p2", "f", 2, 0, ("not_ink",)),  # other: both right
        LabelledPoint("p3", "f", 3, 0, ("ink_A",)),  # ally: v3 inverted, A1 right
        LabelledPoint("p4", "f", 3, 0, ("ink_A", "ink_B")),  # duplicate disagree
        LabelledPoint("p5", "f", 3, 0, ("cant_tell",)),
        LabelledPoint("p6", "f", 3, 0, ("ink_A", None)),
    ]
    out = frame_outcome("f", points, "A", a1, v3)
    assert out.judgeable_points == 4 and not out.judgeable
    assert (out.b, out.c, out.d) == (0, 2, 2)
    assert (out.a1_correct, out.v3_correct) == (4, 2)
    assert (out.v3_inverted, out.a1_inverted) == (2, 0)


def _frames(ds: list[int], judgeable: int = 9) -> list[FrameOutcome]:
    return [
        FrameOutcome(f"f{i}", judgeable, b=max(0, -d), c=max(0, d))
        for i, d in enumerate(ds)
    ]


def test_stage1_and_stage2_verdicts() -> None:
    eight_up = _frames([1] * 8)
    verdict, test = stage1_verdict(
        swatch_valid=True, own_swatch="A", frames=eight_up, identity=False
    )
    assert verdict == PRESERVED_IMPROVED and test.p_imp == Fraction(1, 256)
    verdict, _ = stage1_verdict(
        swatch_valid=True, own_swatch="A", frames=_frames([-1] * 8), identity=False
    )
    assert verdict == REGRESSED
    verdict, _ = stage1_verdict(
        swatch_valid=True,
        own_swatch="A",
        frames=_frames([1] * 8, judgeable=5),
        identity=True,
    )
    assert verdict == NOT_EVALUABLE
    verdict, _ = stage1_verdict(
        swatch_valid=True, own_swatch=None, frames=eight_up, identity=True
    )
    assert verdict == NOT_EVALUABLE
    verdict, test = stage1_verdict(
        swatch_valid=True, own_swatch="A", frames=eight_up, identity=True
    )
    assert verdict == PRESERVED and test is None
    s1 = _frames([1, -1, 0, 0, 1, 0, 0, 0])
    assert (
        stage2_verdict(
            own_swatch_all=None,
            stage1_frames=s1,
            stage2_frames=_frames([1] * 8),
            identity=False,
        )[0]
        == NOT_EVALUABLE
    )
    assert (
        stage2_verdict(
            own_swatch_all="A",
            stage1_frames=s1,
            stage2_frames=_frames([1]),
            identity=False,
        )[0]
        == INCONCLUSIVE
    )
    verdict, test = stage2_verdict(
        own_swatch_all="A",
        stage1_frames=s1,
        stage2_frames=_frames([1] * 8),
        identity=False,
    )
    assert test.f_nz == 11 and verdict == PRESERVED_IMPROVED
