"""Report-level advice-family cap over ranked coaching units (experimental).

Runs after ranking and before LLM selection. It changes only
``selected_for_llm``: scores, ranks, factors, claim eligibility and evidence
are untouched, and every unit stays in the report. An advice family is a
recommendation template a unit's eligible claim_basis already authorizes.

A selected unit whose families are all taken by a higher-ranked selected unit
is suppressed. A freed slot is filled only by a positive-score unit offering a
family no selected unit offers yet; fact-only units are never promoted.
Suppression is a selection outcome, not a redundancy verdict, so each
suppressed unit carries a factual comparison of its evidence with the
retained units.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field

from splatoon3_ai_coach.analysis.scenario_evidence import SpecialGaugeState
from splatoon3_ai_coach.coach.claim_basis import build_claim_basis
from splatoon3_ai_coach.coach.claim_catalog import CoachingUnitResult
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.coach.death_importance import own_splat_gap
from splatoon3_ai_coach.coach.observation_facts import observed_map_ink

ADVICE_FAMILY_CAP_POLICY = "advice_family_cap_v1"
REPORT_DIVERSITY_SCHEMA_VERSION = 1
REPORT_DIVERSITY_FILENAME = "report_diversity.json"

SelectionAction = Literal["kept", "suppressed", "promoted", "not_selected"]
SelectionReason = Literal[
    "kept_unused_advice_family",
    "kept_fact_only",
    "suppressed_repeated_advice_family",
    "promoted_unused_advice_family",
    "not_promoted_fact_only",
    "not_promoted_repeated_advice_family",
    "not_promoted_zero_score",
    "not_promoted_slots_full",
]
EvidenceStatus = Literal[
    "not_observed", "same_value_in_retained", "different_from_retained"
]
EVIDENCE_CATEGORIES = ("objective", "roster", "map_ink", "special", "combat")
_INK_PERCENTS = (
    "visible_ink_coverage_percent",
    "ally_share_of_detected_ink_percent",
    "opponent_share_of_detected_ink_percent",
)


class EvidenceComparison(BaseModel):
    """One evidence category of a suppressed unit against the retained units."""

    category: str
    status: EvidenceStatus
    suppressed: dict[str, Any] | None = None
    retained: dict[str, dict[str, Any]] = Field(default_factory=dict)


class MomentSelection(BaseModel):
    """Selection outcome of one unit under the advice-family cap."""

    candidate_id: str
    video_time: float
    rank: int | None
    importance_score: float
    advice_families: list[str]
    baseline_selected: bool
    selected: bool
    action: SelectionAction
    reason: SelectionReason
    repeats_family_of: list[str] = Field(default_factory=list)
    distinct_evidence: list[EvidenceComparison] = Field(default_factory=list)
    coachable_claims_not_in_retained: list[str] = Field(default_factory=list)


class ReportDiversityDiagnostics(BaseModel):
    """Experiment diagnostics for one report (written only when the cap is on)."""

    schema_version: int = REPORT_DIVERSITY_SCHEMA_VERSION
    policy: str = ADVICE_FAMILY_CAP_POLICY
    max_llm_units: int
    baseline_selected: list[str]
    selected: list[str]
    moments: list[MomentSelection]

    @property
    def changed(self) -> bool:
        """True when the cap selected a different set of units."""
        return set(self.baseline_selected) != set(self.selected)


def advice_families(coach_input: CoachInput, unit: CoachingUnitResult) -> list[str]:
    """Recommendation templates the unit's eligible claim_basis authorizes."""
    basis = build_claim_basis(coach_input, unit)
    return sorted({t for entry in basis for t in entry.rec_template_ids})


