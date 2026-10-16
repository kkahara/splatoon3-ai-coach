"""Experiment H: roster evidence in coaching (pre-registered design).

Research only. Three arms over the 12 live matches refreshed on 2026-10-15
with the promoted roster detector:

* ``A``: refreshed analysis, ``coach.roster_evidence=false`` (production today).
* ``B``: pre-refresh analysis (``analysis/_rerun_backup_2026-10-15``), roster on.
* ``C``: refreshed analysis, roster on.

Each arm is the production pipeline (``score_match`` + ``build_coach_llm_view``
+ ``load_system_prompt``) with only the production switch set; nothing is
patched. Q1 (correction effect) compares B with C on the deaths whose roster
evidence changed; Q2 (value) compares A with C.

Stop point 1 implements ``design`` only: it computes the treatment set, the
seeded control draw, the selection-effect table and a production-equivalence
check of every arm, then writes ``DESIGN.md`` and ``design.json``. The output
checks below (roster mention, count fidelity, rule violations, divergence) are
frozen here, before any model output exists. ``build`` / ``plan`` / ``run`` /
``summary`` / ``sheet`` / ``analyze`` come at later stop points.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import random
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coaching_units_experiment_h_doc import design_markdown
from importance_report import _score
from loguru import logger

from splatoon3_ai_coach.coach.artifacts import safe_filename
from splatoon3_ai_coach.coach.coach import serialize_llm_view_user_prompt
from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view
from splatoon3_ai_coach.coach.prompts import load_system_prompt
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT

REPORT_DIR = PROJECT_ROOT / "analysis" / "importance_report"
OUT_DIR = REPORT_DIR / "coaching_units_experiment_h"
REFRESHED_ROOT = PROJECT_ROOT / "analysis"
PRE_REFRESH_ROOT = PROJECT_ROOT / "analysis" / "_rerun_backup_2026-10-15"
MATCHES = tuple(
    f"en-live-playerview-{stamp}"
    for stamp in (
        "2026-10-11 18-18-00",
        "2026-10-11 18-25-16",
        "2026-10-11 20-33-31",
        "2026-10-11 22-20-42",
        "2026-10-11 22-31-53",
        "2026-10-11 22-37-47",
        "2026-10-11 22-57-30",
        "2026-10-12 17-42-59",
        "2026-10-12 19-47-46",
        "2026-10-12 19-59-26",
        "2026-10-12 20-17-32",
        "2026-10-12 20-25-22",
    )
)
ARMS: dict[str, tuple[str, bool]] = {
    "A": ("refreshed", False),
    "B": ("pre_refresh", True),
    "C": ("refreshed", True),
}
ROOTS = {"refreshed": REFRESHED_ROOT, "pre_refresh": PRE_REFRESH_ROOT}
REPEATS = 2
CONTROL_SEED = 20261017
RATING_SEED = 20261018
FIDELITY_MIN = 0.90
VALIDATOR_MARGIN = 0.25
MODEL = "gpt-5.6-luna"
PROVIDER = "cursor"
ROSTER_USE_VALUES = ("correct", "incorrect", "not_mentioned")

AMENDMENTS = (
    {
        "id": 1,
        "date": "2026-10-15",
        "after": "78f0f6f (stop point 1)",
        "text": (
            "After Stop Point 1 and before inputs were built or any model "
            "outputs existed, inspection of the frozen automatic checker found "
            "that generic 'advantage' matching would incorrectly classify "
            "ordinary constructions such as 'take advantage of your special' "
            "as numerical-roster claims. The checker was narrowed to exclude "
            "non-roster 'take advantage of ...' usage (take / takes / taking / "
            "took / taken, optionally 'full' or 'more') from both the "
            "numbers-state match used by count fidelity and the roster-mention "
            "match, so such an output is neither a roster claim nor a roster "
            "mention. No experimental output had been generated or observed "
            "when this change was made. All decision thresholds and other "
            "checks remain unchanged."
        ),
    },
)

AT_OR_BEFORE_DEATH = "at_or_before_death"
WINDOW_ONLY = "window_only"
RANK_ONLY = "rank_only"
UNCHANGED = "unchanged"
WINDOW_PREFIX = ".roster.player_count_window"


def arm_config(base: AppConfig, arm: str) -> AppConfig:
    """``base`` with only ``coach.roster_evidence`` set for ``arm``."""
    config = base.model_copy(deep=True)
    config.coach.roster_evidence = ARMS[arm][1]
    return config


def arm_system_prompt(arm: str) -> str:
    """The production system prompt for the arm's roster setting."""
    return load_system_prompt(roster_evidence=ARMS[arm][1])


