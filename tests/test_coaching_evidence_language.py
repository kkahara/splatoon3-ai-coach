"""Player-facing evidence language, LLM-view hygiene and historical compatibility."""

from __future__ import annotations

import json
import re
import string
from pathlib import Path

import pytest
from loguru import logger
from public_site.evidence_text import (
    EVIDENCE_TEXT,
    factor_line,
    observed_ink_line,
    observed_roster_line,
    render_evidence,
)
from public_site.localize import localize_result, template_line
from public_site.models import (
    CoachingMoment,
    EvidenceLine,
    MomentEvidence,
    PublicSubmissionResult,
)
from public_site.results import _context, load_result

from splatoon3_ai_coach.coach.claim_basis import CLAIM_DEFINITIONS
from splatoon3_ai_coach.coach.claim_catalog import CoachingUnitResult, RosterSample
from splatoon3_ai_coach.coach.claim_integrity import video_clock_wording_hits
from splatoon3_ai_coach.coach.claim_validation import (
    ReferenceKind,
    resolve_evidence_reference,
)
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.coach.death_importance import build_supporting_evidence
from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view
from splatoon3_ai_coach.coach.prompts import load_system_prompt

_ANALYSIS = Path(__file__).resolve().parents[1] / "analysis"
_EN_GAME = _ANALYSIS / "en_2026-11-01 15-00-53_supplied_en"
_JA_GAME = _ANALYSIS / "ja-2026-09-20 17-00-29"
_HISTORICAL = [
    _JA_GAME,
    _EN_GAME,
    _ANALYSIS / "en_2026-11-01 15-00-53_supplied_ja",
    _ANALYSIS / "en-2026-07-05 17-34-46/83c9ca06a616494eaa9e9f9a4d177165",
    _ANALYSIS / "en-2026-07-05 17-34-46/52a2fda8bd284f36b38848bc815ed6e6",
]
_REPLAY_VIEW = (
    _ANALYSIS / "coaching_observation_replay_2026-10-30/brinewater_death_64.llm_view.json"
)
_JAPANESE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
_ENGLISH_WORD = re.compile(r"[A-Za-z]{3,}")
_TECHNICAL = (
    "at video",
    "video time",
    "recording timeline",
    "registered polygon",
    "timing_description",
    "source_path",
    "primary_context",
)
_NARRATION_KEYS = (
    "timing_description",
    "ally_share_of_detected_ink_display",
    "opponent_share_of_detected_ink_display",
)
_EN_ONLY = {"ally_noun", "opponent_noun", "seconds_unit"}


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def _sample(key: str) -> EvidenceLine:
    names = _fields(EVIDENCE_TEXT[key]["en"]) - _EN_ONLY
    return EvidenceLine(key=key, params={name: 3 for name in names})


def _units(analysis: Path):
    inputs = analysis / "coach_inputs"
    index = json.loads((inputs / "coaching_index.json").read_text(encoding="utf-8"))
    for entry in index["entries"] if isinstance(index, dict) else index:
        view = entry.get("llm_view_json")
        if not view or not (inputs / view).is_file():
            continue
        coaching = json.loads(
            (inputs / entry["coaching_json"]).read_text(encoding="utf-8")
        )
        yield (
            CoachInput.model_validate_json(
                (inputs / entry["coach_input_json"]).read_text(encoding="utf-8")
            ),
            coaching,
            json.loads((inputs / view).read_text(encoding="utf-8")),
        )


def _require(path: Path) -> Path:
    if not path.exists():
        pytest.skip("Local saved analysis is unavailable")
    return path


def _evidence_texts(moment: CoachingMoment) -> list[str]:
    lines = [moment.until_active_again, moment.recovery_context]
    return [line for line in [*lines, *moment.context, *moment.statements] if line]


def _warnings():
    messages: list[str] = []
    sink = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    return messages, sink


