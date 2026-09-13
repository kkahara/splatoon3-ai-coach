"""Player and developer VMV text for coaching unit results."""

from __future__ import annotations

from splatoon3_ai_coach.coach.claim_catalog import (
    NO_RECOMMENDATION_MESSAGE,
    CoachingPoint,
    CoachingUnitResult,
)


def format_vmv_player(result: CoachingUnitResult) -> str:
    """Player-facing card(s); uses 'No recommendation…' when advice is empty."""
    header = (
        f"{result.scenario_type} @ {_fmt_mmss(result.video_time)}\n"
    )
    if not result.coaching_points:
        body = (
            "Coaching\n"
            "  No coaching point selected for this scenario.\n\n"
            f"{_support_block(result)}"
        )
        return header + "\n" + body.strip() + "\n"

    blocks = [header]
    for point in result.coaching_points:
        blocks.append(_player_point(point))
        blocks.append("")
    blocks.append(_support_block(result))
    return "\n".join(blocks).rstrip() + "\n"


def format_vmv_developer(result: CoachingUnitResult) -> str:
    """Developer/eval view with claim IDs and explicit null triad fields."""
    lines = [
        f"{result.scenario_type} @ {_fmt_mmss(result.video_time)}",
        f"scenario_id: {result.scenario_id}",
        f"eligible: {[cid.value for cid in result.eligible_claim_ids]}",
        f"match_duration_seconds: {result.match_duration_seconds}",
        "",
    ]
    if not result.coaching_points:
        lines.append("coaching_points: []")
        lines.append("")
        lines.append(_support_block_dev(result))
        return "\n".join(lines).rstrip() + "\n"

    for point in result.coaching_points:
        lines.extend(
            [
                f"Claim: {point.claim_id.value}",
                f"Statement: {point.statement}",
                f"statement_internal: {point.statement_internal}",
                f"Interpretation: {_null(point.interpretation)}",
                f"Recommendation: {_null(point.recommendation)}",
                f"evidence_paths: {point.evidence_paths}",
                "",
            ]
        )
    lines.append(_support_block_dev(result))
    return "\n".join(lines).rstrip() + "\n"


def _player_point(point: CoachingPoint) -> str:
    lines = [
        "Statement",
        f"  {point.statement}",
        "",
    ]
    if point.interpretation or point.recommendation:
        if point.interpretation:
            lines.extend(["Interpretation", f"  {point.interpretation}", ""])
        if point.recommendation:
            lines.extend(["Recommendation", f"  {point.recommendation}"])
        else:
            lines.extend(["Coaching", f"  {NO_RECOMMENDATION_MESSAGE}"])
    else:
        lines.extend(["Coaching", f"  {NO_RECOMMENDATION_MESSAGE}"])
    return "\n".join(lines).rstrip()


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
