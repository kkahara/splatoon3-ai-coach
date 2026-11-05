"""Player-facing evidence lines: one English and one Japanese rendering per key.

Lines are built from structured evidence (stored claim values, coach input
fields), never by parsing English. Roster and score factors use their saved
samples when available. Legacy factor statements fall back to deterministic
templates over their stored English text.

No recording-clock times, evidence ids or detector terms appear here, and a
pre-death observation is never worded as post-death (or the reverse).
"""

from __future__ import annotations

import math
from typing import Literal

from public_site.models import EvidenceLine
from splatoon3_ai_coach.coach.observation_facts import rounded_percent

EvidenceLocale = Literal["en", "ja"]
FACTOR_PREFIX = "factor."

_TIMINGS = ("before_death", "at_death", "after_death")

EVIDENCE_TEXT: dict[str, dict[EvidenceLocale, str]] = {
    "special.ready": {
        "en": "Special was ready.",
        "ja": "スペシャルが溜まっていました。",
    },
    "special.unconfirmed": {
        "en": "Special readiness just before the death was not confirmed.",
        "ja": "デスの直前にスペシャルが溜まっていたかは確認できませんでした。",
    },
    "special.not_ready": {
        "en": "Special was not ready just before the death.",
        "ja": "デスの直前、スペシャルは溜まっていませんでした。",
    },
    "special.not_observed": {
        "en": "The special gauge was not visible just before the death.",
        "ja": "デスの直前はスペシャルゲージが見えていませんでした。",
    },
    "map.unknown_around_death": {
        "en": "Map-check evidence was unavailable around this death.",
        "ja": "このデス前後のマップ確認は判定できませんでした。",
    },
    "map.unknown_for_video": {
        "en": "Map-check evidence was unavailable for this video.",
        "ja": "この動画ではマップ確認の有無を判定できませんでした。",
    },
    "map.not_observed": {
        "en": "No map check observed before death",
        "ja": "デス前のマップ確認は見られませんでした。",
    },
    "map.observed": {
        "en": "A map check was observed before this death.",
        "ja": "このデスの前にマップ確認が見られました。",
    },
    "map.observed_seconds_before": {
        "en": "Last observed map check: {seconds}s before death",
        "ja": "最後のマップ確認: デスの{seconds}秒前",
    },
    "recovery.map_checked": {
        "en": "The player checked the map during recovery.",
        "ja": "復帰待ちの間にマップを確認しています。",
    },
    "recovery.out_of_play_game_clock": {
        "en": "{seconds}s out of play (game clock)",
        "ja": "{seconds}秒間 戦線離脱（試合時間）",
    },
    "recovery.out_of_play": {
        "en": "{seconds}s out of play",
        "ja": "{seconds}秒間 戦線離脱",
    },
    "recovery.until_respawn_game_clock": {
        "en": "{seconds}s until respawn (game clock)",
        "ja": "復帰まで{seconds}秒（試合時間）",
    },
    "recovery.until_respawn": {
        "en": "{seconds}s until respawn",
        "ja": "復帰まで{seconds}秒",
    },
    "roster.unreliable": {
        "en": "Roster information around this death was unreliable.",
        "ja": "このデス前後の人数情報は信頼できませんでした。",
    },
    "roster.unavailable": {
        "en": "No roster information was available.",
        "ja": "人数の情報はありませんでした。",
    },
    "roster.at_death_counts": {
        "en": "Roster at death: {ally}v{opponent}.",
        "ja": "デス時の人数: 味方{ally} 対 相手{opponent}。",
    },
    "observed_roster.before_death": {
        "en": "Just before the death, {ally} {ally_noun} and {opponent} "
        "{opponent_noun} were alive.",
        "ja": "デス直前の生存人数は、味方{ally}人、相手{opponent}人でした。",
    },
    "observed_roster.at_death": {
        "en": "At the death, {ally} {ally_noun} and {opponent} {opponent_noun} "
        "were alive.",
        "ja": "デス時の生存人数は、味方{ally}人、相手{opponent}人でした。",
    },
    "observed_roster.after_death": {
        "en": "Shortly after the death, {ally} {ally_noun} and {opponent} "
        "{opponent_noun} were alive.",
        "ja": "デス直後の生存人数は、味方{ally}人、相手{opponent}人でした。",
    },
    "observed_roster.offset_before_death": {
        "en": "{seconds} {seconds_unit} before the death, {ally} {ally_noun} and "
        "{opponent} {opponent_noun} were alive.",
        "ja": "デスの{seconds}秒前、味方{ally}人、相手{opponent}人が生存していました。",
    },
    "observed_roster.offset_after_death": {
        "en": "{seconds} {seconds_unit} after the death, {ally} {ally_noun} and "
        "{opponent} {opponent_noun} were alive.",
        "ja": "デスの{seconds}秒後、味方{ally}人、相手{opponent}人が生存していました。",
    },
    "score.pre_death": {
        "en": "Before the death, the remaining counts were {ally} for allies "
        "and {opponent} for opponents.",
        "ja": "デス前の残りカウントは、味方{ally}、相手{opponent}でした。",
    },
    "observed_ink.before_death": {
        "en": "Before the death, detectable ink covered {coverage} of the "
        "measured map area. Of that ink, {ally} belonged to your team and "
        "{opponent} to the opposing team.",
        "ja": "デス前に、観測対象のマップ範囲の{coverage}で塗りが検出されました。"
        "検出された塗りのうち、味方は{ally}、相手は{opponent}でした。",
    },
    "observed_ink.at_death": {
        "en": "At the death, detectable ink covered {coverage} of the measured "
        "map area. Of that ink, {ally} belonged to your team and {opponent} "
        "to the opposing team.",
        "ja": "デス時に、観測対象のマップ範囲の{coverage}で塗りが検出されました。"
        "検出された塗りのうち、味方は{ally}、相手は{opponent}でした。",
    },
    "observed_ink.after_death": {
        "en": "After the death, detectable ink covered {coverage} of the "
        "measured map area. Of that ink, {ally} belonged to your team and "
        "{opponent} to the opposing team.",
        "ja": "デス後に、観測対象のマップ範囲の{coverage}で塗りが検出されました。"
        "検出された塗りのうち、味方は{ally}、相手は{opponent}でした。",
    },
    "observed_ink.offset_after_death": {
        "en": "{seconds} {seconds_unit} after the death, detectable ink "
        "covered {coverage} of the measured map area. Of that ink, {ally} "
        "belonged to your team and {opponent} to the opposing team.",
        "ja": "デスの{seconds}秒後に、観測対象のマップ範囲の{coverage}で塗りが検出されました。"
        "検出された塗りのうち、味方は{ally}、相手は{opponent}でした。",
    },
    "observed_ink_unavailable.before_death": {
        "en": "Before the death, the map observation did not include a "
        "measurable ink coverage value. Of the detected ink, {ally} belonged "
        "to your team and {opponent} to the opposing team.",
        "ja": "デス前のマップ観測では、塗りの範囲を測定できませんでした。"
        "検出された塗りのうち、味方は{ally}、相手は{opponent}でした。",
    },
    "observed_ink_unavailable.at_death": {
        "en": "At the death, the map observation did not include a measurable "
        "ink coverage value. Of the detected ink, {ally} belonged to your "
        "team and {opponent} to the opposing team.",
        "ja": "デス時のマップ観測では、塗りの範囲を測定できませんでした。"
        "検出された塗りのうち、味方は{ally}、相手は{opponent}でした。",
    },
    "observed_ink_unavailable.after_death": {
        "en": "After the death, the map observation did not include a "
        "measurable ink coverage value. Of the detected ink, {ally} belonged "
        "to your team and {opponent} to the opposing team.",
        "ja": "デス後のマップ観測では、塗りの範囲を測定できませんでした。"
        "検出された塗りのうち、味方は{ally}、相手は{opponent}でした。",
    },
}


