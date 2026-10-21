"""Experiment J stop point 3: arms, prompts, call plan, ablation and checks.

Research only; no model calls. Everything here is fixed by the frozen
``DESIGN.md`` / ``design.json`` and the sealed stop point 2 populations
(``populations.json``). Arms:

* ``A``: production view (roster off) and production system prompt.
* ``M``: production + ``back_in_play`` + ``map_awareness`` + ``extra_point``
  (the experiment-only ``no_map_check_since_back_in_play`` claim grounding
  ``rec_map_after_respawn`` on eligible deaths), production system prompt +
  the extra-point section with the M lines.
* ``C``: production + ``back_in_play`` + ``extra_point`` naming the death's
  natural C claim, production system prompt + the extra-point section.

M-minus-map is not an arm: ``ablate`` derives it from each M output. The
output checks are frozen here before any output exists.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import coaching_units_experiment_d as xd
import coaching_units_experiment_i as xi
import coaching_units_experiment_j_census as xc
from importance_report import _score

from splatoon3_ai_coach.coach.claim_basis import RECOMMENDATION_TEMPLATES
from splatoon3_ai_coach.coach.coach import (
    annotate_claim_flags,
    serialize_llm_view_user_prompt,
)
from splatoon3_ai_coach.coach.evidence_contract import split_sentences
from splatoon3_ai_coach.coach.llm_client import CoachingAssessment
from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view
from splatoon3_ai_coach.coach.prompts import load_system_prompt
from splatoon3_ai_coach.config.models import AppConfig

POPULATIONS = xc.POPULATIONS
ARMS = ("A", "M", "C")
REPEATS = 2
POPULATION_ARMS = {"P1": ("A", "M"), "P2": ("A", "M"), "P3": ("M",), "C": ("C",)}
MIN_C = 5
MAP_CLAIM_ID = "no_map_check_since_back_in_play"
MAP_CLAIM_FRAME = (
    "No map check was observed in the {seconds_back_in_play}s since the player "
    "was back in play."
)
MAP_EVIDENCE_PATHS = (
    "back_in_play.seconds_back_in_play",
    "map_awareness.map_checks_since_back_in_play",
)
MAP_TEMPLATE = "rec_map_after_respawn"
REPOSITION_TEMPLATE = "rec_after_splat_reposition"
REPOSITION_FRAME = "After you get a splat, reposition before taking the next fight."
REPOSITION_PATTERN = re.compile(
    r"(?i)\breposition\w*\b|\bfall\s+back\b|\b(?:safer|new)\s+(?:spot|position|angle)\b"
)
C_MENU = (
    ("death_low_ink", "rec_refill_ink"),
    ("death_own_splat_before", REPOSITION_TEMPLATE),
)
SPLITTER = re.compile(r"(?<=[.!?])\s+")
MAP_REGEX = re.compile(xi._MAP_MENTION.pattern + r"|\bmap\b", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")

SECTION_HEAD = "ONE ADDITIONAL COACHING POINT (extra_point block)\n"
SECTION_BACK_IN_PLAY = (
    "- back_in_play.seconds_back_in_play is the time from when the player was\n"
    "  last back in play (previous respawn, or match start) to this death.\n"
)
SECTION_M_LINES = (
    "- map_awareness.map_checks_since_back_in_play counts map checks the player\n"
    "  opened while alive in that time. The respawn map is not counted.\n"
    "- Do not describe what the map would have shown (opponent positions, ink,\n"
    "  routes); it was not observed.\n"
)
SECTION_REST = (
    "- extra_point.claim_id names one claim_basis entry for this death.\n"
    "  extra_point.rec_template_id names the recommendation for it.\n"
    "- When extra_point is present, coach it as one additional point: state the\n"
    "  claim's fact with its value from the view in one assessment sentence,\n"
    "  and give one recommendation reworded from that entry's rec_frames.\n"
    "- Do not say the death happened because of it.\n"
    "- When extra_point is null, give no additional point.\n"
)


# --- populations --------------------------------------------------------------


def load_json(path: Path) -> Any:
    """A sealed JSON artifact."""
    return json.loads(path.read_text(encoding="utf-8"))


def population_deaths(
    populations: Mapping[str, Any], census: Mapping[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    """P1, P2, P3 and the natural C deaths as census records, in death order."""
    by_id = {xc.death_id(d): d for d in census["deaths"]}
    c_ids = populations["c_diagnostic"]["natural_deaths"]
    out = {name: [by_id[i] for i in populations[name]] for name in ("P1", "P2", "P3")}
    out["C"] = [by_id[i] for i in c_ids] if len(c_ids) >= MIN_C else []
    return {k: sorted(v, key=xc.death_key) for k, v in out.items()}


def c_claim(record: Mapping[str, Any]) -> tuple[str, str]:
    """The death's C claim and its recommendation template (menu priority)."""
    return next((c, t) for c, t in C_MENU if c == record["c_point"])


