"""Public factual evidence comes from saved structured claims, not LLM prose."""

from __future__ import annotations

import json
from pathlib import Path

from coach_artifact_helpers import index_entry
from public_site.localize import localize_result
from public_site.results import load_result


def _saved_analysis(
    root: Path,
    *,
    frame_text: str,
    playback: str,
    include_roster: bool = True,
    include_ink: bool = True,
    timing: str | None = "after_death",
    offset: float | None = 4.0,
    assessment: str | None = None,
) -> Path:
    """Write legacy-shaped artifacts with current structured evidence fields."""
    analysis = root / "analysis"
    inputs = analysis / "coach_inputs"
    inputs.mkdir(parents=True)
    entry = index_entry(
        "death_episode_100.0",
        candidate_type="death_episode",
        video_time=100.0,
        rank=1,
        llm_view_json="death_episode_100.0.llm_view.json",
    )
    (inputs / "coaching_index.json").write_text(json.dumps([entry]), encoding="utf-8")
    (inputs / entry["coach_input_json"]).write_text(
        json.dumps(
            {
                "primary_context": {"death_episode": {"death_time": 100.0}},
                "playback_clock": {"status": playback},
                "roster_evidence": include_roster,
                "game_clock_samples": [],
                "durations": {},
            }
        ),
        encoding="utf-8",
    )
    (inputs / entry["coaching_json"]).write_text(
        json.dumps({"factors": []}), encoding="utf-8"
    )

    claims = []
    if include_roster:
        values = {
            "temporal_relationship": "before_death",
            "offset_from_anchor_seconds": -3.5,
            "video_time": 96.5,
            "anchor_video_time": 100.0,
            "ally_alive_count": 4,
            "opponent_alive_count": 4,
            # Historical views retain prose that the player report must ignore.
            "timing_description": frame_text,
        }
        claims.append(
            {"claim_id": "observed_roster_counts", "values": values, "frame": frame_text}
        )
    if include_ink:
        values = {
            "temporal_relationship": timing,
            "offset_from_anchor_seconds": offset,
            "video_time": None if offset is None else 100.0 + offset,
            "anchor_video_time": 100.0,
            "visible_ink_coverage_percent": 29.82,
            "ally_share_of_detected_ink_percent": 48.17,
            "opponent_share_of_detected_ink_percent": 51.83,
            "timing_description": frame_text,
        }
        claims.append(
            {"claim_id": "observed_map_ink", "values": values, "frame": frame_text}
        )
    (inputs / entry["llm_view_json"]).write_text(
        json.dumps({"claim_basis": claims, "roster_evidence": True}), encoding="utf-8"
    )
    if assessment is not None:
        prototype = analysis / "coach_prototype"
        prototype.mkdir()
        (prototype / f"{entry['safe_id']}.model.output.json").write_text(
            json.dumps({"assessment": assessment}), encoding="utf-8"
        )
    return analysis


def _contexts(analysis: Path, locale: str) -> list[str]:
    """Complete rendered Match Evidence lines for a saved public report."""
    result = localize_result(load_result(analysis), locale, analysis, None)
    return result.moments[0].context


def test_saved_english_and_japanese_reports_render_structured_observations(
    tmp_path: Path,
) -> None:
    analysis = _saved_analysis(
        tmp_path,
        frame_text=(
            "At video 104s (4s after death on the recording timeline; "
            "post-death observation)"
        ),
        playback="normal",
    )
    english = _contexts(analysis, "en")
    japanese = _contexts(analysis, "ja")
    assert (
        "3.5 seconds before the death, 4 teammates and 4 opponents were alive." in english
    )
    assert any(
        "4 seconds after the death" in line and "29.82%" in line for line in english
    )
    assert any("Of that ink, 48% belonged to your team" in line for line in english)
    assert any("52% to the opposing team" in line for line in english)
    assert "デスの3.5秒前、味方4人、相手4人が生存していました。" in japanese
    assert any("デスの4秒後" in line and "29.82%" in line for line in japanese)
    for line in [*english, *japanese]:
        lowered = line.lower()
        assert "at video" not in lowered
        assert "recording timeline" not in lowered
        assert "post-death observation" not in lowered
        assert "registered polygon" not in lowered
    assert not any("At video" in line for line in japanese)