@dataclass(frozen=True)
class DeathView:
    """One death unit as one arm's production pipeline presents it."""

    match: str
    cid: str
    score: float
    rank: int | None
    selected: bool
    before_death: tuple[int, int] | None
    view: dict[str, Any]
    prompt: str


def _counts(sample: Any) -> tuple[int, int] | None:
    if sample is None:
        return None
    return (int(sample.ally_alive_count), int(sample.opponent_alive_count))


def arm_views(
    base: AppConfig, arm: str, matches: Sequence[str] = MATCHES
) -> dict[tuple[str, str], DeathView]:
    """Every death unit of ``matches`` under ``arm``."""
    config = arm_config(base, arm)
    root = ROOTS[ARMS[arm][0]]
    thresholds = config.coach.death_factor_thresholds
    out: dict[tuple[str, str], DeathView] = {}
    for match in matches:
        _, scoring = _score(root / match, config)
        for unit in scoring.units:
            view = build_coach_llm_view(
                scoring.coach_inputs[unit.candidate_id], unit, thresholds
            )
            out[(match, unit.candidate_id)] = DeathView(
                match=match,
                cid=unit.candidate_id,
                score=float(unit.importance_score),
                rank=unit.rank,
                selected=bool(unit.selected_for_llm),
                before_death=_counts(unit.roster_before_death),
                view=view.model_dump(mode="json"),
                prompt=serialize_llm_view_user_prompt(view),
            )
    return out


def diff_paths(a: Any, b: Any, path: str = "") -> Iterator[str]:
    """Paths (list indexes as ``[]``) where two JSON values differ."""
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key):
                yield from diff_paths(a.get(key), b.get(key), f"{path}.{key}")
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for x, y in zip(a, b, strict=True):
            if x != y:
                yield from diff_paths(x, y, f"{path}[]")
    elif a != b:
        yield path


def classify(paths: Iterable[str]) -> str:
    """Change type of a B-to-C view difference.

    ``rank_only``: only ``.importance`` differs (a selection side effect).
    ``window_only``: outside ``.importance``, only the roster window differs.
    ``at_or_before_death``: any other evidence path differs.
    """
    evidence = [p for p in set(paths) if not p.startswith(".importance")]
    if not evidence:
        return RANK_ONLY if set(paths) else UNCHANGED
    if all(p.startswith(WINDOW_PREFIX) for p in evidence):
        return WINDOW_ONLY
    return AT_OR_BEFORE_DEATH


def draw_controls(
    pool: Sequence[tuple[str, str]],
    selected: Mapping[tuple[str, str], bool],
    n_selected: int,
    n_unselected: int,
    seed: int = CONTROL_SEED,
) -> list[tuple[str, str]]:
    """Seeded draw from ``pool``, stratified by arm-C production selection."""
    rng = random.Random(seed)
    out: list[tuple[str, str]] = []
    for stratum, k in ((True, n_selected), (False, n_unselected)):
        candidates = sorted(key for key in pool if selected[key] is stratum)
        if len(candidates) < k:
            raise SystemExit(
                f"control stratum selected={stratum}: {len(candidates)} < {k}"
            )
        out.extend(sorted(rng.sample(candidates, k)))
    return out