# --- views --------------------------------------------------------------------


def production_views(
    analysis: Path, cids: Sequence[str], config: AppConfig
) -> dict[str, tuple[dict[str, Any], str]]:
    """Production view JSON and user prompt per death (roster off)."""
    _, scoring = _score(analysis, config)
    thresholds = config.coach.death_factor_thresholds
    out = {}
    for unit in scoring.units:
        if unit.candidate_id not in cids:
            continue
        view = build_coach_llm_view(
            scoring.coach_inputs[unit.candidate_id], unit, thresholds
        )
        out[unit.candidate_id] = (
            view.model_dump(mode="json"),
            serialize_llm_view_user_prompt(view),
        )
    return out


def back_in_play_block(record: Mapping[str, Any]) -> dict[str, Any]:
    """``back_in_play`` (M and C)."""
    return {
        "source": record["back_in_play_source"],
        "seconds_back_in_play": record["seconds_back_in_play"],
    }


def map_awareness_block(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """``map_awareness`` (M only); null when map observability is insufficient."""
    if not record["map_observable"]:
        return None
    return {
        "map_checks_since_back_in_play": record["map_checks_since_back_in_play"],
        "seconds_since_alive_map_check": record["seconds_since_alive_map_check"],
        "respawn_grace_seconds": xi.RESPAWN_GRACE,
        "threshold_seconds": xi.THRESHOLD,
        "eligible": bool(record["eligible"]),
    }


def map_claim(record: Mapping[str, Any]) -> dict[str, Any]:
    """The experiment-only claim_basis entry (M, eligible deaths)."""
    template = RECOMMENDATION_TEMPLATES[MAP_TEMPLATE]
    return {
        "authorizes": [],
        "claim_id": MAP_CLAIM_ID,
        "coachable": True,
        "evidence_paths": list(MAP_EVIDENCE_PATHS),
        "frame": MAP_CLAIM_FRAME,
        "level": "A",
        "rec_frames": [template.frame],
        "rec_template_ids": [template.template_id],
        "values": {
            "map_checks_since_back_in_play": record["map_checks_since_back_in_play"],
            "seconds_back_in_play": record["seconds_back_in_play"],
        },
    }


def m_view(view: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    """Arm M: production + back_in_play + map_awareness + extra_point."""
    out = json.loads(json.dumps(view))
    block = map_awareness_block(record)
    eligible = bool(block and block["eligible"])
    out["back_in_play"] = back_in_play_block(record)
    out["map_awareness"] = block
    out["extra_point"] = (
        {"claim_id": MAP_CLAIM_ID, "rec_template_id": MAP_TEMPLATE} if eligible else None
    )
    if eligible:
        out["claim_basis"] = [*out.get("claim_basis", []), map_claim(record)]
    return out


def c_view(view: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    """Arm C: production + back_in_play + extra_point (the natural C claim)."""
    out = json.loads(json.dumps(view))
    claim_id, template = c_claim(record)
    out["back_in_play"] = back_in_play_block(record)
    out["extra_point"] = {"claim_id": claim_id, "rec_template_id": template}
    if template == REPOSITION_TEMPLATE:
        for entry in out["claim_basis"]:
            if entry["claim_id"] == claim_id:
                entry["rec_template_ids"] = [*entry["rec_template_ids"], template]
                entry["rec_frames"] = [*entry["rec_frames"], REPOSITION_FRAME]
    return out


def arm_view(arm: str, view: Mapping[str, Any], record: Mapping[str, Any]) -> dict:
    """The view one arm sends for one death."""
    if arm == "A":
        return json.loads(json.dumps(view))
    return m_view(view, record) if arm == "M" else c_view(view, record)


# --- prompts ------------------------------------------------------------------


def section(arm: str) -> str:
    """The frozen extra-point section; M adds its lines after the first bullet."""
    lines = SECTION_M_LINES if arm == "M" else ""
    return SECTION_HEAD + SECTION_BACK_IN_PLAY + lines + SECTION_REST


def system_prompt(arm: str) -> str:
    """Production system prompt (roster off); M and C append their section."""
    base = load_system_prompt(roster_evidence=False)
    return base if arm == "A" else base.rstrip("\n") + "\n\n" + section(arm)


def user_prompt(production_prompt: str, view: Mapping[str, Any]) -> str:
    """Production preamble + the arm view, serialized like production."""
    return xi.user_prompt(xi.prompt_preamble(production_prompt), view)


# --- call plan ----------------------------------------------------------------


def slug(record: Mapping[str, Any]) -> str:
    """File-safe death id."""
    return re.sub(r"[^0-9A-Za-z]+", "_", xc.death_id(record)).strip("_")


def call_plan(deaths: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[dict]:
    """One row per model call: population, arm, death and repeat."""
    calls = []
    for population, arms in POPULATION_ARMS.items():
        for record in deaths[population]:
            for arm in arms:
                for repeat in range(1, REPEATS + 1):
                    name = f"{arm}__{slug(record)}"
                    calls.append(
                        {
                            "call_id": f"{name}__r{repeat}",
                            "population": population,
                            "arm": arm,
                            "repeat": repeat,
                            "death_id": xc.death_id(record),
                            "file": f"{arm}/{slug(record)}.user_prompt.txt",
                            "system": f"system_prompt_{arm}.txt",
                        }
                    )
    return calls


def call_counts(calls: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Calls per population and in total."""
    counts = {p: sum(c["population"] == p for c in calls) for p in POPULATION_ARMS}
    return {**counts, "total": len(calls)}


# --- map ablation (frozen) ----------------------------------------------------


def segments(text: str) -> list[str]:
    """``assessment`` split with the frozen splitter, stripped, non-empty."""
    return [s.strip() for s in SPLITTER.split(text.strip()) if s.strip()]


def is_map_recommendation(text: str) -> bool:
    """``rec_map_after_respawn`` pattern or the map regex."""
    template = RECOMMENDATION_TEMPLATES[MAP_TEMPLATE]
    return bool(template.pattern.search(text) or MAP_REGEX.search(text))


def matches_non_map_template(text: str) -> bool:
    """Any recommendation template other than the map one (incl. C's)."""
    others = [t for k, t in RECOMMENDATION_TEMPLATES.items() if k != MAP_TEMPLATE]
    return any(t.pattern.search(text) for t in others) or bool(
        REPOSITION_PATTERN.search(text)
    )


def ablate(output: Mapping[str, Any] | None) -> dict[str, Any]:
    """M-minus-map: remove the map sentence and map recommendations, or refuse."""
    if not output:
        return {"ablatable": False, "reasons": ["unparsed"]}
    claims = [c for c in output.get("claims") or () if c.get("claim_id") == MAP_CLAIM_ID]
    sentence = str(claims[0].get("sentence") or "").strip() if len(claims) == 1 else ""
    parts = segments(str(output.get("assessment") or ""))
    recs = [str(r) for r in output.get("recommendations") or ()]
    map_recs = [r for r in recs if is_map_recommendation(r)]
    remaining = [p for p in parts if p != sentence]
    reasons = []
    if not sentence:
        reasons.append("map_claim_not_exactly_one")
    elif parts.count(sentence) != 1:
        reasons.append("map_sentence_not_exactly_one_segment")
    if sentence and not remaining:
        reasons.append("no_other_segment")
    if any(MAP_REGEX.search(p) for p in remaining):
        reasons.append("map_in_remaining_segment")
    if any(matches_non_map_template(r) for r in map_recs):
        reasons.append("map_recommendation_matches_non_map_template")
    if reasons:
        return {"ablatable": False, "reasons": reasons}
    kept = [r for r in recs if r not in map_recs]
    return {
        "ablatable": True,
        "reasons": [],
        "assessment": " ".join(remaining),
        "recommendations": kept,
        "kept_recommendation": bool(kept),
    }


# --- automatic output checks (frozen) -----------------------------------------


def _numbers(text: str) -> list[float]:
    return [float(m.group(0)) for m in _NUMBER.finditer(text)]


def fidelity_values(view: Mapping[str, Any]) -> list[float]:
    """Numeric values of back_in_play, map_awareness and the extra-point claim."""
    blocks = [view.get("back_in_play") or {}, view.get("map_awareness") or {}]
    point = view.get("extra_point") or {}
    blocks += [
        e.get("values") or {}
        for e in view.get("claim_basis") or ()
        if e.get("claim_id") == point.get("claim_id")
    ]
    return [
        float(v)
        for block in blocks
        for v in block.values()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]


def value_fidelity_errors(
    output: Mapping[str, Any], view: Mapping[str, Any]
) -> list[str]:
    """Numbers in map or extra-point sentences that equal no block value."""
    allowed = fidelity_values(view)
    point = (view.get("extra_point") or {}).get("claim_id")
    texts = [s for s in xi.output_sentences(output) if xi.mentions_map_check(s)] + [
        str(c.get("sentence") or "")
        for c in output.get("claims") or ()
        if point and c.get("claim_id") == point
    ]
    return [
        f"{n} in {t!r}"
        for t in texts
        for n in _numbers(t)
        if not any(xi._matches_value(n, v) for v in allowed)
    ]


def uptake(output: Mapping[str, Any] | None, view: Mapping[str, Any]) -> bool:
    """M: a map recommendation and an assessment sentence stating the seconds."""
    if not output:
        return False
    seconds = float((view.get("back_in_play") or {}).get("seconds_back_in_play", -1))
    template = RECOMMENDATION_TEMPLATES[MAP_TEMPLATE]
    recommended = any(
        template.pattern.search(str(r)) for r in output.get("recommendations") or ()
    )
    stated = any(
        xi._matches_value(float(m.group(1)), seconds)
        for _, s in split_sentences(str(output.get("assessment") or ""))
        for m in xi._SECONDS.finditer(s)
    )
    return recommended and stated


def c_adjustment(validation: Mapping[str, Any]) -> int:
    """C only: R10 no_template hits whose recommendation is the C template."""
    return sum(
        (h.get("rule"), h.get("check")) == ("R10", "no_template")
        and bool(REPOSITION_PATTERN.search(str(h.get("text") or "")))
        for h in validation.get("integrity_hits") or ()
    )


def hit_rules(validation: Mapping[str, Any]) -> list[str]:
    """Rule labels of every counted validator hit."""
    rules = {f"{h['rule']}:{h['check']}" for h in validation["integrity_hits"]}
    if any(
        h["counts_as_violation"] for h in validation["unsupported_recommendation_hits"]
    ):
        rules.add("unsupported_recommendation")
    if any(h["counts_as_violation"] for h in validation["causal_claim_hits"]):
        rules.add("causal_map_claim")
    if validation["unresolved_evidence_paths"]:
        rules.add("unresolved_evidence_path")
    return sorted(rules)


def output_checks(
    arm: str, output: Mapping[str, Any] | None, view: Mapping[str, Any]
) -> dict[str, Any]:
    """Every frozen automatic check for one output."""
    if not output:
        return {"parsed": False, "uptake": False, "ablation": ablate(None)}
    parsed = CoachingAssessment.model_validate(output).model_dump(mode="json")
    validation = annotate_claim_flags(
        CoachingAssessment.model_validate(output), dict(view)
    )
    hits = xd._violations(dict(validation))
    adjust = c_adjustment(validation) if arm == "C" else 0
    out = {
        "parsed": True,
        "validator_hits": hits,
        "c_adjustment": adjust,
        "adjusted_hits": hits - adjust,
        "hit_rules": hit_rules(validation),
        "blocked": bool(validation["blocked"]),
        "any_recommendation": bool(parsed["recommendations"]),
    }
    if arm in ("M", "C"):
        out["value_fidelity_errors"] = value_fidelity_errors(parsed, view)
    if arm == "M":
        block = view.get("map_awareness")
        out["map_rule_violations"] = xi.map_rule_violations(parsed, block)
        out["uptake"] = uptake(parsed, view)
        out["ablation"] = ablate(parsed) if view.get("extra_point") else None
    return out
