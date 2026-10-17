"""Coaching gap study: which missing facts would have made coaching useful.

Observational research only: no model calls, no production change. The pool
is the 22 Experiment H deaths, whose arm-A outputs are production coaching
(``coach.roster_evidence=false``).

* ``freeze`` writes ``study.json`` / ``STUDY.md``: the seed, the eligible
  population, the draw rule, the A / B / C / D classification rules and the
  next-step decision rule. It must be committed before ``select``.
* ``select`` refuses unless ``study.json`` is committed unchanged, then draws
  all production-selected deaths plus a seeded set of unselected ones (at
  most one per match) and writes ``gap_sheet.csv`` and ``INSTRUCTIONS.md``.
* ``summarize`` reads ``gap_classification.csv`` (one row per missing fact)
  and writes ``GAP_STUDY.md`` with the frozen decision rule applied.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import re
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from splatoon3_ai_coach.config.paths import PROJECT_ROOT

REPORT_DIR = PROJECT_ROOT / "analysis" / "importance_report"
OUT_DIR = REPORT_DIR / "coaching_gap_study"
H_DIR = REPORT_DIR / "coaching_units_experiment_h"
RERUN_RECORD = (
    PROJECT_ROOT / "analysis" / "_rerun_backup_2026-10-15" / "rerun_record.json"
)
ANALYSIS_ROOT = PROJECT_ROOT / "analysis"
SEED = 20261019
N_UNSELECTED = 4
WATCH_BEFORE, WATCH_AFTER = 20.0, 2.0
CATEGORIES = ("A", "B", "C", "D")
A_LOCATIONS = ("view", "pipeline")
THEMES = (
    "special",
    "objective",
    "ink",
    "map",
    "opponent",
    "teammate",
    "positioning",
    "timing",
    "other",
)
RANKING_REVIEW_MIN_GAP = 1.0
USER_COLUMNS = (
    "what_happened",
    "fact_1",
    "fact_2",
    "fact_3",
    "ideal_coaching",
    "why_current_falls_short",
)
CLASSIFICATION_COLUMNS = (
    "death",
    "fact",
    "category",
    "a_location",
    "theme",
    "evidence",
)


def death_id(match: str, cid: str) -> str:
    """Short stable id, e.g. ``2026-10-11 22-57-30 @308.0``."""
    return (
        f"{match.removeprefix('en-live-playerview-')} @{float(cid.split(':', 1)[1]):.1f}"
    )


def mmss(seconds: float) -> str:
    """Playback time ``m:ss.s``."""
    seconds = max(seconds, 0.0)
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


# --- freeze -----------------------------------------------------------------


def population(design: Mapping[str, Any]) -> dict[str, list[list[str]]]:
    """Experiment H deaths split by production (arm A) selection."""
    rows = {(r["match"], r["cid"]): r for r in design["deaths"]}
    keys = [tuple(k) for k in design["treatment"] + design["controls"]]
    return {
        "selected": [list(k) for k in sorted(keys) if rows[k]["A_selected"]],
        "unselected": [list(k) for k in sorted(keys) if not rows[k]["A_selected"]],
    }


DRAW_RULE = (
    "All production-selected deaths are included. From the unselected pool, "
    "random.Random(seed) shuffles the sorted list of matches that have an "
    "unselected death; the first N matches are kept and, in that order, "
    "rng.choice picks one death from each match's sorted unselected deaths."
)
CATEGORY_RULES = {
    "A": (
        "Already available: a concrete field holds the fact for this death. "
        "a_location=view if it is in the CoachLlmView the model receives "
        "(e.g. death, clock, low_ink, map, related, importance); "
        "a_location=pipeline if it is in ScenarioContext or the vision "
        "manifest but not in the view (e.g. special, score, zone_control, "
        "map.ink, players)."
    ),
    "B": (
        "Derivable: computable from readings or fused state that exist today, "
        "with no new detector (e.g. special ready when the fight began, from "
        "special gauge readings and the death time)."
    ),
    "C": (
        "New detector required: visible on screen but not extracted (e.g. the "
        "weapon that splatted you, the splat feed)."
    ),
    "D": (
        "Not realistically observable: needs information the footage does not "
        "carry or substantially harder inference (e.g. off-screen teammate "
        "positions, opponent intent)."
    ),
}
PRIMARY_RULE = (
    "Exactly one primary category per fact. When more than one applies, the "
    "category needing the least engineering work wins: A before B before C "
    "before D, and within A, view before pipeline."
)
EVIDENCE_RULE = (
    "A: field path and its value for this death. B: the existing readings or "
    "state it is computed from. C: the screen element. D: the reason."
)
DECISION_RULE = (
    "Counts are per missing fact, not per death. Actionable facts are A, B "
    "and C; D facts are recorded only. If A + B >= C, the next experiment "
    "targets the theme with the most A + B facts (ties: more A facts, then "
    "report both); within that theme the most common of A-view, A-pipeline "
    "and B decides its kind (ties: least work first): A-view -> prompt / "
    "claim-catalog experiment, A-pipeline -> view-extension experiment, "
    "B -> fusion feature. If C > A + B, scope a detector for the theme with "
    "the most C facts. Ranking review is flagged if unselected deaths "
    f"average at least {RANKING_REVIEW_MIN_GAP} more A + B missing facts "
    "per death than selected deaths (descriptive; no automatic change)."
)


def study_doc(design: Mapping[str, Any]) -> dict[str, Any]:
    """The frozen study definition (no draw)."""
    return {
        "schema_version": 1,
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
        ).strip(),
        "source": "Experiment H design.json; arm A (production, roster off) outputs",
        "seed": SEED,
        "n_unselected": N_UNSELECTED,
        "population": population(design),
        "draw_rule": DRAW_RULE,
        "watch_window_seconds": [-WATCH_BEFORE, WATCH_AFTER],
        "categories": CATEGORY_RULES,
        "primary_rule": PRIMARY_RULE,
        "evidence_rule": EVIDENCE_RULE,
        "themes": list(THEMES),
        "decision_rule": DECISION_RULE,
        "ranking_review_min_gap": RANKING_REVIEW_MIN_GAP,
    }


def study_markdown(doc: Mapping[str, Any]) -> str:
    """``STUDY.md``: the frozen definition, before any draw or fact exists."""
    pop = doc["population"]
    lines = [
        "# Coaching gap study (frozen before the draw)",
        "",
        "Observational. No model calls and no production change. Frozen at",
        f"`{doc['git_head'][:7]}` before the draw and before any fact is",
        "recorded. Machine-readable: `study.json`.",
        "",
        "## Population and draw",
        "",
        f"Source: {doc['source']}. Seed **{doc['seed']}**.",
        "",
        f"- Production-selected ({len(pop['selected'])}, all included): "
        + "; ".join(death_id(*k) for k in pop["selected"]),
        f"- Unselected pool ({len(pop['unselected'])}, draw "
        f"{doc['n_unselected']}, at most one per match): "
        + "; ".join(death_id(*k) for k in pop["unselected"]),
        "",
        doc["draw_rule"],
        "",
        "## Classification (one row per missing fact)",
        "",
        *[f"- **{c}.** {text}" for c, text in doc["categories"].items()],
        "",
        doc["primary_rule"],
        "",
        f"Evidence pointer: {doc['evidence_rule']}",
        "",
        "Theme (one per fact): " + ", ".join(doc["themes"]) + ".",
        "",
        "## Decision rule",
        "",
        doc["decision_rule"],
        "",
        "## Stop points",
        "",
        "1. Freeze (this file), committed.",
        "2. Draw and worksheet, committed; the rater fills `gap_sheet.csv`.",
        "3. Classification (`gap_classification.csv`); rater review.",
        "4. `GAP_STUDY.md` with the decision rule applied.",
    ]
    return "\n".join(lines) + "\n"


def freeze(out: Path) -> int:
    """Write ``study.json`` / ``STUDY.md``; never overwrites."""
    target = out / "study.json"
    if target.exists():
        raise SystemExit(f"{target} exists; the study is frozen")
    design = json.loads((H_DIR / "design.json").read_text(encoding="utf-8"))
    doc = study_doc(design)
    out.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    (out / "STUDY.md").write_text(study_markdown(doc), encoding="utf-8")
    sys.stdout.write(study_markdown(doc))
    return 0


# --- select -----------------------------------------------------------------


def draw(study: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The frozen draw: all selected deaths plus the seeded unselected ones."""
    rng = random.Random(study["seed"])
    by_match: dict[str, list[str]] = defaultdict(list)
    for match, cid in study["population"]["unselected"]:
        by_match[match].append(cid)
    matches = sorted(by_match)
    rng.shuffle(matches)
    picked = [
        (m, rng.choice(sorted(by_match[m]))) for m in matches[: study["n_unselected"]]
    ]
    selected = [tuple(k) for k in study["population"]["selected"]]
    return selected + picked


