"""N2 citation-compliance experiment: strict scorer, gate, sealed inputs."""

from __future__ import annotations

import json

import citation_compliance_n2 as cc
import citation_compliance_n2_run as run
import citation_compliance_n2_score as score
import coaching_units_experiment_d as xd
import pytest

VIEW = {
    "schema_version": 3,
    "roster_evidence": False,
    "low_ink": None,
    "clock": {
        "display": "2:33",
        "seconds_remaining": 153,
        "source_path": "game_clock_samples[label=death]",
    },
    "death": {
        "death_time_video": 205.0,
        "death_to_active_again": {
            "value": 8.5,
            "clock": "video",
            "end_gap_seconds": None,
        },
        "source_path": "primary_context.death_episode",
    },
    "special": {"state_at_anchor": "not_ready", "gauge_visible": True},
    "map": {
        "map_check_before_death": {
            "status": "observed",
            "source_path": "primary_context.map",
        }
    },
    "related": [
        {
            "relations": {
                "leads_to_death_episode_id": "death_episode:226.000",
                "source_path": "related[0].context.relations",
            },
            "source_path": "related[0]",
        }
    ],
    "claim_basis": [
        {
            "claim_id": "death_out_of_play",
            "evidence_paths": [
                "death.death_to_active_again.value",
                "death.death_to_active_again.clock",
            ],
        }
    ],
}
INDEX = score.view_index(VIEW)


def _classes(text: str) -> list[str]:
    return [c["class"] for c in score.score_entry(VIEW, text, INDEX)["citations"]]


@pytest.mark.parametrize(
    ("text", "expected", "form"),
    [
        ('clock.display = "2:33"', score.COMPLIANT, "exact"),
        ("clock.seconds_remaining = 153", score.COMPLIANT, "exact"),
        ("clock.seconds_remaining = 153.0", score.COMPLIANT, "exact"),
        ("death.death_to_active_again.value = 8.5 seconds", score.COMPLIANT, "remark"),
        ("death.death_to_active_again.value = 8.5s", score.COMPLIANT, "remark"),
        ("clock.seconds_remaining = 153 s", score.COMPLIANT, "remark"),
        ('clock.seconds_remaining = "153"', score.COMPLIANT, "lenient"),
        ("clock.display = 2:33", score.COMPLIANT, "lenient"),
        ("special.gauge_visible = true", score.COMPLIANT, "exact"),
        ("death.death_to_active_again.end_gap_seconds = null", score.COMPLIANT, "exact"),
        ("low_ink = null", score.COMPLIANT, "exact"),
        ("roster_evidence = false", score.COMPLIANT, "exact"),
        ("death.death_to_active_again.value = 8.54", "n2_scalar_wrong_value", "wrong"),
        ("clock.seconds_remaining = 152", "n2_scalar_wrong_value", "wrong"),
        ('special.gauge_visible = "yes"', "n2_scalar_wrong_value", "wrong"),
        ("special.gauge_visible = 1", "n2_scalar_wrong_value", "wrong"),
    ],
)
def test_literal_scalar_value(text: str, expected: str, form: str) -> None:
    (citation,) = score.score_entry(VIEW, text, INDEX)["citations"]
    assert (citation["class"], citation["value_form"]) == (expected, form)
    assert citation["exists_literally"] and citation["terminates_at_scalar"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("death.death_to_active_again = 8.5", "n2_object_asserted"),
        ("map.map_check_before_death = null", "n2_object_asserted"),
        ('clock.source_path = "game_clock_samples[label=death]"', "source_path_field"),
        ("game_clock_samples[label=death] = 153", "legacy_label"),
        (
            "game_clock_samples[label=death].observation.seconds_remaining = 153",
            "legacy_label",
        ),
        ('primary_context.map.status = "observed"', "legacy_label"),
        ("related[0].context.relations.leads_to_death_episode_id = 1", "legacy_label"),
        ("related[0].scenario.start_time = 205.0", "n1_coachinput"),
        (
            "primary_context.timeline.time_since_previous_death.value = 1.0",
            "n1_coachinput",
        ),
        ("durations.death_to_respawn = 7.0", "n1_coachinput"),
        ('map_check_before_death.status = "observed"', "relative_path"),
        (
            'relations.leads_to_death_episode_id = "death_episode:226.000"',
            "relative_path",
        ),
        ("clock.seconds_left = 1", "unresolved_path"),
        ("related[3].relations.leads_to_death_episode_id = 1", "unresolved_path"),
        ('enemy.weapon = "splattershot"', "unsupported_invented"),
        ("Death time = 2:33", "unsupported_invented"),
    ],
)
def test_noncompliant_class(text: str, expected: str) -> None:
    assert _classes(text) == [expected]