# --- catalog ---------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(EVIDENCE_TEXT))
def test_every_evidence_key_has_english_and_japanese(key: str) -> None:
    entry = EVIDENCE_TEXT[key]
    assert set(entry) == {"en", "ja"}
    assert _fields(entry["ja"]) <= _fields(entry["en"]) - _EN_ONLY
    english = render_evidence(_sample(key), "en")
    japanese = render_evidence(_sample(key), "ja")
    assert english and japanese
    assert _JAPANESE.search(japanese) and not _ENGLISH_WORD.search(japanese)
    assert not _JAPANESE.search(english)
    for phrase in _TECHNICAL:
        assert phrase not in english.lower()


@pytest.mark.parametrize("key", sorted(EVIDENCE_TEXT))
def test_catalog_japanese_matches_the_legacy_template(key: str) -> None:
    """Old and new artifacts read the same Japanese where a legacy line existed."""
    line = _sample(key)
    legacy = template_line(render_evidence(line, "en") or "", "ja")
    if legacy is not None:
        assert legacy == render_evidence(line, "ja")


def test_unknown_evidence_key_is_omitted_and_logged_in_japanese(tmp_path: Path) -> None:
    unknown = EvidenceLine(key="brand_new.fact", params={"value": 1})
    known = EvidenceLine(key="special.ready")
    moment = CoachingMoment(
        scenario_type="death_episode",
        video_time=10.0,
        heading="Death",
        context=["?", "Special was ready."],
        evidence=MomentEvidence(context=[unknown, known], until_active_again=unknown),
    )
    messages, sink = _warnings()
    try:
        result = PublicSubmissionResult(moments=[moment])
        (ja,) = localize_result(result, "ja", tmp_path, None).moments
    finally:
        logger.remove(sink)
    assert ja.context == ["スペシャルが溜まっていました。"]
    assert ja.until_active_again is None
    assert ja.heading == "デス"
    assert any("brand_new.fact" in message for message in messages)
    assert render_evidence(unknown, "en") is None


def test_factor_statement_without_japanese_is_omitted_with_its_key(
    tmp_path: Path,
) -> None:
    line = factor_line("death_new_factor", "A statement nobody templated.")
    moment = CoachingMoment(
        scenario_type="death_episode",
        video_time=10.0,
        statements=["A statement nobody templated."],
        evidence=MomentEvidence(statements=[line]),
    )
    messages, sink = _warnings()
    try:
        result = PublicSubmissionResult(moments=[moment])
        (ja,) = localize_result(result, "ja", tmp_path, None).moments
    finally:
        logger.remove(sink)
    assert ja.statements == []
    assert any("factor.death_new_factor" in message for message in messages)


def test_unkeyed_legacy_evidence_line_is_omitted_not_shown_in_english(
    tmp_path: Path,
) -> None:
    moment = CoachingMoment(
        scenario_type="death_episode",
        video_time=10.0,
        heading="Some new heading",
        context=["A brand-new evidence line.", "Special was ready."],
    )
    messages, sink = _warnings()
    try:
        result = PublicSubmissionResult(moments=[moment])
        (ja,) = localize_result(result, "ja", tmp_path, None).moments
    finally:
        logger.remove(sink)
    assert ja.context == ["スペシャルが溜まっていました。"]
    assert ja.heading == "Some new heading"  # Headings keep their fallback.
    assert any("unkeyed" in message for message in messages)


def test_redeath_game_clock_statement_has_japanese() -> None:
    text = "Two consecutive deaths occurred 12.0 game-clock seconds apart."
    assert template_line(text, "ja") == "12.0秒（試合時間）の間隔で連続デスしています。"


# --- structured observation lines ----------------------------------------------------