def test_public_facts_ignore_legacy_llm_phrasing(tmp_path: Path) -> None:
    first = _saved_analysis(
        tmp_path / "first",
        frame_text="At video 104s (4s after death on the recording timeline)",
        playback="normal",
        assessment="At video 104s, the roster and map observation showed the situation.",
    )
    second = _saved_analysis(
        tmp_path / "second",
        frame_text="At 104 seconds in the recording, map ink was measured after death.",
        playback="normal",
        assessment="The later map view showed the same situation after the death.",
    )
    assert _contexts(first, "en") == _contexts(second, "en")
    assert _contexts(first, "ja") == _contexts(second, "ja")


def test_unverified_playback_omits_precise_offset_but_keeps_order(tmp_path: Path) -> None:
    analysis = _saved_analysis(
        tmp_path,
        frame_text="legacy wording",
        playback="insufficient_clock_evidence",
    )
    english = _contexts(analysis, "en")
    japanese = _contexts(analysis, "ja")
    assert any("Just before the death" in line for line in english)
    assert any("After the death" in line and "29.82%" in line for line in english)
    assert not any("3.5 seconds" in line or "4 seconds" in line for line in english)
    assert any("デス直前" in line for line in japanese)
    assert any("デス後" in line and "29.82%" in line for line in japanese)
    assert not any("3.5秒" in line or "4秒" in line for line in japanese)


def test_missing_observation_or_relation_is_omitted_safely(tmp_path: Path) -> None:
    no_observations = _saved_analysis(
        tmp_path / "none",
        frame_text="At video 100s",
        playback="normal",
        include_roster=False,
        include_ink=False,
    )
    no_timing = _saved_analysis(
        tmp_path / "no_timing",
        frame_text="At video 104s; post-death observation",
        playback="normal",
        include_roster=False,
        timing=None,
    )
    assert _contexts(no_observations, "en") == []
    assert _contexts(no_observations, "ja") == []
    assert _contexts(no_timing, "en") == []
    assert _contexts(no_timing, "ja") == []


def test_missing_observation_timestamp_keeps_only_known_direction(tmp_path: Path) -> None:
    analysis = _saved_analysis(
        tmp_path,
        frame_text="At video 104s",
        playback="normal",
        include_roster=False,
        offset=None,
    )
    english = _contexts(analysis, "en")
    japanese = _contexts(analysis, "ja")
    assert any("After the death" in line and "29.82%" in line for line in english)
    assert not any("4 seconds" in line for line in english)
    assert any("デス後" in line and "29.82%" in line for line in japanese)
    assert not any("4秒" in line for line in japanese)


def test_missing_coverage_and_shares_never_create_percent_placeholders(
    tmp_path: Path,
) -> None:
    analysis = _saved_analysis(
        tmp_path,
        frame_text="legacy wording",
        playback="normal",
        include_roster=False,
    )
    view = analysis / "coach_inputs" / "death_episode_100.0.llm_view.json"
    saved = json.loads(view.read_text(encoding="utf-8"))
    ink = next(
        claim
        for claim in saved["claim_basis"]
        if claim["claim_id"] == "observed_map_ink"
    )
    ink["values"].pop("visible_ink_coverage_percent")
    ink["values"].pop("ally_share_of_detected_ink_percent")
    ink["values"].pop("opponent_share_of_detected_ink_percent")
    view.write_text(json.dumps(saved), encoding="utf-8")
    for locale in ("en", "ja"):
        rendered = " ".join(_contexts(analysis, locale))
        assert "None%" not in rendered
        assert "unavailable%" not in rendered
        assert "不明%" not in rendered
        assert "unavailable" in rendered or "不明" in rendered
