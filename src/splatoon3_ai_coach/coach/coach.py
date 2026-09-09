"""Generate structured coaching assessments from CoachInput evidence."""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from splatoon3_ai_coach.analysis.session import GameSession
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.coach.evidence_contract import (
    PROHIBITED_CLAIM_PATTERNS,
    iter_prohibited_claim_hits,
)
from splatoon3_ai_coach.coach.llm_client import (
    CoachingAssessment,
    CoachingOutput,
    LLMProvider,
)
from splatoon3_ai_coach.coach.prompts import load_system_prompt

_USER_PREAMBLE = (
    "CoachInput JSON follows. Return ONLY a JSON object with keys "
    "assessment, evidence_used, limitations, recommendations. "
    "Empty recommendations is valid when evidence is insufficient.\n\n"
)


def serialize_coach_input_user_prompt(coach_input: CoachInput) -> str:
    """Stable byte-identical user prompt for multi-model comparison."""
    payload = coach_input.model_dump(mode="json")
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _USER_PREAMBLE + body


def generate_coaching_assessment(
    coach_input: CoachInput,
    provider: LLMProvider,
    *,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
) -> CoachingAssessment:
    """Run one model on one CoachInput and parse a CoachingAssessment.

    Pass the same ``system_prompt`` and ``user_prompt`` bytes to every model
    under comparison. When omitted, they are loaded/serialized here.
    """
    system = system_prompt if system_prompt is not None else load_system_prompt()
    user = (
        user_prompt
        if user_prompt is not None
        else serialize_coach_input_user_prompt(coach_input)
    )
    logger.info(
        "Generating coaching assessment for {} via {}",
        coach_input.primary_scenario.scenario_id,
        type(provider).__name__,
    )
    raw = provider.complete(system, user)
    return parse_coaching_assessment(raw)


def parse_coaching_assessment(raw: str) -> CoachingAssessment:
    """Parse model JSON into CoachingAssessment; raise on invalid shape."""
    payload = _extract_json_object(raw)
    return CoachingAssessment.model_validate(payload)


def annotate_claim_flags(assessment: CoachingAssessment) -> dict[str, Any]:
    """Experiment-only annotation. Never modifies ``assessment``.

    Returns a separate flags document for human review. ``hit_count`` counts
    only hits classified as prohibited assertions.
    """
    fields = {
        "assessment": assessment.assessment,
        "evidence_used": list(assessment.evidence_used),
        "limitations": list(assessment.limitations),
        "recommendations": list(assessment.recommendations),
    }
    hits: list[dict[str, object]] = []
    for field, value in fields.items():
        texts = value if isinstance(value, list) else [value]
        for text in texts:
            for item in iter_prohibited_claim_hits(str(text), field=field):
                hits.append(
                    {
                        "field": field,
                        "match": item["match"],
                        "text": item["text"],
                        "classification": item["classification"],
                        "counts_as_violation": item["counts_as_violation"],
                    }
                )
    return {
        "prohibited_pattern_hits": hits,
        "hit_count": sum(1 for item in hits if item["counts_as_violation"]),
        "patterns_checked": [pattern.pattern for pattern in PROHIBITED_CLAIM_PATTERNS],
    }


def generate_coaching(
    session: GameSession,
    provider: LLMProvider,
) -> CoachingOutput:
    """Legacy GameSession entry point (stub). Prefer generate_coaching_assessment."""
    _ = provider
    logger.warning(
        "generate_coaching(GameSession) is a legacy stub; use "
        "generate_coaching_assessment(CoachInput) for the prototype"
    )
    _ = session
    return CoachingOutput()


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Accept raw JSON or a fenced ```json block."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fence is not None:
        text = fence.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError(f"CoachingAssessment JSON must be an object, got {type(payload)}")
    return payload