def test_roster_and_ink_lines_keep_timing_and_denominators() -> None:
    before = observed_roster_line(
        {
            "temporal_relationship": "before_death",
            "offset_from_anchor_seconds": -1.0,
            "ally_alive_count": 4,
            "opponent_alive_count": 2,
        }
    )
    assert (
        render_evidence(before, "ja") == "デス直前の生存人数は、味方4人、相手2人でした。"
    )
    assert render_evidence(before, "en") == (
        "Just before the death, 4 teammates and 2 opponents were alive."
    )
    one = observed_roster_line(
        {
            "temporal_relationship": "before_death",
            "ally_alive_count": 1,
            "opponent_alive_count": 1,
        }
    )
    assert render_evidence(one, "en") == (
        "Just before the death, 1 teammate and 1 opponent were alive."
    )
    after = observed_ink_line(
        {
            "temporal_relationship": "after_death",
            "visible_ink_coverage_percent": 29.82,
            "ally_share_of_detected_ink_rounded_percent": 48,
            "opponent_share_of_detected_ink_rounded_percent": 52,
        }
    )
    assert render_evidence(after, "ja") == (
        "デス後に、観測対象のマップ範囲の29.82%で塗りが検出されました。"
        "検出された塗りのうち、味方は48%、相手は52%でした。"
    )
    assert render_evidence(after, "en") == (
        "After the death, detectable ink covered 29.82% of the measured map "
        "area. Of that ink, 48% belonged to your team and 52% to the "
        "opposing team."
    )
    assert observed_roster_line({"temporal_relationship": "before_anchor"}) is None


def test_historical_claim_values_render_with_correct_timing() -> None:
    view = json.loads(_require(_REPLAY_VIEW).read_text(encoding="utf-8"))
    assert view["schema_version"] < 6
    claims = {c["claim_id"]: c["values"] for c in view["claim_basis"]}
    roster = observed_roster_line(claims["observed_roster_counts"])
    ink = observed_ink_line(claims["observed_map_ink"])
    assert claims["observed_map_ink"]["temporal_relationship"] == "after_death"
    assert (
        render_evidence(roster, "ja") == "デス直前の生存人数は、味方3人、相手4人でした。"
    )
    assert render_evidence(ink, "ja").startswith(
        "デス後に、観測対象のマップ範囲の26.90%で塗りが検出されました。"
    )
    raw = claims["observed_map_ink"]
    assert ink.params["ally"] == f"{round(raw['ally_share_of_detected_ink_percent'])}%"
    assert ink.params["opponent"] == (
        f"{round(raw['opponent_share_of_detected_ink_percent'])}%"
    )
    assert "before" not in render_evidence(ink, "en")


# --- historical reports ----------------------------------------------------------------


@pytest.mark.parametrize("analysis", _HISTORICAL, ids=lambda p: p.name)
def test_historical_reports_render_in_both_locales(analysis: Path) -> None:
    english = load_result(_require(analysis))
    japanese = localize_result(english, "ja", analysis, None)
    assert english.moments
    for en, ja in zip(english.moments, japanese.moments, strict=True):
        assert len(_evidence_texts(ja)) == len(_evidence_texts(en))  # None omitted.
        for line in _evidence_texts(ja):
            assert _JAPANESE.search(line) and not _ENGLISH_WORD.search(line), line
        for line in _evidence_texts(en):
            assert not _JAPANESE.search(line)
            assert video_clock_wording_hits({"assessment": line}) == []
            for phrase in _TECHNICAL:
                assert phrase not in line.lower(), line


@pytest.mark.parametrize(
    ("analysis", "report"), [(_EN_GAME, "ja"), (_JA_GAME, "en")], ids=["en_ja", "ja_en"]
)
def test_report_language_is_independent_of_game_language(
    analysis: Path, report: str
) -> None:
    english = load_result(_require(analysis))
    result = localize_result(english, report, analysis, None)
    lines = [line for m in result.moments for line in _evidence_texts(m)]
    assert lines
    for line in lines:
        assert bool(_JAPANESE.search(line)) is (report == "ja"), line


@pytest.mark.parametrize("analysis", _HISTORICAL, ids=lambda p: p.name)
def test_each_moment_has_at_most_one_roster_observation(analysis: Path) -> None:
    for moment in load_result(_require(analysis)).moments:
        keys = [line.key for line in moment.evidence.context]
        roster = [k for k in keys if k.startswith(("observed_roster.", "roster."))]
        assert len(roster) <= 1, keys
        if any(k.startswith("observed_roster.") for k in keys):
            assert "factor.death_while_ahead_in_numbers" not in keys
            assert "factor.death_while_outnumbered" not in keys