def apply_advice_family_cap(
    units: Sequence[CoachingUnitResult],
    coach_inputs: Mapping[str, CoachInput],
    *,
    max_llm_units: int,
) -> tuple[list[CoachingUnitResult], ReportDiversityDiagnostics]:
    """Re-mark ``selected_for_llm`` under the cap; everything else is copied."""
    ordered = sorted(units, key=lambda u: (u.rank is None, u.rank or 0, u.candidate_id))
    families = {
        u.candidate_id: advice_families(coach_inputs[u.candidate_id], u) for u in ordered
    }
    moments = _select(ordered, families, max_llm_units)
    selected = {m.candidate_id for m in moments if m.selected}
    _compare_suppressed(moments, ordered, coach_inputs, selected)
    remarked = [
        u.model_copy(update={"selected_for_llm": u.candidate_id in selected})
        for u in units
    ]
    diagnostics = ReportDiversityDiagnostics(
        max_llm_units=max_llm_units,
        baseline_selected=[u.candidate_id for u in ordered if u.selected_for_llm],
        selected=[u.candidate_id for u in ordered if u.candidate_id in selected],
        moments=moments,
    )
    return remarked, diagnostics


def _select(
    ordered: Sequence[CoachingUnitResult],
    families: Mapping[str, list[str]],
    max_llm_units: int,
) -> list[MomentSelection]:
    """Walk units in rank order; baseline units precede every backfill candidate."""
    owner: dict[str, str] = {}
    chosen = 0
    moments: list[MomentSelection] = []
    for unit in ordered:
        fams = families[unit.candidate_id]
        repeats = (
            sorted({owner[f] for f in fams})
            if fams and all(f in owner for f in fams)
            else []
        )
        action, reason = _decide(unit, fams, repeats, slots_free=chosen < max_llm_units)
        selected = action in ("kept", "promoted")
        if selected:
            chosen += 1
            for family in fams:
                owner.setdefault(family, unit.candidate_id)
        moments.append(
            MomentSelection(
                candidate_id=unit.candidate_id,
                video_time=float(unit.video_time),
                rank=unit.rank,
                importance_score=float(unit.importance_score),
                advice_families=fams,
                baseline_selected=bool(unit.selected_for_llm),
                selected=selected,
                action=action,
                reason=reason,
                repeats_family_of=repeats if action != "kept" else [],
            )
        )
    return moments


def _decide(
    unit: CoachingUnitResult, fams: list[str], repeats: list[str], *, slots_free: bool
) -> tuple[SelectionAction, SelectionReason]:
    if unit.selected_for_llm:
        if repeats:
            return "suppressed", "suppressed_repeated_advice_family"
        return "kept", ("kept_unused_advice_family" if fams else "kept_fact_only")
    if not slots_free:
        return "not_selected", "not_promoted_slots_full"
    if float(unit.importance_score) <= 0:
        return "not_selected", "not_promoted_zero_score"
    if not fams:
        return "not_selected", "not_promoted_fact_only"
    if repeats:
        return "not_selected", "not_promoted_repeated_advice_family"
    return "promoted", "promoted_unused_advice_family"


def _compare_suppressed(
    moments: list[MomentSelection],
    ordered: Sequence[CoachingUnitResult],
    coach_inputs: Mapping[str, CoachInput],
    selected: set[str],
) -> None:
    by_id = {u.candidate_id: u for u in ordered}
    retained = [u for u in ordered if u.candidate_id in selected]
    summaries = {
        u.candidate_id: evidence_summary(coach_inputs[u.candidate_id], u) for u in ordered
    }
    retained_claims = {
        c for u in retained for c in _coachable_claims(coach_inputs[u.candidate_id], u)
    }
    for moment in moments:
        if moment.action != "suppressed":
            continue
        unit = by_id[moment.candidate_id]
        own = summaries[unit.candidate_id]
        moment.distinct_evidence = [
            _comparison(category, own[category], retained, summaries)
            for category in EVIDENCE_CATEGORIES
        ]
        moment.coachable_claims_not_in_retained = sorted(
            set(_coachable_claims(coach_inputs[unit.candidate_id], unit))
            - retained_claims
        )


