#!/usr/bin/env python3
"""Export the citation-contract fixtures (read-only over ``analysis/``).

Each fixture is one real death unit: the CoachInput and its scored
``CoachingUnitResult`` under the production config (roster evidence off).
``tests/test_citation_contract.py`` rebuilds the view from them with the
current projection code, so projection changes reach the contract test.

The units are fixed and chosen for the evidence blocks they carry; ``export``
refuses if a unit no longer has them. The Experiment I unit also carries its
sealed ``map_awareness`` facts, copied from Experiment I ``design.json``
(recorded by sha256; the experiment directory is only read).

Usage::

    citation_contract_fixtures.py export [--out DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "reports"))

from importance_report import _score  # noqa: E402
from loguru import logger  # noqa: E402

from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view  # noqa: E402
from splatoon3_ai_coach.config.loader import load_config  # noqa: E402
from splatoon3_ai_coach.config.paths import (  # noqa: E402
    PROJECT_ROOT,
    default_config_path,
)

ANALYSIS_ROOT = PROJECT_ROOT / "analysis"
MATCH_PREFIX = "en-live-playerview-"
DEFAULT_OUT = PROJECT_ROOT / "tests" / "fixtures" / "citation_contract"
EXPERIMENT_I = ANALYSIS_ROOT / "importance_report" / "coaching_units_experiment_i"
EXPERIMENT_I_UNIT = ("2026-10-11 22-57-30", "death_episode:308.000")


def _get(node: Any, *keys: str) -> Any:
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node


def _leads_to(view: Mapping[str, Any]) -> bool:
    related = view.get("related") or []
    return any(_get(r, "relations", "leads_to_death_episode_id") for r in related)


FEATURES: dict[str, Callable[[Mapping[str, Any]], bool]] = {
    "death_to_respawn_value": lambda v: (
        _get(v, "death", "death_to_respawn", "value") is not None
    ),
    "time_since_previous_death_value": lambda v: (
        _get(v, "timeline", "time_since_previous_death", "value") is not None
    ),
    "related_leads_to": _leads_to,
    "low_ink": lambda v: v.get("low_ink") is not None,
    "playback": lambda v: v.get("playback") is not None,
    "score": lambda v: v.get("score") is not None,
    "zone_control": lambda v: v.get("zone_control") is not None,
    "special_ready": lambda v: _get(v, "special", "state_at_anchor") == "ready",
    "map_status": lambda v: (
        _get(v, "map", "map_check_before_death", "status") is not None
    ),
}

UNITS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "2026-10-10 23-43-31",
        "death_episode:205.000",
        (
            "related_leads_to",
            "low_ink",
            "playback",
            "death_to_respawn_value",
            "time_since_previous_death_value",
        ),
    ),
    (
        "2026-10-11 22-37-47",
        "death_episode:58.000",
        ("score", "zone_control", "special_ready"),
    ),
    (*EXPERIMENT_I_UNIT, ("map_status", "death_to_respawn_value")),
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture_id(match: str, candidate_id: str) -> str:
    """File stem, e.g. ``2026-10-11_22-57-30_death_episode_308.000``."""
    return f"{match.replace(' ', '_')}_{candidate_id.replace(':', '_')}"


def experiment_i_facts() -> dict[str, Any]:
    """Sealed ``map_awareness`` facts for the Experiment I unit, with provenance."""
    path = EXPERIMENT_I / "design.json"
    raw = path.read_bytes()
    match, cid = EXPERIMENT_I_UNIT
    death = next(
        d
        for d in json.loads(raw)["deaths"]
        if d["match"] == match and d["scenario_id"] == cid
    )
    return {
        "source": str(path.relative_to(PROJECT_ROOT)),
        "source_sha256": _sha(raw),
        "facts": death,
    }


def sealed_view_a(match: str, cid: str) -> dict[str, Any] | None:
    """Experiment I sealed arm-A view for the unit, decoded from its user prompt."""
    stem = f"{match}_{cid}".replace(" ", "_").replace("-", "_").replace(":", "_")
    path = EXPERIMENT_I / "inputs" / "A" / f"{stem.replace('.', '_')}.user_prompt.txt"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    return json.JSONDecoder().raw_decode(text[text.index("{") :])[0]


def export_unit(
    match: str, cid: str, features: tuple[str, ...], scoring: Any, config: Any
) -> dict[str, Any]:
    """One fixture entry; raises if the unit lost a feature it was chosen for."""
    unit = next(u for u in scoring.units if u.candidate_id == cid)
    coach_input = scoring.coach_inputs[cid]
    thresholds = config.coach.death_factor_thresholds
    view = build_coach_llm_view(coach_input, unit, thresholds).model_dump(mode="json")
    missing = [name for name in features if not FEATURES[name](view)]
    if missing:
        raise SystemExit(f"{match} {cid}: missing chosen features {missing}")
    entry: dict[str, Any] = {
        "id": fixture_id(match, cid),
        "source": {"match": match, "candidate_id": cid},
        "chosen_for": list(features),
        "coach_input": coach_input.model_dump(mode="json"),
        "unit": unit.model_dump(mode="json"),
    }
    if (match, cid) == EXPERIMENT_I_UNIT:
        entry["experiment_i_map_awareness"] = experiment_i_facts()
        entry["equals_experiment_i_sealed_view_a"] = sealed_view_a(match, cid) == view
    return entry


def export(out: Path) -> int:
    """Write every fixture in ``UNITS`` to ``out``."""
    config = load_config(default_config_path())
    if config.coach.roster_evidence:
        raise SystemExit("production config must have coach.roster_evidence=false")
    out.mkdir(parents=True, exist_ok=True)
    for match, cid, features in UNITS:
        _, scoring = _score(ANALYSIS_ROOT / f"{MATCH_PREFIX}{match}", config)
        entry = export_unit(match, cid, features, scoring, config)
        path = out / f"{entry['id']}.json"
        path.write_text(json.dumps(entry, indent=1, sort_keys=True) + "\n")
        logger.info("wrote {}", path)
    return 0


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("export")
    cmd.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    return export(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
