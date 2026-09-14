"""Player and developer VMV text for coaching unit results."""

from __future__ import annotations

from splatoon3_ai_coach.coach.claim_catalog import (
    NO_RECOMMENDATION_MESSAGE,
    CoachingUnitResult,
)
from splatoon3_ai_coach.coach.coaching_candidates import ImportanceFactorContribution


def format_vmv_player(result: CoachingUnitResult) -> str:
    """Player-facing view: score, active factors, supporting evidence."""
    header = (
        f"{result.candidate_type} @ {_fmt_mmss(result.video_time)}\n"
    )
    active = [f for f in result.factors if f.active]
    if not result.selected_for_llm:
        body = (
            "Coaching\n"
            "  Not selected for LLM (below top-N importance rank).\n\n"
            f"{_score_block(result)}\n\n"
            f"{_factors_block_player(active)}\n\n"
            f"{_support_block(result)}"
        )
        return header + "\n" + body.strip() + "\n"

    blocks = [
        header,
        _score_block(result),
        "",
        _factors_block_player(active),
        "",
        _support_block(result),
    ]
    return "\n".join(blocks).rstrip() + "\n"


def format_vmv_developer(result: CoachingUnitResult) -> str:
    """Developer/eval view with scores, factors, and supporting evidence."""
    lines = [
        f"{result.candidate_type} @ {_fmt_mmss(result.video_time)}",
        f"candidate_id: {result.candidate_id}",
        f"candidate_type: {result.candidate_type}",
        f"importance_score: {result.importance_score}",
        f"rank: {result.rank}",
        f"selected_for_llm: {result.selected_for_llm}",
        f"match_duration_seconds: {result.match_duration_seconds}",
        "",
        "factors:",
    ]
    for factor in result.factors:
        mark = "active" if factor.active else "inactive"
        lines.append(
            f"  - {factor.factor_id}: {mark} "
            f"weight={factor.weight} contribution={factor.contribution}"
        )
        if factor.active and factor.statement_player:
            lines.append(f"    statement: {factor.statement_player}")
            lines.append(f"    statement_internal: {_null(factor.statement_internal)}")
            lines.append(f"    interpretation: {_null(factor.interpretation)}")
            lines.append(f"    recommendation: {_null(factor.recommendation)}")
    lines.append("")
    lines.append(_support_block_dev(result))
    return "\n".join(lines).rstrip() + "\n"


def _score_block(result: CoachingUnitResult) -> str:
    return (
        "Importance\n"
        f"  score: {result.importance_score}\n"
        f"  rank: {result.rank}\n"
        f"  selected_for_llm: {result.selected_for_llm}"
    )


def _factors_block_player(active: list[ImportanceFactorContribution]) -> str:
    lines = ["Importance factors"]
    if not active:
        lines.append("  (none active)")
        return "\n".join(lines)
    for factor in active:
        lines.append(f"  • {factor.factor_id}: +{factor.contribution}")
        if factor.statement_player:
            lines.append(f"      {factor.statement_player}")
        if factor.interpretation:
            lines.append(f"      Interpretation: {factor.interpretation}")
        if factor.recommendation:
            lines.append(f"      Recommendation: {factor.recommendation}")
        elif factor.statement_player and factor.recommendation is None:
            # Statement-only factors (e.g. special ready).
            if factor.interpretation is None:
                lines.append(f"      Coaching: {NO_RECOMMENDATION_MESSAGE}")
    return "\n".join(lines)


def _support_block(result: CoachingUnitResult) -> str:
    lines = ["Supporting evidence"]
    if not result.supporting_evidence:
        lines.append("  (none)")
        return "\n".join(lines)
    for item in result.supporting_evidence:
        lines.append(f"  • {item.label}: {item.value}")
    return "\n".join(lines)


def _support_block_dev(result: CoachingUnitResult) -> str:
    lines = ["Supporting evidence"]
    for item in result.supporting_evidence:
        path = f" ({item.path})" if item.path else ""
        lines.append(f"  - {item.label}: {item.value}{path}")
    return "\n".join(lines)


def _null(value: str | None) -> str:
    return "null" if value is None else value


def _fmt_mmss(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60}:{total % 60:02d}"