def test_real_roster_factor_duplicate_is_shown_once() -> None:
    (moment,) = [
        m for m in load_result(_require(_EN_GAME)).moments if m.video_time == 63.5
    ]
    roster = [line for line in moment.context if "alive" in line]
    assert roster == [
        "1.0 second before the death, 4 teammates and 2 opponents were alive."
    ]


def _roster_view(sample_time: float) -> dict:
    return {
        "roster": {
            "observed_counts": {
                "video_time": sample_time,
                "anchor_video_time": sample_time,
                "ally_alive_count": 4,
                "opponent_alive_count": 2,
            }
        },
        "claim_basis": [
            {
                "claim_id": "observed_roster_counts",
                "values": {
                    "temporal_relationship": "before_death",
                    "offset_from_anchor_seconds": 0.0,
                    "ally_alive_count": 4,
                    "opponent_alive_count": 2,
                    "video_time": sample_time,
                    "anchor_video_time": sample_time,
                },
            }
        ],
        "schema_version": 5,
    }


_ROSTER_FACTOR = {
    "factor_id": "death_while_ahead_in_numbers",
    "active": True,
    "statement_player": "Just before you were splatted, your team had 4 players "
    "alive and the opponents had 2.",
}
_SPLAT_FACTOR = {
    "factor_id": "death_own_splat_before",
    "active": True,
    "statement_player": "You got a splat 2.0s before you were splatted.",
}


def test_roster_dedup_uses_sample_identity_not_text() -> None:
    sample = {"video_time": 62.5, "ally_alive_count": 4, "opponent_alive_count": 2}
    factors = [_ROSTER_FACTOR, _SPLAT_FACTOR]
    same = _context({}, factors, _roster_view(62.5), sample)
    other = _context({}, factors, _roster_view(60.0), sample)
    assert [line.key for line in same] == [
        "factor.death_own_splat_before",
        "observed_roster.before_death",
    ]
    assert "factor.death_while_ahead_in_numbers" in [line.key for line in other]
    assert "factor.death_own_splat_before" in [line.key for line in other]


# --- LLM view hygiene and provenance ---------------------------------------------------


@pytest.mark.parametrize("analysis", _HISTORICAL, ids=lambda p: p.name)
def test_new_views_carry_no_narration_or_duplicate_support(analysis: Path) -> None:
    for coach_input, coaching, _stored in _units(_require(analysis)):
        unit = CoachingUnitResult.model_validate(coaching)
        view = build_coach_llm_view(coach_input, unit).model_dump(mode="json")
        text = json.dumps(view)
        assert view["schema_version"] == 6
        assert "supporting_evidence" not in view
        for phrase in (
            "recording timeline",
            "registered polygon",
            "At video",
            *_NARRATION_KEYS,
        ):
            assert phrase not in text
        assert coaching["supporting_evidence"]  # Still kept for the viewer.
        for label in ("Death anchor", "Roster before death", "Observed roster"):
            assert label not in text
        assert "fill=" not in text
        for claim in view["claim_basis"]:
            assert "video_time" not in _fields(claim["frame"])
            if claim["claim_id"].startswith("observed_"):
                assert not {"video_time", "anchor_video_time"} & set(claim["values"])


def test_no_claim_frame_or_prompt_teaches_recording_clock_wording() -> None:
    for claim in CLAIM_DEFINITIONS:
        assert not {f for f in _fields(claim.frame) if "video_time" in f}, claim.claim_id
    prompt = load_system_prompt(roster_evidence=True)
    for phrase in (
        "recording timeline",
        "registered polygon",
        "At video time",
        "by 43.0s",
    ):
        assert phrase not in prompt


@pytest.mark.parametrize("analysis", _HISTORICAL, ids=lambda p: p.name)
def test_claim_evidence_paths_resolve_in_new_and_stored_views(analysis: Path) -> None:
    for coach_input, coaching, stored in _units(_require(analysis)):
        unit = CoachingUnitResult.model_validate(coaching)
        new = build_coach_llm_view(coach_input, unit).model_dump(mode="json")
        for view in (new, stored):
            for claim in view["claim_basis"]:
                for path in claim["evidence_paths"]:
                    value = _walk(view, path)
                    cited = resolve_evidence_reference(
                        view, f"{path} = {json.dumps(value)}"
                    )
                    assert cited["kind"] == ReferenceKind.VIEW_NATIVE.value, (
                        analysis.name,
                        claim["claim_id"],
                        path,
                    )