def test_label_that_is_a_view_path_is_not_a_legacy_prefix() -> None:
    assert "related[0]" not in INDEX.labels
    assert _classes("related[0].scenario.start_time = 1") == ["n1_coachinput"]


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("death.death_to_active_again.value", "path_without_value"),
        ('clock.display: "2:33"', "colon_separator"),
        ("`clock.display` = 2:33", "path_without_value"),
        ("The clock showed 2:33", "no_path"),
        ("low_ink", "path_without_value"),
    ],
)
def test_malformed_reason(text: str, reason: str) -> None:
    (citation,) = score.score_entry(VIEW, text, INDEX)["citations"]
    assert (citation["class"], citation["reason"]) == ("malformed", reason)


def test_entry_flags_and_segments() -> None:
    entry = score.score_entry(
        VIEW, 'Clock: clock.display = "2:33", clock.seconds_remaining = 153', INDEX
    )
    assert [c["class"] for c in entry["citations"]] == [score.COMPLIANT] * 2
    assert entry["prose_prefix"] and entry["multi_fact"] and entry["compliant"]
    mixed = score.score_entry(
        VIEW, "clock.seconds_remaining = 153; durations.x = 1", INDEX
    )
    assert not mixed["compliant"]


def test_quoted_value_with_equals_is_one_segment() -> None:
    assert score.segments('special.state_at_anchor = "a.b = c"') == [
        (0, "special.state_at_anchor", '"a.b = c"')
    ]


def _record(evidence: list[str], **extra) -> dict:
    from splatoon3_ai_coach.coach.coach import annotate_claim_flags
    from splatoon3_ai_coach.coach.llm_client import CoachingAssessment

    assessment = CoachingAssessment(evidence_used=evidence, **extra)
    return {
        "call_id": "u__r1",
        "match": "m",
        "cid": "c",
        "assessment": assessment.model_dump(mode="json"),
        "validation": annotate_claim_flags(assessment, dict(VIEW)),
    }


def test_output_support_and_claim_coverage() -> None:
    out = score.score_output(
        VIEW,
        _record(
            ["death.death_to_active_again.value = 8.5"],
            recommendations=["Re-enter carefully."],
            selected_claim_ids=["death_out_of_play"],
        ),
    )
    assert out["output_compliant"] and out["support_ok"]
    assert out["claim_coverage"] == [
        {
            "claim_id": "death_out_of_play",
            "in_claim_basis": True,
            "evidence_path_cited": True,
        }
    ]
    uncited = score.score_output(
        VIEW, _record([], recommendations=["Re-enter carefully."])
    )
    assert not uncited["support_ok"] and not uncited["output_compliant"]
    assert score.score_output(VIEW, _record([]))["output_compliant"]


def test_validator_disagreement_is_recorded() -> None:
    out = score.score_output(VIEW, _record(["death.death_to_active_again.value = 8.54"]))
    (row,) = out["validator"]["disagreements"]
    assert row["validator_accepts"] and not row["strict_compliant"]


def test_gate() -> None:
    good = score.score_output(VIEW, _record(['clock.display = "2:33"']))
    bad = score.score_output(VIEW, _record(["durations.death_to_respawn = 7.0"]))
    unparsed = {
        "call_id": "x",
        "match": "m",
        "cid": "c",
        "parsed": False,
        "entries": [],
        "output_compliant": False,
    }
    assert score.gate([good, good], 2)["passed"]
    failed = score.gate([good, bad], 2)
    assert not failed["passed"] and failed["group_counts"]["legacy_form"] == 1
    assert not score.gate([good, unparsed], 2)["checks"]["all_calls_parsed"]
    assert not score.gate([good], 2)["checks"]["all_calls_parsed"]
    summary = score.summary([good, bad], 2)
    assert summary["units_compliant_every_repeat"] == 0
    assert summary["citations"] == {"total": 2, "compliant": 1}