def _committed(path: Path) -> bool:
    rel = str(path.relative_to(PROJECT_ROOT))
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel], cwd=PROJECT_ROOT, capture_output=True
    )
    clean = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", rel], cwd=PROJECT_ROOT
    )
    return tracked.returncode == 0 and clean.returncode == 0


def current_coaching(match: str, cid: str) -> str:
    """Arm-A (production) repeat-1 assessment plus recommendations."""
    short = match.removeprefix("en-live-playerview-")
    stem = re.sub(r"[^A-Za-z0-9]+", "_", f"{short}__{cid}").strip("_")
    path = H_DIR / "outputs" / "calls" / f"A__{stem}__r1.json"
    a = json.loads(path.read_text("utf-8"))["assessment"]
    if a is None:
        return "(no parsed output)"
    recs = " || ".join(s.strip() for s in a["recommendations"])
    return a["assessment"].strip() + (f" || {recs}" if recs else "")


def sheet_rows(
    deaths: Sequence[tuple[str, str]], selected: set[tuple[str, str]]
) -> list[dict[str, str]]:
    """Prefilled worksheet rows, in match / time order."""
    videos = {
        m["match"]: m["video"]
        for m in json.loads(RERUN_RECORD.read_text("utf-8"))["matches"]
    }
    rows = []
    for match, cid in sorted(deaths):
        t = float(cid.split(":", 1)[1])
        ident = json.loads((ANALYSIS_ROOT / match / "match_identity.json").read_text())
        rows.append(
            {
                "death": death_id(match, cid),
                "death_time": mmss(t),
                "recording": videos[match].replace(str(Path.home()), "~"),
                "watch_from": mmss(t - WATCH_BEFORE),
                "watch_to": mmss(t + WATCH_AFTER),
                "mode": str(ident.get("battle_mode_id") or ""),
                "stage": str(ident.get("stage_id") or ""),
                "selected_in_production": "Y" if (match, cid) in selected else "N",
                "current_coaching": current_coaching(match, cid),
                **{c: "" for c in USER_COLUMNS},
            }
        )
    return rows