def _walk(view: dict, path: str) -> object:
    value: object = view
    for part in re.findall(r"[^.\[\]]+", path):
        value = value[int(part)] if isinstance(value, list) else value[part]
    assert not isinstance(value, dict | list), path
    return value


@pytest.mark.parametrize("analysis", _HISTORICAL, ids=lambda p: p.name)
def test_selection_provenance_and_support_are_unchanged(analysis: Path) -> None:
    """Rebuilt views differ from the stored v5 views only where intended."""
    for coach_input, coaching, stored in _units(_require(analysis)):
        unit = CoachingUnitResult.model_validate(coaching)
        new = build_coach_llm_view(coach_input, unit).model_dump(mode="json")
        assert _stable(new) == _stable(stored)
        for block, key in (("roster", "observed_counts"), ("map", "observed_ink")):
            old = (stored.get(block) or {}).get(key)
            fresh = (new.get(block) or {}).get(key)
            assert (old is None) is (fresh is None)
            if old is not None:
                kept = {k: v for k, v in old.items() if k not in _NARRATION_KEYS}
                assert {k: fresh[k] for k in kept} == kept  # Raw leaves unchanged.
        old_paths = {
            c["claim_id"]: {
                p for p in c["evidence_paths"] if not p.endswith(_NARRATION_KEYS)
            }
            for c in stored["claim_basis"]
        }
        for claim in new["claim_basis"]:
            assert old_paths[claim["claim_id"]] <= set(claim["evidence_paths"])
        roster = coaching.get("roster_before_death")
        rebuilt = build_supporting_evidence(
            coach_input, roster=RosterSample(**roster) if roster else None
        )
        assert [i.model_dump(mode="json") for i in rebuilt] == coaching[
            "supporting_evidence"
        ]


def _stable(view: dict) -> dict:
    copy = json.loads(json.dumps(view))
    copy.pop("schema_version", None)
    copy.pop("supporting_evidence", None)
    for block, key in (("roster", "observed_counts"), ("map", "observed_ink")):
        if isinstance(copy.get(block), dict):
            copy[block].pop(key, None)
    copy["claim_basis"] = [
        {k: c[k] for k in ("claim_id", "level", "coachable", "authorizes", "rec_frames")}
        for c in copy["claim_basis"]
    ]
    return copy


def test_structured_roster_offset_and_provenance_are_localized() -> None:
    values = {
        "temporal_relationship": "before_death",
        "offset_from_anchor_seconds": -2.0,
        "anchor_video_time": 60.0,
        "ally_alive_count": 3,
        "opponent_alive_count": 3,
    }
    view = {
        "roster": {
            "observed_counts": {
                **values,
                "video_time": 58.0,
                "anchor_video_time": 60.0,
                "source_path": "primary_context.players.trajectory[0]",
            }
        },
        "claim_basis": [{"claim_id": "observed_roster_counts", "values": values}],
    }
    normal = {"playback_clock": {"status": "normal"}, "roster_evidence": True}
    (line,) = _context(normal, [], view)
    assert render_evidence(line, "en") == (
        "2.0 seconds before the death, 3 teammates and 3 opponents were alive."
    )
    assert render_evidence(line, "ja") == (
        "デスの2.0秒前、味方3人、相手3人が生存していました。"
    )
    assert line.provenance == view["roster"]["observed_counts"]
    (unknown_speed,) = _context({"roster_evidence": True}, [], view)
    assert "seconds" not in render_evidence(unknown_speed, "en")
    assert unknown_speed.provenance == line.provenance
    after = observed_roster_line(
        {
            **values,
            "temporal_relationship": "after_death",
            "offset_from_anchor_seconds": 1.0,
        },
        include_offset=True,
    )
    assert render_evidence(after, "en").startswith("1.0 second after")
    assert "1.0秒後" in render_evidence(after, "ja")