def _comparison(
    category: str,
    value: dict[str, Any] | None,
    retained: Sequence[CoachingUnitResult],
    summaries: Mapping[str, dict[str, dict[str, Any] | None]],
) -> EvidenceComparison:
    others = {
        u.candidate_id: summaries[u.candidate_id][category]
        for u in retained
        if summaries[u.candidate_id][category] is not None
    }
    if value is None:
        status: EvidenceStatus = "not_observed"
    elif value in others.values():
        status = "same_value_in_retained"
    else:
        status = "different_from_retained"
    return EvidenceComparison(
        category=category, status=status, suppressed=value, retained=others
    )


def _coachable_claims(coach_input: CoachInput, unit: CoachingUnitResult) -> list[str]:
    return [e.claim_id for e in build_claim_basis(coach_input, unit) if e.coachable]


def evidence_summary(
    coach_input: CoachInput, unit: CoachingUnitResult
) -> dict[str, dict[str, Any] | None]:
    """Observed values per evidence category; ``None`` when nothing was observed."""
    return {
        "objective": _objective(coach_input),
        "roster": _roster(coach_input, unit),
        "map_ink": _map_ink(coach_input),
        "special": _special(coach_input),
        "combat": _combat(coach_input),
    }


def _compact(values: dict[str, Any]) -> dict[str, Any] | None:
    kept = {k: v for k, v in values.items() if v not in (None, [], {})}
    return kept or None


def _objective(coach_input: CoachInput) -> dict[str, Any] | None:
    score, zone, temporal = (
        coach_input.score_facts,
        coach_input.zone_control_facts,
        coach_input.objective_temporal_facts,
    )
    values: dict[str, Any] = {}
    if score is not None:
        for name in (
            "ally_remaining",
            "opponent_remaining",
            "ally_penalty",
            "opponent_penalty",
        ):
            values[name] = getattr(score, name, None)
    if zone is not None:
        values["control_before_death"] = zone.control_before_death
        values["control_at_death"] = zone.control_at_death
    if temporal is not None:
        values["temporal_facts"] = sorted(
            name
            for name in (
                "number_advantage_without_progress",
                "sole_ally_alive_during_progress",
                "progress_then_stop_after_death",
            )
            if getattr(temporal, name) is not None
        )
    return _compact(values)


def _roster(coach_input: CoachInput, unit: CoachingUnitResult) -> dict[str, Any] | None:
    sample = unit.roster_before_death
    if not coach_input.roster_evidence or sample is None:
        return None
    return {
        "ally_alive_count": sample.ally_alive_count,
        "opponent_alive_count": sample.opponent_alive_count,
    }


def _map_ink(coach_input: CoachInput) -> dict[str, Any] | None:
    values = observed_map_ink(coach_input)
    if values is None:
        return None
    relation = values.get("temporal_relationship")
    summary: dict[str, Any] = {
        "timing": relation.split("_", 1)[0] if isinstance(relation, str) else None
    }
    for name in _INK_PERCENTS:
        raw = values.get(name)
        summary[f"{name}_rounded"] = None if raw is None else int(round(float(raw)))
    return _compact(summary)


def _special(coach_input: CoachInput) -> dict[str, Any] | None:
    special = coach_input.primary_context.special
    state = None if special is None else special.state_at_anchor
    if state is None or state == SpecialGaugeState.NOT_OBSERVED:
        return None
    return {"state_at_anchor": str(state.value if hasattr(state, "value") else state)}


def _combat(coach_input: CoachInput) -> dict[str, Any] | None:
    gap = own_splat_gap(coach_input)
    engagement = next(
        (r for r in coach_input.related if r.role == "preceded_by_engagement"), None
    )
    combat = None if engagement is None else engagement.context.combat
    return _compact(
        {
            "own_splat_gap_seconds": None if gap is None else round(gap * 2) / 2,
            "preceding_engagement_splats": None if combat is None else combat.splat_count,
        }
    )
