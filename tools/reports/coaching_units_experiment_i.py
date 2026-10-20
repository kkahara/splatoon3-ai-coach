"""Experiment I: map-check coaching (pre-registered in ``DESIGN.md``).

Research only. Two arms over the 22 Experiment H deaths (refreshed analysis):

* ``A``: production view and system prompt (``coach.roster_evidence=false``).
* ``M``: the same view plus a derived ``map_awareness`` block (and, when
  eligible, an experiment-only claim in ``claim_basis``) and the frozen
  map-check prompt section appended to the production system prompt.

The derived fact is simple arithmetic over existing artifacts
(``scenarios.json`` death episodes, ``vision_manifest.json`` MAP_OVERLAY
events, the first ``in_match`` snapshot); no vision, fusion, Scenario or
ScenarioContext change. The output checks below are frozen at stop point 2,
before any model output exists. ``build`` / ``plan`` live in
``coaching_units_experiment_i_inputs``.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import coaching_units_experiment_d as xd
import coaching_units_experiment_h as xh
from loguru import logger

from splatoon3_ai_coach.coach.claim_basis import RECOMMENDATION_TEMPLATES
from splatoon3_ai_coach.coach.claim_validation import causal_map_claim_hits
from splatoon3_ai_coach.coach.evidence_contract import split_sentences
from splatoon3_ai_coach.config import default_config_path
from splatoon3_ai_coach.config.paths import PROJECT_ROOT

REPORT_DIR = PROJECT_ROOT / "analysis" / "importance_report"
OUT_DIR = REPORT_DIR / "coaching_units_experiment_i"
ANALYSIS_ROOT = PROJECT_ROOT / "analysis"
MATCH_PREFIX = "en-live-playerview-"
ARMS = ("A", "M")
REPEATS = 2
MODEL = "gpt-5.6-luna"
PROVIDER = "cursor"
RATING_SEED = 20261020
RESPAWN_GRACE = 2.0
THRESHOLD = 10.0
NOISE_FLOOR = 0.1364
VALIDATOR_MARGIN = 0.25
UPTAKE_MIN, UPTAKE_OF = 14, 28
UNOBSERVABLE = (("2026-10-11 20-33-31", "death_episode:199.500"),)
MAP_USE_VALUES = ("correct", "incorrect", "not_mentioned")
EXPERIMENT_CLAIM_ID = "no_map_check_since_back_in_play"
EXPERIMENT_CLAIM_FRAME = (
    "No map check was observed in the {seconds_back_in_play}s since the player "
    "was back in play."
)
MAP_REC_TEMPLATE = "rec_map_after_respawn"
MAP_SECTION = """MAP CHECK SINCE BACK IN PLAY (map_awareness block)
- map_awareness.seconds_back_in_play is the time from when the player was
  last back in play (previous respawn, or match start) to this death.
- map_awareness.map_checks_since_back_in_play counts map checks the player
  opened while alive in that time. The respawn map is not counted.
- When map_awareness.eligible is true, you may coach map awareness as a
  habit: state seconds_back_in_play and that no map check was observed
  since then, and suggest a quick map check before moving up or engaging.
- Do not say the death happened because of the missing map check.
- Do not describe what the map would have shown (opponent positions, ink,
  routes); it was not observed.