def test_cells_and_counts() -> None:
    manifest = {
        "repeats": cc.REPEATS,
        "calls": [
            {"match": u.key[0], "cid": u.cid, "file": f"{u.slug}.user_prompt.txt"}
            for u in cc.UNITS
        ],
    }
    cells = run.expected_cells(manifest)
    assert len(cells) == 24 and len({c.call_id for c in cells}) == 24
    assert [c.repeat for c in cells[:12]] == [1] * 12
    assert run.count_problems(manifest) == []
    assert len({u.key for u in cc.UNITS}) == len(cc.UNITS) == 12


def test_score_run_writes_once(tmp_path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    prompt = "CoachLlmView JSON follows.\n\n" + json.dumps(VIEW, sort_keys=True)
    (inputs / "u.user_prompt.txt").write_text(prompt, encoding="utf-8")
    manifest = {
        "repeats": 2,
        "calls": [{"match": "m", "cid": "c", "file": "u.user_prompt.txt"}],
    }
    (inputs / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    calls = tmp_path / "outputs" / "calls"
    calls.mkdir(parents=True)
    for repeat, evidence in ((1, ['clock.display = "2:33"']), (2, ["durations.x = 1"])):
        cell = run.Cell(match="m", cid="c", repeat=repeat, file="u.user_prompt.txt")
        record = {
            **_record(evidence),
            "call_id": cell.call_id,
            "file": cell.file,
            "call_error": None,
        }
        (calls / f"{cell.call_id}.json").write_text(json.dumps(record), encoding="utf-8")
    assert run.score_run(tmp_path) == 0
    result = json.loads((tmp_path / "scoring.json").read_text(encoding="utf-8"))
    assert not result["summary"]["gate"]["passed"]
    assert [r["class"] for r in result["noncompliant"]] == ["n1_coachinput"]
    assert "**Gate: FAIL**" in (tmp_path / "SCORE.md").read_text(encoding="utf-8")
    assert "scoring.json" in (tmp_path / "SHA256SUMS").read_text(encoding="utf-8")
    with pytest.raises(SystemExit, match="immutable"):
        run.score_run(tmp_path)


SEALED = (cc.OUT_DIR / "inputs" / "manifest.json").is_file()


@pytest.mark.skipif(not SEALED, reason="sealed inputs not present")
def test_sealed_inputs_carry_the_design_hashes() -> None:
    design = cc.load_design(cc.OUT_DIR)
    files = {
        str(p.relative_to(cc.OUT_DIR / "inputs")): p.read_text(encoding="utf-8")
        for p in (cc.OUT_DIR / "inputs").rglob("*")
        if p.is_file()
    }
    assert cc.hash_problems(design, files) == []
    manifest = json.loads(files["manifest.json"])
    assert manifest["design_sha256"] == cc.sha(json.dumps(design, sort_keys=True))
    assert (manifest["provider"], manifest["model"]) == (cc.PROVIDER, cc.MODEL)
    assert run.count_problems(manifest) == []


# Validator columns of sealed smoke rows the removed label fallback resolved
# (``source_path_ref`` in dry_run.json); the frozen scorer columns are unchanged.
FALLBACK_REMOVED = {
    "game_clock_samples[label=death].observation.seconds_remaining = 115": {
        "validator_kind": "unresolved"
    },
    'game_clock_samples[label=death].display = "1:55"': {
        "validator_kind": "unresolved",
        "validator_accepts": False,
    },
}


@pytest.mark.skipif(not SEALED, reason="sealed inputs not present")
def test_sealed_smoke_cases_classify_as_frozen() -> None:
    prompt = (cc.OUT_DIR / "inputs" / f"{cc.SMOKE_UNIT.slug}.user_prompt.txt").read_text(
        encoding="utf-8"
    )
    rows = cc.smoke_test(xd.split_prompt(prompt)[0])
    assert [r["text"] for r in rows if not r["ok"]] == []
    stored = json.loads((cc.OUT_DIR / "dry_run.json").read_text(encoding="utf-8"))
    assert stored["llm_calls_made"] == 0
    changed = [s for s in stored["smoke_test"] if s["text"] in FALLBACK_REMOVED]
    assert [s["validator_kind"] for s in changed] == ["source_path_ref"] * 2
    assert rows == [
        {**s, **FALLBACK_REMOVED.get(s["text"], {})} for s in stored["smoke_test"]
    ]