def render_evidence(line: EvidenceLine, locale: EvidenceLocale) -> str | None:
    """Text for ``line`` in ``locale``; ``None`` when no rendering exists."""
    if line.key.startswith(FACTOR_PREFIX):
        return _factor_text(line, locale)
    template = EVIDENCE_TEXT.get(line.key, {}).get(locale)
    if template is None:
        return None
    try:
        return template.format(**_locale_params(line.params, locale))
    except (KeyError, IndexError, ValueError):
        return None


def factor_line(factor_id: str, statement: str) -> EvidenceLine:
    """A stored English factor statement, keyed by its factor id."""
    return EvidenceLine(key=f"{FACTOR_PREFIX}{factor_id}", params={"text": statement})


def observed_roster_line(
    values: object, *, include_offset: bool = False
) -> EvidenceLine | None:
    """Roster counts from a stored ``observed_roster_counts`` claim (v5 or v6)."""
    if not isinstance(values, dict):
        return None
    timing = _timing(values)
    ally = _count(values.get("ally_alive_count"))
    opponent = _count(values.get("opponent_alive_count"))
    if timing is None or ally is None or opponent is None:
        return None
    params: dict[str, str | int | float | None] = {"ally": ally, "opponent": opponent}
    offset = values.get("offset_from_anchor_seconds")
    if (
        include_offset
        and isinstance(offset, (int, float))
        and not isinstance(offset, bool)
        and math.isfinite(offset)
        and (
            (timing == "before_death" and offset < 0)
            or (timing == "after_death" and offset > 0)
        )
    ):
        timing = f"offset_{timing}"
        params["seconds"] = str(abs(offset))
    return EvidenceLine(key=f"observed_roster.{timing}", params=params)


