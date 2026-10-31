"""Conservative temporal facts derived from observed Clam basket-open runs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from splatoon3_ai_coach.analysis.basket_open_runs import (
    BasketOpenRun,
    BasketOpenRunsArtifact,
    BasketSample,
)
from splatoon3_ai_coach.vision.canonical import canonical_json, sha256_hex

ArtifactAvailability = Literal["available", "partial"]
FactAvailability = Literal["available", "unavailable"]
ActionRelation = Literal[
    "at_or_before_preceding_closed_observation",
    "within_opening_transition_bounds",
    "at_observed_open_boundary",
    "between_observed_open_boundaries_continuity_unproven",
    "within_closing_transition_bounds",
    "at_or_after_following_closed_observation",
    "indeterminate_left_censored",
    "indeterminate_right_censored",
]


class BasketTransitionBounds(BaseModel):
    """Transition time is unresolved after lower, but occurred by upper: `(l, u]`."""

    lower_time_exclusive: float = Field(ge=0)
    upper_time_inclusive: float = Field(ge=0)
    lower_frame_id: str
    upper_frame_id: str
    lower_evidence_ids: list[str] = Field(min_length=1)
    upper_evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_order(self) -> BasketTransitionBounds:
        """Require a nonempty, forward transition window."""
        if self.lower_time_exclusive >= self.upper_time_inclusive:
            raise ValueError("transition bounds must increase")
        return self


class _BasketTemporalFact(BaseModel):
    """Shared source identity without implying artifact-wide fact quality."""

    schema_version: Literal[1] = 1
    analysis_id: str
    source_manifest_sha256: str
    battle_mode_id: Literal["clam_blitz"] = "clam_blitz"
    source_run_id: str
    source_artifact_availability: ArtifactAvailability
    source_artifact_limitations: list[str] = Field(default_factory=list)
    fact_availability: FactAvailability
    fact_limitations: list[str] = Field(default_factory=list)
    source_evidence_ids: list[str] = Field(min_length=1)


class BasketObservedOpenSpan(_BasketTemporalFact):
    """Elapsed video span between boundary open observations, not open duration."""

    fact_availability: Literal["available"] = "available"
    first_observed_open_time: float = Field(ge=0)
    last_observed_open_time: float = Field(ge=0)
    observed_span_seconds: float = Field(ge=0)
    opening_transition_bounds: BasketTransitionBounds | None = None
    closing_transition_bounds: BasketTransitionBounds | None = None
    left_censor_reason: str | None = None
    right_censor_reason: str | None = None

    @model_validator(mode="after")
    def validate_span(self) -> BasketObservedOpenSpan:
        """Keep the named span equal to its observed boundary arithmetic."""
        expected = self.last_observed_open_time - self.first_observed_open_time
        if expected < 0 or self.observed_span_seconds != expected:
            raise ValueError("observed span must equal last minus first observation")
        return self


class BasketClosingTimeBounds(_BasketTemporalFact):
    """Bounds on a closing transition, or an explicit censored result."""

    bounds: BasketTransitionBounds | None = None
    right_censor_reason: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> BasketClosingTimeBounds:
        """Keep a bounded closing distinct from a censored unavailable one."""
        if self.fact_availability == "available":
            if self.bounds is None or self.right_censor_reason is not None:
                raise ValueError("available closing bounds require bounds only")
        elif self.bounds is not None or self.right_censor_reason is None:
            raise ValueError("unavailable closing bounds require censoring only")
        return self


class BasketActionTemporalRelation(_BasketTemporalFact):
    """Conservative relation of one supplied timestamp to observed run bounds."""

    fact_availability: Literal["available"] = "available"
    action_reference_id: str
    action_video_time: float = Field(ge=0)
    relation: ActionRelation


def basket_source_run_id(artifact: BasketOpenRunsArtifact, run: BasketOpenRun) -> str:
    """Return deterministic identity from source artifact and run provenance."""
    _validate_source(artifact, run)
    identity = {
        "analysis_id": artifact.analysis_id,
        "source_manifest_sha256": artifact.source_manifest_sha256,
        "run": run.model_dump(mode="json"),
    }
    return f"basket-open-run:{sha256_hex(canonical_json(identity))}"


def build_basket_observed_open_span(
    artifact: BasketOpenRunsArtifact, run: BasketOpenRun
) -> BasketObservedOpenSpan:
    """Build an observed boundary span without asserting continuous openness."""
    common = _common(artifact, run)
    first = run.first_observed_open.video_time
    last = run.last_observed_open.video_time
    return BasketObservedOpenSpan(
        **common,
        fact_availability="available",
        first_observed_open_time=first,
        last_observed_open_time=last,
        observed_span_seconds=last - first,
        opening_transition_bounds=_opening_bounds(run),
        closing_transition_bounds=_closing_bounds(run),
        left_censor_reason=run.left_censor_reason,
        right_censor_reason=run.right_censor_reason,
    )


def build_basket_closing_time_bounds(
    artifact: BasketOpenRunsArtifact, run: BasketOpenRun
) -> BasketClosingTimeBounds:
    """Build closing bounds when observed, otherwise preserve right censoring."""
    common = _common(artifact, run)
    bounds = _closing_bounds(run)
    if bounds is not None:
        return BasketClosingTimeBounds(
            **common, fact_availability="available", bounds=bounds
        )
    return BasketClosingTimeBounds(
        **common,
        fact_availability="unavailable",
        fact_limitations=["closing_transition_right_censored"],
        right_censor_reason=run.right_censor_reason,
    )


def build_basket_action_temporal_relation(
    artifact: BasketOpenRunsArtifact,
    run: BasketOpenRun,
    *,
    action_reference_id: str,
    action_video_time: float,
) -> BasketActionTemporalRelation:
    """Classify a supplied timestamp without looking up or interpreting an event."""
    relation = _classify_action_time(run, action_video_time)
    limitations = (
        ["relation_limited_by_censored_run_boundary"]
        if relation.startswith("indeterminate_")
        else []
    )
    return BasketActionTemporalRelation(
        **_common(artifact, run),
        fact_availability="available",
        fact_limitations=limitations,
        action_reference_id=action_reference_id,
        action_video_time=action_video_time,
        relation=relation,
    )


def _common(artifact: BasketOpenRunsArtifact, run: BasketOpenRun) -> dict:
    """Build shared provenance while preserving source and fact availability."""
    return {
        "analysis_id": artifact.analysis_id,
        "source_manifest_sha256": artifact.source_manifest_sha256,
        "source_run_id": basket_source_run_id(artifact, run),
        "source_artifact_availability": artifact.availability,
        "source_artifact_limitations": list(artifact.limitations),
        "source_evidence_ids": _evidence_ids(run),
    }


def _validate_source(artifact: BasketOpenRunsArtifact, run: BasketOpenRun) -> None:
    """Reject non-Clam, unavailable, or foreign run inputs."""
    if artifact.battle_mode_id != "clam_blitz":
        raise ValueError("basket temporal facts require resolved Clam Blitz mode")
    if artifact.availability == "unavailable":
        raise ValueError("unavailable basket run artifacts cannot produce facts")
    if not any(candidate == run for candidate in artifact.runs):
        raise ValueError("run is not present in the source artifact")


def _evidence_ids(run: BasketOpenRun) -> list[str]:
    """Collect ordered, unique observation evidence IDs used by the fact."""
    samples = [
        run.preceding_closed,
        run.first_observed_open,
        run.last_observed_open,
        run.following_closed,
    ]
    return list(
        dict.fromkeys(
            evidence_id
            for sample in samples
            if sample is not None
            for evidence_id in sample.evidence_ids
        )
    )


def _bounds(lower: BasketSample, upper: BasketSample) -> BasketTransitionBounds:
    """Make explicit `(lower observation, upper observation]` bounds."""
    return BasketTransitionBounds(
        lower_time_exclusive=lower.video_time,
        upper_time_inclusive=upper.video_time,
        lower_frame_id=lower.frame_id,
        upper_frame_id=upper.frame_id,
        lower_evidence_ids=lower.evidence_ids,
        upper_evidence_ids=upper.evidence_ids,
    )


def _opening_bounds(run: BasketOpenRun) -> BasketTransitionBounds | None:
    """Return opening bounds only when a preceding closed sample exists."""
    if run.preceding_closed is None:
        return None
    return _bounds(run.preceding_closed, run.first_observed_open)


def _closing_bounds(run: BasketOpenRun) -> BasketTransitionBounds | None:
    """Return closing bounds only when a following closed sample exists."""
    if run.following_closed is None:
        return None
    return _bounds(run.last_observed_open, run.following_closed)


def _classify_action_time(run: BasketOpenRun, action_time: float) -> ActionRelation:
    """Relate time to bounds; exact observations take precedence over windows."""
    first = run.first_observed_open.video_time
    last = run.last_observed_open.video_time
    if action_time == first or action_time == last:
        return "at_observed_open_boundary"
    if action_time < first:
        if run.preceding_closed is None:
            return "indeterminate_left_censored"
        if action_time <= run.preceding_closed.video_time:
            return "at_or_before_preceding_closed_observation"
        return "within_opening_transition_bounds"
    if action_time < last:
        return "between_observed_open_boundaries_continuity_unproven"
    if run.following_closed is None:
        return "indeterminate_right_censored"
    if action_time < run.following_closed.video_time:
        return "within_closing_transition_bounds"
    return "at_or_after_following_closed_observation"