- When eligible is false or the block is null, give no map-check advice.
"""

AMENDMENTS = (
    {
        "id": 1,
        "date": "2026-10-20",
        "after": "a82fcdc (stop point 1)",
        "text": (
            "At stop point 2, before any model call, the production validator "
            "was run on synthetic arm-M outputs. Two production rules encode "
            "the respawn-map concept this experiment replaces. (a) Map advice "
            "is an unsupported recommendation (and fails R10) unless a claim "
            "grounds rec_map_after_respawn: as pre-registered, the "
            "experiment-only claim no_map_check_since_back_in_play is added "
            "to arm M's claim_basis on eligible deaths, grounding that "
            "production template. (b) R5 map_absence flags any 'no map check' "
            "sentence unless map.map_check_before_death.status is "
            "not_observed, and that status counts the respawn map, so the "
            "frozen arm-M statement is a validator hit by construction. The "
            "validator is not changed; each record keeps the production count. "
            "A second, adjusted count removes only R5 map_absence hits where "
            "the call's own map_awareness block is non-null with "
            "map_checks_since_back_in_play == 0 and the sentence ties the "
            "absence to the back-in-play window ('since', back in play, "
            "respawn, return, rejoin, came back, or the seconds_back_in_play "
            "value). Arm A has no block, so the adjustment cannot apply to it. "
            "The safety gate uses the adjusted count; both are reported. No "
            "output existed; gates and thresholds are unchanged. Approved by "
            "the rater before any call."
        ),
    },
    {
        "id": 2,
        "date": "2026-10-20",
        "after": "a82fcdc (stop point 1)",
        "text": (
            "Building the views showed that the death table and the frozen "
            "observability rule disagree on 2026-10-11 20-33-31 "
            "death_episode:199.500: its episode map coverage is insufficient "
            "(no respawn overlay detected for its own respawn), so the rule "
            "makes its map_awareness block null with no map-check coaching, "
            "while the table lists it as eligible. The rater chose the rule. "
            "Eligible 14, not eligible 8 (the derived facts are unchanged and "
            "still reproduce design.json). The uptake gate keeps its 50% "
            "rate: at least 14 of 28 eligible arm-M outputs. The value gate "
            "uses the 14 eligible deaths; all other gates and thresholds are "
            "unchanged. No output existed."
        ),
    },
)


# --- derived fact -------------------------------------------------------------


def death_time(analysis: Path, cid: str) -> float:
    """Start time of the death episode ``cid`` in ``scenarios.json``."""
    doc = json.loads((analysis / "scenarios.json").read_text(encoding="utf-8"))
    scenarios = doc["scenarios"] if isinstance(doc, dict) else doc
    return float(next(s["start_time"] for s in scenarios if s["scenario_id"] == cid))


def map_awareness_facts(
    episodes: Sequence[tuple[float, float]],
    overlays: Sequence[float],
    match_start: float,
    death: float,
) -> dict[str, Any]:
    """Back-in-play time, alive map checks since then, and eligibility.

    ``episodes`` are death-episode ``(start, end)``; ``overlays`` are
    MAP_OVERLAY start times. Overlays within ``RESPAWN_GRACE`` of being back
    in play are the tail of the respawn map and do not count.
    """
    previous = sorted(e for e in episodes if e[0] < death)
    if previous:
        back, source = float(previous[-1][1]), "previous_respawn"
    else:
        back, source = float(match_start), "match_start"
    checks = sorted(t for t in overlays if back + RESPAWN_GRACE < t < death)
    seconds = round(death - back, 3)
    return {
        "back_in_play_source": source,
        "back_in_play_time": back,
        "seconds_back_in_play": seconds,
        "map_checks_since_back_in_play": len(checks),
        "seconds_since_alive_map_check": round(death - checks[-1], 3) if checks else None,
        "eligible": not checks and seconds >= THRESHOLD,
    }


def facts_for(analysis: Path, cid: str) -> dict[str, Any]:
    """``map_awareness_facts`` for one death from its analysis artifacts."""
    doc = json.loads((analysis / "scenarios.json").read_text(encoding="utf-8"))
    scenarios = doc["scenarios"] if isinstance(doc, dict) else doc
    episodes = [
        (float(s["start_time"]), float(s["end_time"]))
        for s in scenarios
        if s["scenario_type"] == "death_episode" and s["end_time"] is not None
    ]
    manifest = json.loads((analysis / "vision_manifest.json").read_text(encoding="utf-8"))
    overlays = [
        float(e["start_time"])
        for e in manifest["game_events"]
        if e["event_type"] == "map_overlay"
    ]
    start = next(
        float(s["timestamp"])
        for s in manifest["state_snapshots"]
        if s["match_phase"] == "in_match"
    )
    return map_awareness_facts(episodes, overlays, start, death_time(analysis, cid))


def map_observable(view: Mapping[str, Any]) -> bool:
    """Map-check status and episode coverage known (design: else block null)."""
    block = (view.get("map") or {}).get("map_check_before_death") or {}
    coverage = block.get("coverage") or {}
    return block.get("status") in ("observed", "not_observed") and (
        coverage.get("episode") == "sufficient"
    )


def awareness_block(facts: Mapping[str, Any], view: Mapping[str, Any]) -> dict | None:
    """The ``map_awareness`` view block, or ``None`` when map is unobservable."""
    if not map_observable(view):
        return None
    return {
        "back_in_play_source": facts["back_in_play_source"],
        "seconds_back_in_play": facts["seconds_back_in_play"],
        "map_checks_since_back_in_play": facts["map_checks_since_back_in_play"],
        "seconds_since_alive_map_check": facts["seconds_since_alive_map_check"],
        "respawn_grace_seconds": RESPAWN_GRACE,
        "threshold_seconds": THRESHOLD,
        "eligible": facts["eligible"],
    }


def experiment_claim(block: Mapping[str, Any]) -> dict[str, Any]:
    """Experiment-only ``claim_basis`` entry for an eligible death (arm M)."""
    template = RECOMMENDATION_TEMPLATES[MAP_REC_TEMPLATE]
    return {
        "authorizes": [],
        "claim_id": EXPERIMENT_CLAIM_ID,
        "coachable": True,
        "evidence_paths": [
            "map_awareness.seconds_back_in_play",
            "map_awareness.map_checks_since_back_in_play",
        ],
        "frame": EXPERIMENT_CLAIM_FRAME,
        "level": "A",
        "rec_frames": [template.frame],
        "rec_template_ids": [template.template_id],
        "values": {
            "map_checks_since_back_in_play": block["map_checks_since_back_in_play"],
            "seconds_back_in_play": block["seconds_back_in_play"],
        },
    }


def m_view(view: Mapping[str, Any], facts: Mapping[str, Any]) -> dict[str, Any]:
    """Arm M view: production view + ``map_awareness`` (+ claim if eligible)."""
    out = json.loads(json.dumps(view))
    block = awareness_block(facts, view)
    out["map_awareness"] = block
    if block is not None and block["eligible"]:
        out["claim_basis"] = [*out.get("claim_basis", []), experiment_claim(block)]
    return out


def user_prompt(preamble: str, view: Mapping[str, Any]) -> str:
    """Serialize like ``serialize_llm_view_user_prompt`` (same bytes for A)."""
    body = json.dumps(view, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return preamble + body


def prompt_preamble(prompt: str) -> str:
    """Production preamble: everything before the view JSON."""
    return prompt[: prompt.index("{")]


def arm_system_prompt(arm: str) -> str:
    """Production system prompt (roster off); arm M appends the section."""
    base = xh.arm_system_prompt("A")
    return base if arm == "A" else base.rstrip("\n") + "\n\n" + MAP_SECTION


# --- frozen output checks (pre-registered; applied at stop point 3) ---------

_MAP_MENTION = re.compile(
    r"\bmap[-\s]?checks?\b|\bmap\s+awareness\b|\bmap\s+(?:glance|look)s?\b"
    r"|\b(?:check|look|glanc|open|watch)\w*\s+(?:at\s+)?(?:the\s+|your\s+|a\s+)?map\b",
    re.IGNORECASE,
)
_SECONDS = re.compile(
    r"(?<![\d.:])(\d+(?:\.\d+)?)\s*(?:s|secs?|seconds?)\b", re.IGNORECASE
)
_ADVICE = re.compile(
    r"\b(?:check|look|glanc|open|watch)\w*\s+(?:at\s+)?(?:the\s+|your\s+|a\s+)?map\b"
    r"|\b(?:quick|brief|fast)\s+map[-\s]?(?:check|glance|look)\b"
    r"|\bmap\s+awareness\b",
    re.IGNORECASE,
)
_CAUSAL = re.compile(
    r"\b(?:because|due\s+to|led\s+to|lead(?:s|ing)?\s+to|caus\w*|result\w*\s+(?:in|from)"
    r"|contribut\w*|why\s+you\s+(?:died|were\s+splatted))\b",
    re.IGNORECASE,
)
_CAUSAL_EXEMPT = re.compile(
    r"\b(?:not|n't|no\s+evidence|cannot|can't|doesn't|does\s+not|isn't)\b"
    r"[^.]{0,60}\b(?:because|due\s+to|led\s+to|caus\w*|result\w*|why\s+you)\b",
    re.IGNORECASE,
)
_CONTENT = re.compile(
    r"\b(?:would|could|might)\s+have\s+(?:shown|revealed|seen|spotted|noticed|told)\b"
    r"|\bmap\s+(?:showed|revealed|was\s+showing)\b",
    re.IGNORECASE,
)
_WINDOW_ANCHOR = re.compile(
    r"\bsince\b|\bback\s+in(?:to)?\s+(?:play|the\s+(?:fight|action|game))\b"
    r"|\brespawn\w*|\breturn\w*|\brejoin\w*|\bcame\s+back\b",
    re.IGNORECASE,
)


def output_sentences(assessment: Mapping[str, Any] | None) -> list[str]:
    """Player-facing sentences: assessment, recommendations and claim sentences."""
    if not assessment:
        return []
    texts = [str(assessment.get("assessment") or "")]
    texts += [str(r) for r in assessment.get("recommendations") or ()]
    texts += [
        str(c.get("sentence") or "")
        for c in assessment.get("claims") or ()
        if isinstance(c, Mapping)
    ]
    return [s.strip() for t in texts for _, s in split_sentences(t) if s.strip()]


def mentions_map_check(text: str) -> bool:
    """Map check / checking or looking at the map / map awareness wording."""
    return bool(_MAP_MENTION.search(text))


def _allowed_seconds(block: Mapping[str, Any] | None) -> list[float]:
    if not block:
        return []
    values = [block["seconds_back_in_play"], block["seconds_since_alive_map_check"]]
    return [float(v) for v in values if v is not None]


def _matches_value(cited: float, value: float) -> bool:
    """Equal at 0.5 s or whole-second rounding."""
    candidates = (value, round(value * 2) / 2, math.floor(value), math.ceil(value))
    return any(abs(cited - r) < 1e-6 for r in candidates)


def value_fidelity_errors(
    sentences: Sequence[str], block: Mapping[str, Any] | None
) -> list[str]:
    """Seconds cited in map-check sentences that match no ``map_awareness`` value."""
    allowed = _allowed_seconds(block)
    errors = []
    for sentence in sentences:
        if not mentions_map_check(sentence):
            continue
        for match in _SECONDS.finditer(sentence):
            cited = float(match.group(1))
            if not any(_matches_value(cited, v) for v in allowed):
                errors.append(f"{match.group(0)!r} in {sentence!r}")
    return errors


def _causal(sentence: str) -> bool:
    if not mentions_map_check(sentence) or not _CAUSAL.search(sentence):
        return False
    if _CAUSAL_EXEMPT.search(sentence):
        return False
    return True


def map_rule_violations(
    assessment: Mapping[str, Any] | None, block: Mapping[str, Any] | None
) -> list[str]:
    """Codes: advice on a non-eligible death, causal claim, described map content."""
    if not assessment:
        return []
    out = []
    eligible = bool(block and block["eligible"])
    advice = [str(r) for r in assessment.get("recommendations") or ()]
    if not eligible and any(_ADVICE.search(r) for r in advice):
        out.append("advice_not_eligible")
    sentences = output_sentences(assessment)
    texts = [str(assessment.get(f) or "") for f in ("assessment",)] + advice
    production = [
        h
        for t in texts
        for h in causal_map_claim_hits(t, field="any")
        if h["counts_as_violation"]
    ]
    if production or any(_causal(s) for s in sentences):
        out.append("causal")
    if any(mentions_map_check(s) and _CONTENT.search(s) for s in sentences):
        out.append("map_content")
    return out


def r5_window_exempt(
    validation: Mapping[str, Any], block: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    """Amendment 1: R5 map_absence hits supported by the call's own block."""
    if not block or block["map_checks_since_back_in_play"] != 0:
        return []
    seconds = block["seconds_back_in_play"]
    out = []
    for hit in validation.get("integrity_hits") or ():
        if (hit.get("rule"), hit.get("check")) != ("R5", "map_absence"):
            continue
        text = str(hit.get("text") or "")
        cited = [float(m.group(1)) for m in _SECONDS.finditer(text)]
        anchored = _WINDOW_ANCHOR.search(text) or any(
            _matches_value(c, seconds) for c in cited
        )
        if anchored:
            out.append(hit)
    return out


def validator_counts(
    validation: Mapping[str, Any], block: Mapping[str, Any] | None
) -> dict[str, int]:
    """Production validator hit count and the amendment-1 adjusted count."""
    raw = xd._violations(dict(validation))
    exempt = len(r5_window_exempt(validation, block))
    return {"validator_hits": raw, "r5_window_exempt": exempt, "adjusted": raw - exempt}


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``build`` / ``plan`` (stop point 2)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("build", "plan"))
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--config", type=Path, default=default_config_path())
    args = parser.parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    import coaching_units_experiment_i_inputs as xi

    command = xi.build if args.command == "build" else xi.plan
    return command(args.out, args.config.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