def test_localized_score_ink_special_recovery_and_map_without_translator(
    tmp_path: Path,
) -> None:
    score = {
        "video_time": 57.5,
        "ally_remaining": 34,
        "opponent_remaining": 61,
        "ally_score_quality": "observed",
        "opponent_score_quality": "observed",
        "evidence_ids": ["score-reading-id"],
    }
    coach_input = {
        "primary_context": {
            "score": {"pre_death": score},
            "special": {
                "state_at_anchor": "not_ready",
                "nearest_before_anchor": {"visible": True},
            },
            "map": {
                "map_check_status": "observed",
                "seconds_since_map_check_before_death": 3.5,
            },
        },
        "playback_clock": {"status": "normal"},
        "roster_evidence": False,
    }
    factors = [
        {
            "factor_id": "death_while_ahead_in_count",
            "active": True,
            "statement_player": "stale prose that must not be used",
        }
    ]
    ink_values = {
        "temporal_relationship": "after_death",
        "visible_ink_coverage_percent": 35.0,
        "ally_share_of_detected_ink_percent": 55.6,
        "opponent_share_of_detected_ink_percent": 44.4,
    }
    view = {
        "map": {
            "observed_ink": {
                **ink_values,
                "video_time": 62.0,
                "source_path": "primary_context.map.ink.observations[0]",
            }
        },
        "claim_basis": [{"claim_id": "observed_map_ink", "values": ink_values}],
    }
    lines = _context(coach_input, factors, view)
    score_line = next(line for line in lines if line.key == "score.pre_death")
    assert score_line.params == {"ally": 34, "opponent": 61}
    assert score_line.provenance["evidence_ids"] == ["score-reading-id"]
    assert score_line.provenance["video_time"] == 57.5
    ink = next(line for line in lines if line.key.startswith("observed_ink."))
    assert ink.provenance == view["map"]["observed_ink"]
    recovery = EvidenceLine(key="recovery.until_respawn", params={"seconds": "7.0"})
    evidence = MomentEvidence(context=lines, until_active_again=recovery)
    en = CoachingMoment(
        scenario_type="death_episode",
        video_time=60,
        heading="Death",
        context=[render_evidence(line, "en") for line in lines],
        until_active_again=render_evidence(recovery, "en"),
        evidence=evidence,
    )
    ja = localize_result(
        PublicSubmissionResult(moments=[en]), "ja", tmp_path, None
    ).moments[0]
    assert (
        "Before the death, the remaining counts were 34 for allies and 61 for opponents."
        in en.context
    )
    assert "デス前の残りカウントは、味方34、相手61でした。" in ja.context
    assert any("56%" in text and "44%" in text for text in ja.context)
    assert any("スペシャル" in text for text in ja.context)
    assert any("3.5秒前" in text for text in ja.context)
    assert ja.until_active_again == "復帰まで7.0秒"
    assert all(
        _JAPANESE.search(text) and not _ENGLISH_WORD.search(text)
        for text in [*ja.context, ja.until_active_again]
    )
    assert all(
        not _JAPANESE.search(text) for text in [*en.context, en.until_active_again]
    )
    assert ja.evidence == evidence


def test_roster_factor_uses_its_saved_sample_instead_of_english_prose() -> None:
    sample = {
        "video_time": 58.0,
        "ally_alive_count": 3,
        "opponent_alive_count": 1,
        "source_path": "primary_context.players.trajectory[2]",
    }
    coach_input = {
        "primary_context": {"death_episode": {"death_time": 60.0}},
        "roster_evidence": False,
        "playback_clock": {"status": "normal"},
    }
    factor = {**_ROSTER_FACTOR, "statement_player": "incorrect English 4 versus 2"}
    (line,) = _context(coach_input, [factor], {}, sample)
    assert (
        render_evidence(line, "ja")
        == "デスの2.0秒前、味方3人、相手1人が生存していました。"
    )
    assert render_evidence(line, "en") == (
        "2.0 seconds before the death, 3 teammates and 1 opponent were alive."
    )
    assert line.provenance["source_path"] == sample["source_path"]
    assert line.provenance["video_time"] == 58.0