# --- frozen output checks (pre-registered; applied at stop point 3) ---------

_PAIR = re.compile(
    r"\b([0-4])\s*(?:v|vs\.?|versus|-|to)\s*([0-4])\b"
    r"|\b([0-4])\s+(?:players?\s+)?(?:alive\s+)?against\s+([0-4])\b",
    re.IGNORECASE,
)
_STATES = {
    "even": re.compile(
        r"\beven\s+(?:numbers|count|teams?)\b|\bnumbers\s+were\s+even\b|\bequal\s+numbers\b",
        re.IGNORECASE,
    ),
    "disadvantage": re.compile(
        r"\bdisadvantage\b|\boutnumbered\b|\bdown\s+a\s+player\b", re.IGNORECASE
    ),
    "advantage": re.compile(
        r"(?<!dis)\badvantage\b|\bahead\s+in\s+numbers\b|\bup\s+a\s+player\b",
        re.IGNORECASE,
    ),
}
_MENTION = re.compile(
    r"\balive\b|\boutnumber|\bin\s+numbers\b|\bnumbers?\s+(?:dis)?advantage\b"
    r"|\b(?:dis)?advantage\b|\bplayers?\s+(?:alive|left|remaining|down)\b"
    r"|\broster\b|\bplayer\s+count\b",
    re.IGNORECASE,
)
_VERDICT = re.compile(
    r"\bshould(?:\s+have|'ve)?\s+(?:retreated|pushed|played\s+(?:more\s+)?passive(?:ly)?"
    r"|disengaged|backed\s+off|waited|held\s+back|engaged)\b",
    re.IGNORECASE,
)
_FIGHT = re.compile(
    r"\b(?:outnumbered|uneven|even|[1-4]\s*v\s*[1-4])\s+(?:fight|engagement)\b"
    r"|\bfought\s+(?:while\s+)?outnumbered\b|\btook\s+an?\s+outnumbered\b",
    re.IGNORECASE,
)
_INFERENCE = re.compile(
    r"\bteammates?\s+(?:had\s+)?(?:died|was\s+splatted|were\s+splatted|got\s+splatted)\b"
    r"|\btrade[ds]?\b",
    re.IGNORECASE,
)
_DURATION = re.compile(
    r"\b(?:disadvantaged|outnumbered|at\s+a\s+disadvantage)\s+for\s+\d+(?:\.\d+)?\s*s",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
# Pre-output amendment 1: "take advantage of ..." uses an opportunity; it is
# not a numbers-advantage claim and is removed before state / mention matching.
_TAKE_ADVANTAGE = re.compile(
    r"\b(?:take|takes|taking|took|taken)\s+(?:full\s+|more\s+)?advantage\s+of\b",
    re.IGNORECASE,
)


def _roster_text(text: str) -> str:
    return _TAKE_ADVANTAGE.sub(" ", text)


def mentions_roster(text: str) -> bool:
    """The output refers to alive counts, numbers state or the roster."""
    text = _roster_text(text)
    return bool(_MENTION.search(text) or _PAIR.search(text))


def cited_pairs(text: str) -> set[tuple[int, int]]:
    """Ally-vs-opponent count pairs cited in ``text`` (ally first)."""
    out = set()
    for match in _PAIR.finditer(text):
        a, b = (match.group(1), match.group(2)) if match.group(1) else match.group(3, 4)
        out.add((int(a), int(b)))
    return out


def cited_states(text: str) -> set[str]:
    """Numbers states (even / advantage / disadvantage) named in ``text``."""
    text = _roster_text(text)
    return {state for state, pattern in _STATES.items() if pattern.search(text)}


def roster_evidence_sets(view: Mapping[str, Any]) -> tuple[set, set]:
    """Allowed count pairs and numbers states from an arm's roster block."""
    roster = view.get("roster") or {}
    points = [roster.get("before_death"), roster.get("at_death")]
    points += list(roster.get("player_count_window") or ())
    pairs, states = set(), set()
    for point in (p for p in points if p):
        ally, opp = point.get("ally_alive_count"), point.get("opponent_alive_count")
        if ally is None or opp is None:
            continue
        pairs.add((int(ally), int(opp)))
        states.add(
            "even" if ally == opp else "advantage" if ally > opp else "disadvantage"
        )
    return pairs, states


def count_fidelity(text: str, view: Mapping[str, Any]) -> bool:
    """No cited pair or numbers state outside the arm's own roster evidence."""
    pairs, states = roster_evidence_sets(view)
    return cited_pairs(text) <= pairs and cited_states(text) <= states


def roster_violations(text: str) -> list[str]:
    """Rule codes broken by ``text`` (``coach_roster.txt``)."""
    out = []
    for sentence in _SENTENCE.split(text):
        roster = mentions_roster(sentence)
        if roster and _VERDICT.search(sentence):
            out.append("verdict")
        if _FIGHT.search(sentence):
            out.append("fight_framing")
        if roster and _INFERENCE.search(sentence):
            out.append("teammate_or_trade_inference")
        if _DURATION.search(sentence):
            out.append("continuous_duration")
    return out


def divergence(a: str, b: str) -> float:
    """1 - difflib similarity ratio (0 = identical text)."""
    return 1.0 - difflib.SequenceMatcher(None, a, b).ratio()


# --- design ------------------------------------------------------------------


def classify_deaths(
    views: Mapping[str, Mapping[tuple[str, str], DeathView]],
) -> list[dict[str, Any]]:
    """Every death with its B-to-C change type, covariates and selection table."""
    rows = []
    for key in sorted(views["C"]):
        b, c = views["B"][key], views["C"][key]
        paths = sorted(set(diff_paths(b.view, c.view)))
        rows.append(
            {
                "match": key[0],
                "cid": key[1],
                "change_type": classify(paths),
                "diff_paths": paths,
                "prompts_identical_b_c": b.prompt == c.prompt,
                "before_death_b": b.before_death,
                "before_death_c": c.before_death,
                **{
                    f"{arm}_{field}": getattr(views[arm][key], field)
                    for arm in ARMS
                    for field in ("score", "rank", "selected")
                },
            }
        )
    return rows


def select_sets(rows: Sequence[dict[str, Any]]) -> tuple[list, list]:
    """Treatment (evidence changed B to C) and the seeded control draw."""
    treatment = [r for r in rows if r["change_type"] in (AT_OR_BEFORE_DEATH, WINDOW_ONLY)]
    pool = [(r["match"], r["cid"]) for r in rows if r["change_type"] == UNCHANGED]
    selected = {(r["match"], r["cid"]): r["C_selected"] for r in rows}
    n_sel = sum(1 for r in treatment if r["C_selected"])
    controls = draw_controls(pool, selected, n_sel, len(treatment) - n_sel)
    return treatment, controls


def switched_config_text(base_text: str, roster: bool) -> str:
    """The production config YAML with only ``coach.roster_evidence`` set."""
    if base_text.count("roster_evidence: false") != 1:
        raise SystemExit("expected exactly one 'roster_evidence: false' in config")
    return base_text.replace(
        "roster_evidence: false", f"roster_evidence: {str(roster).lower()}"
    )


def _cli_views(analysis: Path, config: Path, dest: Path) -> dict[str, dict[str, Any]]:
    """``s3-coach coach-inputs`` LLM views for one analysis, by candidate id."""
    subprocess.run(
        [
            str(PROJECT_ROOT / ".venv" / "bin" / "s3-coach"),
            "coach-inputs",
            str(analysis),
            "--config",
            str(config),
            "--out",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )
    index = json.loads((dest / "coaching_index.json").read_text(encoding="utf-8"))
    entries = index["entries"] if isinstance(index, dict) else index
    return {
        e["candidate_id"]: json.loads((dest / e["llm_view_json"]).read_text("utf-8"))
        for e in entries
    }


def production_equivalence(
    base_path: Path, views: Mapping[str, Mapping[tuple[str, str], DeathView]]
) -> dict[str, dict[str, int]]:
    """Each arm's views against ``s3-coach coach-inputs`` output (temp dirs)."""
    text = base_path.read_text(encoding="utf-8")
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for arm, (root_name, roster) in ARMS.items():
            cfg = Path(tmp) / f"{arm}.yaml"
            cfg.write_text(switched_config_text(text, roster), encoding="utf-8")
            matched = total = 0
            for match in MATCHES:
                dest = Path(tmp) / arm / safe_filename(match)
                cli = _cli_views(ROOTS[root_name] / match, cfg, dest)
                for (m, cid), death in views[arm].items():
                    if m == match:
                        total += 1
                        matched += cli.get(cid) == death.view
            out[arm] = {"matched": matched, "total": total}
    return out


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def design_doc(base_path: Path) -> dict[str, Any]:
    """Compute the full pre-registered design (no model calls)."""
    base = load_config(base_path)
    views = {arm: arm_views(base, arm) for arm in ARMS}
    rows = classify_deaths(views)
    treatment, controls = select_sets(rows)
    return {
        "schema_version": 1,
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
        ).strip(),
        "config_sha256": _sha(base_path.read_text(encoding="utf-8")),
        "matches": list(MATCHES),
        "arms": {
            arm: {
                "analysis": root,
                "roster_evidence": roster,
                "system_prompt_sha256": _sha(arm_system_prompt(arm)),
            }
            for arm, (root, roster) in ARMS.items()
        },
        "provider": PROVIDER,
        "model": MODEL,
        "repeats": REPEATS,
        "control_seed": CONTROL_SEED,
        "rating_seed": RATING_SEED,
        "thresholds": {
            "fidelity_min": FIDELITY_MIN,
            "validator_margin": VALIDATOR_MARGIN,
        },
        "production_equivalence": production_equivalence(base_path, views),
        "amendments": list(AMENDMENTS),
        "treatment": [[r["match"], r["cid"]] for r in treatment],
        "controls": [list(k) for k in controls],
        "deaths": rows,
    }


def _stop_point_3(args: argparse.Namespace) -> int:
    import coaching_units_experiment_h_results as xres
    import coaching_units_experiment_h_run as xr

    if args.command in ("run", "recover"):
        if not (args.confirm and args.model):
            raise SystemExit(f"{args.command} needs --model and --confirm")
        command = xr.run if args.command == "run" else xr.recover
        return command(args.out, load_config(args.config), args.provider, args.model)
    if args.command == "summary":
        doc = xres.write_summary(args.out)
        sys.stdout.write(json.dumps(doc["coverage"]) + "\n")
        return 0 if not any(doc["coverage"].values()) else 1
    result = xres.write_sheet(args.out)
    sys.stdout.write(json.dumps(result) + "\n")
    return (
        0 if not any(any(c.values()) for c in (result["records"], result["key"])) else 1
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``design`` (stop point 1), ``build`` / ``plan`` (stop point 2),
    ``run`` / ``recover`` / ``summary`` / ``sheet`` (stop point 3)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command",
        choices=("design", "build", "plan", "run", "recover", "summary", "sheet"),
    )
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--provider", default=PROVIDER)
    parser.add_argument("--model", default=None)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    if args.command in ("run", "recover", "summary", "sheet"):
        return _stop_point_3(args)
    if args.command != "design":
        import coaching_units_experiment_h_inputs as xi

        command = xi.build if args.command == "build" else xi.plan
        return command(args.out, args.config.resolve())
    target = args.out / "design.json"
    if target.exists():
        raise SystemExit(f"{target} exists; the design is frozen")
    doc = design_doc(args.config.resolve())
    args.out.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    (args.out / "DESIGN.md").write_text(design_markdown(doc), encoding="utf-8")
    sys.stdout.write(design_markdown(doc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
