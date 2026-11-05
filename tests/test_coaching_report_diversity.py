"""Coaching diversity experiment tooling (census, prepare, run, assemble, analyze)."""

from __future__ import annotations

import csv
import json

import coaching_report_diversity as xd
import coaching_report_diversity_run as xrun
import pytest
from public_site.models import CoachingMoment, PublicSubmissionResult

from splatoon3_ai_coach.coach.llm_client import LLMProvider
from splatoon3_ai_coach.coach.llm_runs import LlmRun

_SAVED = "analysis/en-live-playerview-2026-10-11 22-57-30"
_REPLY = json.dumps(
    {
        "assessment": "You were splatted again shortly after respawning.",
        "recommendations": [
            "After being splatted, take a moment to reset before re-engaging."
        ],
        "selected_claim_ids": ["death_quick_redeath", "principle_redeath_out_of_play"],
    }
)


class _FakeProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        return _REPLY


def _moment(t: float, state: str, recs: list[str], heading: str) -> CoachingMoment:
    return CoachingMoment(
        scenario_type="death_episode",
        video_time=t,
        heading=heading,
        coaching_state=state,
        assessment="note" if state != "not_reviewed" else None,
        recommendations=recs,
        context=[f"fact at {t}"],
    )


def test_arm_config_changes_only_the_flag():
    base = xd.base_config()
    capped = xd.arm_config(base, "capped")
    assert capped.coach.report_advice_family_cap is True
    assert xd.arm_config(base, "current").coach.report_advice_family_cap is False
    assert capped.coach.model_dump(
        exclude={"report_advice_family_cap"}
    ) == base.coach.model_dump(exclude={"report_advice_family_cap"})
    assert capped.vision == base.vision


def test_freeze_lists_exclusions_with_reasons(tmp_path):
    source = tmp_path / "canonical.json"
    source.write_text(
        json.dumps(
            [
                "analysis/a",
                "analysis/4e8f661b1a7846fb9471a78ba5e33835",
                "analysis/en-2026-07-05 17-34-46/x",
            ]
        )
    )
    frozen = json.loads(xd.freeze(source, tmp_path).read_text())
    assert frozen["analyses"] == ["analysis/a"]
    assert {e["analysis"] for e in frozen["excluded"]} == {
        "analysis/4e8f661b1a7846fb9471a78ba5e33835",
        "analysis/en-2026-07-05 17-34-46/x",
    }
    with pytest.raises(SystemExit):
        xd.freeze(source, tmp_path)


def test_packet_mirrors_the_review_page_sections():
    rec = "After being splatted, take a moment to reset before re-engaging."
    result = PublicSubmissionResult(
        moments=[
            _moment(10, "recommendation", [rec], "Splatted at 4:50"),
            _moment(20, "recommendation", [rec], "Splatted at 4:40"),
            _moment(30, "not_reviewed", [], "Splatted at 4:30"),
        ]
    )
    text = xrun.render_packet(result, "A")
    assert "Coaching recommendations for 2 of 3 detected moments." in text
    assert text.count(f"1. {rec}") == 1 and "2. " not in text.split("## Key")[0]
    assert "## Key coaching moments" in text and "## Other detected moments" in text
    assert "No coaching recommendation for this moment" not in text
    assert text.count("- fact at") == 3


def test_blocked_prose_is_withheld_but_evidence_stays():
    moment = _moment(10, "recommendation", ["x"], "Splatted")
    moment.assessment_status = "blocked_unsupported_claim"
    text = xrun.render_packet(PublicSubmissionResult(moments=[moment]), "B")
    assert "What to work on" not in text and "- fact at 10" in text


def test_sheet_has_every_measure_for_both_versions():
    cols = xrun.sheet_columns()
    assert cols[0] == "blind_id" and cols[-2:] == ["preferred_A_B_tie", "notes"]
    assert len(cols) == 2 + 2 * len(xrun.MEASURES) + 1


@pytest.fixture
def experiment(tmp_path):
    if not (xd.PROJECT_ROOT / _SAVED / "scenario_contexts.json").is_file():
        pytest.skip("Local saved analysis is unavailable")
    xd.dump_json(tmp_path / "recordings.json", {"analyses": [_SAVED], "excluded": []})
    return tmp_path


def test_end_to_end_with_a_fake_provider(experiment):
    out = experiment
    summary = xd.census(out)
    assert summary["changed_reports"] == 1 and summary["invariance_problems"] == 0
    findings = (out / "key" / "evidence_findings.md").read_text()
    assert "suppressed video 119.0s" in findings
    for category in ("objective", "roster", "map_ink", "special", "combat"):
        assert f"  - {category}: " in findings
    plan = xd.prepare(out)
    assert {tuple(c["arms"]) for c in plan["calls"]} <= {
        ("current",),
        ("capped",),
        ("current", "capped"),
    }
    assert sum(c["arms"] == ["current"] for c in plan["calls"]) == 1

    def refuse(_cfg):
        raise AssertionError("no provider without --live")

    assert xrun.run(out, live=False, factory=refuse) == 0
    assert not (out / "outputs").exists()
    fake = _FakeProvider()
    assert xrun.run(out, live=True, factory=lambda _cfg: LlmRun("fake-model", fake)) == 0
    assert fake.calls == len(plan["calls"])
    assert xrun.run(out, live=True, factory=lambda _cfg: LlmRun("fake-model", fake)) == 0
    assert fake.calls == len(plan["calls"])

    key_rows = xrun.assemble(out)
    assert [r["blind_id"] for r in key_rows] == ["R1"]
    assert {key_rows[0]["A"], key_rows[0]["B"]} == set(xd.ARMS)
    for version in "AB":
        text = (out / "blind" / "R1" / f"{version}.md").read_text()
        assert "current" not in text and "capped" not in text
    checks = json.loads((out / "key" / "automatic_checks.json").read_text())["reports"][
        "r01"
    ]
    assert checks["evidence_identical"] is True
    assert checks["checks"]["current"]["moments"] == checks["checks"]["capped"]["moments"]
    with pytest.raises(SystemExit):
        xrun.assemble(out)

    sheet = out / "filled.csv"
    row = {c: "" for c in xrun.sheet_columns()} | {
        "blind_id": "R1",
        "preferred_A_B_tie": "A",
    }
    for version in "AB":
        for name, scale in xrun.MEASURES:
            row[f"{version}_{name}_{scale}"] = "y" if scale == "yn" else "3"
    with sheet.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    result = xrun.analyze(out, sheet)
    assert result["preferred"] == {key_rows[0]["A"]: 1}