def sheet_csv(rows: Sequence[Mapping[str, str]]) -> str:
    """CSV text of the worksheet."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


INSTRUCTIONS = """# Coaching gap study: how to fill `gap_sheet.csv`

One row per death. For each, open the recording, watch from `watch_from`
to `watch_to` (playback time from the start of the file), read
`current_coaching` (what the app says today), and fill the six empty
columns:

| column | what to write |
| --- | --- |
| `what_happened` | Your own short description of the death. |
| `fact_1` to `fact_3` | 1 to 3 facts most useful for coaching, one per cell. |
| `ideal_coaching` | 1 to 2 sentences you would actually want the app to tell you. |
| `why_current_falls_short` | What information is absent from the current coaching. |

**The rule:** do not reconstruct everything that happened. Ask:

> What is the smallest additional fact that would have made this coaching
> materially more useful?

Example. Instead of "I pushed toward the zone, their Heavy was on the
left, my teammates were behind me, I ran low on ink ...", write:

- fact_1: I entered the fight with special ready but died without using it.
- ideal_coaching: "You had your special ready before committing to this
  fight. Consider using it to create the opening instead of taking the
  first engagement directly."

Write facts as you saw them; do not try to judge whether the app could
detect them. That classification comes afterwards. Keep the prefilled
columns and the row order unchanged, save as CSV under the same name,
and say when it is done; it is committed before classification starts.
"""


def select(out: Path) -> int:
    """Draw and write the worksheet; refuses unless the freeze is committed."""
    study_path = out / "study.json"
    if not _committed(study_path):
        raise SystemExit("study.json must be committed unchanged before the draw")
    if (out / "gap_sheet.csv").exists():
        raise SystemExit("gap_sheet.csv exists; never overwritten")
    study = json.loads(study_path.read_text(encoding="utf-8"))
    deaths = draw(study)
    selected = {tuple(k) for k in study["population"]["selected"]}
    rows = sheet_rows(deaths, selected)
    (out / "selection.json").write_text(
        json.dumps({"seed": study["seed"], "deaths": [list(d) for d in deaths]}, indent=1)
        + "\n",
        encoding="utf-8",
    )
    (out / "gap_sheet.csv").write_text(sheet_csv(rows), encoding="utf-8")
    (out / "INSTRUCTIONS.md").write_text(INSTRUCTIONS, encoding="utf-8")
    sys.stdout.write(sheet_csv(rows))
    return 0


# --- summarize --------------------------------------------------------------


def validate(rows: Sequence[Mapping[str, str]], deaths: set[str]) -> list[str]:
    """Problems in ``gap_classification.csv``; one primary category per fact."""
    problems = []
    for n, r in enumerate(rows, start=2):
        if r["death"] not in deaths:
            problems.append(f"line {n}: unknown death {r['death']!r}")
        if r["category"] not in CATEGORIES:
            problems.append(f"line {n}: category must be one of A/B/C/D")
        if (r["category"] == "A") != (r["a_location"] in A_LOCATIONS):
            problems.append(f"line {n}: a_location is required for A and only for A")
        if r["theme"] not in THEMES:
            problems.append(f"line {n}: unknown theme {r['theme']!r}")
        if not r["fact"].strip() or not r["evidence"].strip():
            problems.append(f"line {n}: fact and evidence are required")
    return problems


def kind(r: Mapping[str, str]) -> str:
    """``A-view`` / ``A-pipeline`` / ``B`` / ``C`` / ``D``."""
    return f"A-{r['a_location']}" if r["category"] == "A" else r["category"]


_WORK_ORDER = ("A-view", "A-pipeline", "B")
_EXPERIMENT = {
    "A-view": "prompt / claim-catalog experiment",
    "A-pipeline": "view-extension experiment",
    "B": "fusion feature",
}


def decide(rows: Sequence[Mapping[str, str]], selected: Mapping[str, bool]) -> dict:
    """The frozen decision rule over classified facts."""
    cat = Counter(r["category"] for r in rows)
    ab = [r for r in rows if r["category"] in ("A", "B")]
    out: dict[str, Any] = {"categories": dict(cat), "ab": len(ab), "c": cat["C"]}
    if ab and len(ab) >= cat["C"]:
        themes = Counter(r["theme"] for r in ab)
        a_count = Counter(r["theme"] for r in ab if r["category"] == "A")
        best = max(themes.values())
        top = sorted(t for t, n in themes.items() if n == best)
        top_a = max(a_count[t] for t in top)
        top = [t for t in top if a_count[t] == top_a]
        kinds = Counter(kind(r) for r in ab if r["theme"] in top)
        most = max(kinds.values())
        chosen = next(k for k in _WORK_ORDER if kinds.get(k) == most)
        out.update(path="A/B", themes=top, kind=chosen, next=_EXPERIMENT[chosen])
    elif cat["C"]:
        c_themes = Counter(r["theme"] for r in rows if r["category"] == "C")
        best = max(c_themes.values())
        top = sorted(t for t, n in c_themes.items() if n == best)
        out.update(path="C", themes=top, kind="C", next="scope a detector")
    else:
        out.update(path="none", themes=[], kind=None, next="no actionable facts")
    per_death: dict[str, int] = {d: 0 for d in selected}
    for r in ab:
        per_death[r["death"]] += 1
    sel = [n for d, n in per_death.items() if selected[d]]
    uns = [n for d, n in per_death.items() if not selected[d]]
    gap = (sum(uns) / len(uns) - sum(sel) / len(sel)) if sel and uns else None
    out.update(
        ab_per_death_selected=round(sum(sel) / len(sel), 3) if sel else None,
        ab_per_death_unselected=round(sum(uns) / len(uns), 3) if uns else None,
        ranking_review=gap is not None and gap >= RANKING_REVIEW_MIN_GAP,
    )
    return out


def summary_markdown(rows: Sequence[Mapping[str, str]], doc: Mapping[str, Any]) -> str:
    """``GAP_STUDY.md``."""
    table = Counter((r["theme"], kind(r)) for r in rows)
    kinds = ("A-view", "A-pipeline", "B", "C", "D")
    themes = sorted({r["theme"] for r in rows})
    lines = [
        "# Coaching gap study results",
        "",
        f"{len(rows)} missing facts. Categories: "
        + ", ".join(f"{c} {doc['categories'].get(c, 0)}" for c in CATEGORIES),
        "",
        "| theme | " + " | ".join(kinds) + " |",
        "| --- | " + " | ".join("---" for _ in kinds) + " |",
        *[
            f"| {t} | " + " | ".join(str(table[(t, k)]) for k in kinds) + " |"
            for t in themes
        ],
        "",
        f"A + B facts per death: selected {doc['ab_per_death_selected']}, "
        f"unselected {doc['ab_per_death_unselected']}. Ranking review flagged: "
        f"{'yes' if doc['ranking_review'] else 'no'} "
        f"(threshold {RANKING_REVIEW_MIN_GAP}).",
        "",
        f"## Decision: {doc['next']}",
        "",
        f"Path {doc['path']} (A + B {doc['ab']} vs C {doc['c']}); "
        f"theme(s): {', '.join(doc['themes']) or 'none'}; kind: {doc['kind']}.",
    ]
    return "\n".join(lines) + "\n"


def summarize(out: Path) -> int:
    """Validate the classification and write ``GAP_STUDY.md``."""
    with (out / "gap_classification.csv").open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    with (out / "gap_sheet.csv").open(newline="", encoding="utf-8-sig") as fh:
        sheet = list(csv.DictReader(fh))
    selected = {r["death"]: r["selected_in_production"] == "Y" for r in sheet}
    problems = validate(rows, set(selected))
    if problems:
        raise SystemExit("invalid classification: " + "; ".join(problems))
    doc = decide(rows, selected)
    (out / "gap_study.json").write_text(json.dumps(doc, indent=1) + "\n", "utf-8")
    text = summary_markdown(rows, doc)
    (out / "GAP_STUDY.md").write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``freeze``, ``select``, ``summarize``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("freeze", "select", "summarize"))
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    return {"freeze": freeze, "select": select, "summarize": summarize}[args.command](
        args.out
    )


if __name__ == "__main__":
    raise SystemExit(main())
