"""Identity of the configuration that produced a coaching ranking.

Every ranking records this payload and its hash, so an old run stays
readable and reproducible after the YAML changes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from splatoon3_ai_coach.config.models import CoachConfig

IMPORTANCE_CONFIG_SCHEMA = 1


def importance_config_payload(
    coach: CoachConfig, *, max_llm_units: int
) -> dict[str, Any]:
    """The importance-relevant configuration, in full.

    ``max_llm_units`` is passed explicitly because a CLI override replaces
    the configured value for that run.
    """
    return {
        "schema": IMPORTANCE_CONFIG_SCHEMA,
        "death_importance_weights": {
            key: float(value)
            for key, value in sorted(coach.death_importance_weights.items())
        },
        "death_factor_thresholds": coach.death_factor_thresholds.model_dump(mode="json"),
        "max_llm_units": int(max_llm_units),
        "llm_units_require_positive_score": bool(coach.llm_units_require_positive_score),
        "death_modifier_factors": sorted(coach.death_modifier_factors),
        "death_ranking_excluded_factors": sorted(coach.death_ranking_excluded_factors),
    }


def importance_config_hash(payload: dict[str, Any]) -> str:
    """Short stable hash of a payload from :func:`importance_config_payload`."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