def observed_ink_line(
    values: object, *, include_offset: bool = False
) -> EvidenceLine | None:
    """Detected-ink shares from a stored ``observed_map_ink`` claim (v5 or v6)."""
    if not isinstance(values, dict):
        return None
    timing = _timing(values)
    if timing is None:
        return None
    ally, opponent = _share(values, "ally"), _share(values, "opponent")
    coverage_raw = values.get("visible_ink_coverage_percent")
    if coverage_raw is None:
        coverage_raw = values.get("visible_ink_coverage_rounded_percent")
    coverage = _percentage(coverage_raw)
    params: dict[str, str | int | float | None] = {
        "ally": None if ally is None else f"{ally}%",
        "opponent": None if opponent is None else f"{opponent}%",
        "coverage": None if coverage is None else f"{coverage}%",
    }
    if coverage is None:
        return EvidenceLine(
            key=f"observed_ink_unavailable.{timing}", params=params
        )
    if include_offset:
        offset = values.get("offset_from_anchor_seconds")
        if isinstance(offset, (int, float)) and not isinstance(offset, bool) and math.isfinite(offset):
            expected = {
                "before_death": offset < 0,
                "at_death": offset == 0,
                "after_death": offset > 0,
            }[timing]
            if expected and timing == "after_death":
                params["seconds"] = f"{offset:g}"
                return EvidenceLine(key="observed_ink.offset_after_death", params=params)
    return EvidenceLine(key=f"observed_ink.{timing}", params=params)


def _has_sample_timestamps(values: dict) -> bool:
    """Precise relative wording requires both stored timestamps."""
    return all(
        isinstance(values.get(key), (int, float))
        and not isinstance(values.get(key), bool)
        and math.isfinite(values[key])
        for key in ("video_time", "anchor_video_time")
    )


def _factor_text(line: EvidenceLine, locale: EvidenceLocale) -> str | None:
    structured_key = line.params.get("template_key")
    if isinstance(structured_key, str) and structured_key in EVIDENCE_TEXT:
        return render_evidence(line.model_copy(update={"key": structured_key}), locale)
    text = line.params.get("text")
    if not isinstance(text, str) or not text:
        return None
    if locale == "en":
        return text
    from public_site.localize import template_line

    return template_line(text, locale)


def _locale_params(params: dict, locale: EvidenceLocale) -> dict:
    unavailable = "unavailable" if locale == "en" else "不明"
    params = {
        key: unavailable if value is None and key in {"coverage", "ally", "opponent"} else value
        for key, value in params.items()
    }
    if locale != "en":
        return params
    nouns = {
        "ally_noun": "teammate" if params.get("ally") == 1 else "teammates",
        "opponent_noun": "opponent" if params.get("opponent") == 1 else "opponents",
        "seconds_unit": "second"
        if params.get("seconds") in (1, 1.0, "1", "1.0")
        else "seconds",
    }
    return {**params, **nouns}


def _timing(values: dict) -> str | None:
    relation = values.get("temporal_relationship")
    if relation is None:
        relation = values.get("timing")
    relation = {
        "before": "before_death",
        "at": "at_death",
        "after": "after_death",
    }.get(relation, relation)
    return relation if relation in _TIMINGS else None


def _share(values: dict, side: str) -> int | None:
    rounded = f"{side}_share_of_detected_ink_rounded_percent"
    if rounded in values:
        value = _count(values[rounded])
        return value if value is None or 0 <= value <= 100 else None
    raw = values.get(f"{side}_share_of_detected_ink_percent")
    if (
        isinstance(raw, bool)
        or not isinstance(raw, (int, float))
        or not math.isfinite(raw)
        or not 0 <= raw <= 100
    ):
        return None
    return rounded_percent(raw)


def _percentage(value: object) -> str | None:
    """Format a bounded, finite percentage or preserve it as unavailable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or not 0 <= value <= 100:
        return None
    return f"{value:.2f}"


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
