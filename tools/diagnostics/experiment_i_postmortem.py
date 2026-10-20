"""Experiment I post-result diagnostic (read-only; no model calls).

Answers whether Experiment I's safety failure (adjusted validator hits in arm
M) reflects unsupported evidence or a mismatch between the view structure,
the prompt's path convention, the model's citations and the validator. It
never modifies Experiment I artifacts, never re-runs the validator for a
result and never recomputes the decision rule.

``run`` writes to ``analysis/experiment_i_postmortem/``:

* ``preservation.json``: Experiment I hashes and commits verified unchanged;
* ``evidence_path_rows.csv`` / ``.json``: one row per unresolved evidence
  path in any call record, citation recorded verbatim, with the frozen
  classification rule below;
* ``citation_forms.json``: every evidence citation by arm, path family and
  validator kind, the map_awareness citation form per eligible M output, and
  each system-prompt citation example resolved against the 22 sealed A views;
* ``ratings_diagnostic.json``: descriptive structure of the sealed ratings.

Classification (fixed before the table was generated):

* ``A`` namespace/path mismatch with unambiguous supplied evidence: exactly
  one supplied view leaf is reachable from the citation, either by dropping
  leading namespace segments (``prefix``) or through the block carrying the
  cited ``source_path`` label (``label_block``), and its value equals the
  cited value (production ``_same``);
* ``B`` ambiguous: more than one such leaf has an equal value, or equal and
  unequal candidates coexist;
* ``C`` unsupported: no such leaf and no supplied leaf with the cited final
  key and an equal value;
* ``D`` other: anything else (described in the row).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
import sys
import types
import typing
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from statistics import mean
from typing import Any

from loguru import logger
from pydantic import BaseModel

from splatoon3_ai_coach.coach.claim_validation import (
    _cited_value,
    _same,
    _source_blocks,
    _walk,
    resolve_evidence_reference,
)
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.config.paths import PROJECT_ROOT

EXP_DIR = PROJECT_ROOT / "analysis" / "importance_report" / "coaching_units_experiment_i"
OUT_DIR = PROJECT_ROOT / "analysis" / "experiment_i_postmortem"
FINAL_COMMIT = "419fae1"
COMMITS = {
    "stop_point_2_seal": "79be6ac",
    "run_tooling": "2d47e36",
    "run_outputs": "4f656e9",
    "sealed_ratings": "c20b7cb",
    "analysis_tooling": "81634bd",
    "stop_point_4_analysis": FINAL_COMMIT,
}
SHEET_SHA256 = "b5db14eec2fdfe754c93a4e9019b9df79c317f3ebdce1bf7324a0f00d5d29616"
KEY_SHA256 = "d368951c90c2467eae774aeccc149155e147cf231ace4e324b279d68e0316eca"
NULL_BLOCK = ("en-live-playerview-2026-10-11 20-33-31", "death_episode:199.500")
RATING_FIELDS = (
    "usefulness_1to5",
    "redundancy_1to5",
    "anchor_shows_situation_1to5",
    "missing_context_1to5",
    "supported_yes_no",
    "map_use_correct_incorrect_not_mentioned",
)
_SELECTOR = re.compile(r"\[[^\]]*\]")


# --- preservation ---------------------------------------------------------------


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sums_problems(exp: Path) -> list[str]:
    """Files whose sha256 differs from ``SHA256SUMS``, or that are not listed."""
    listed = {}
    for line in (exp / "SHA256SUMS").read_text("utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        listed[rel] = digest
    on_disk = {str(p.relative_to(exp)) for p in exp.rglob("*") if p.is_file()} - {
        "SHA256SUMS"
    }
    problems = [f"{rel} changed" for rel, d in listed.items() if _sha(exp / rel) != d]
    problems += [f"{rel} not in SHA256SUMS" for rel in sorted(on_disk - set(listed))]
    problems += [f"{rel} missing" for rel in sorted(set(listed) - on_disk)]
    return problems


def preservation(exp: Path = EXP_DIR) -> dict[str, Any]:
    """Experiment I verified unchanged against ``SHA256SUMS`` and its commits."""
    rel = str(exp.relative_to(PROJECT_ROOT))
    resolved = {
        name: _git("rev-parse", "--verify", "--quiet", f"{c}^{{commit}}").stdout.strip()
        for name, c in COMMITS.items()
    }
    checks = {
        "sha256sums_all_files": sums_problems(exp),
        "dir_identical_to_final_commit": []
        if _git("diff", "--quiet", FINAL_COMMIT, "--", rel).returncode == 0
        else [f"{rel} differs from {FINAL_COMMIT}"],
        "rating_sheet_hash": []
        if _sha(exp / "rating_sheet.csv") == SHEET_SHA256
        else ["rating_sheet.csv hash differs"],
        "rating_key_hash": []
        if _sha(exp / "rating_key.csv") == KEY_SHA256
        else ["rating_key.csv hash differs"],
        "commits_resolve": [n for n, full in resolved.items() if not full],
    }
    return {
        "head_commit": _git("rev-parse", "HEAD").stdout.strip(),
        "commits": resolved,
        "sha256sums_sha256": _sha(exp / "SHA256SUMS"),
        "files_verified": len((exp / "SHA256SUMS").read_text().splitlines()),
        "checks": checks,
        "passed": not any(checks.values()),
    }


# --- views and CoachInput schema ------------------------------------------------------


def split_view(prompt: str) -> dict[str, Any]:
    """The lead CoachLlmView JSON of a sealed user prompt."""
    view, _ = json.JSONDecoder().raw_decode(prompt[prompt.index("{") :])
    return view


def leaves(node: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Every scalar leaf as ``(dotted path with [i], value)``."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from leaves(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from leaves(value, f"{prefix}[{i}]")
    else:
        yield prefix, node


def _model_of(annotation: Any) -> type[BaseModel] | None:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType, list, tuple, typing.Annotated):
        for arg in typing.get_args(annotation):
            found = _model_of(arg)
            if found is not None:
                return found
        return None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def coachinput_field_exists(path: str, root: type[BaseModel] = CoachInput) -> bool:
    """Whether ``path`` (selectors ignored) names a serialized CoachInput field."""
    model: type[BaseModel] | None = root
    for segment in _SELECTOR.sub("", path).split("."):
        if model is None:
            return False
        if segment in model.model_fields:
            model = _model_of(model.model_fields[segment].annotation)
        elif segment in model.model_computed_fields:
            model = _model_of(model.model_computed_fields[segment].return_type)
        else:
            return False
    return True


# --- evidence-path classification ---------------------------------------------------


def prefix_candidates(view: Mapping[str, Any], path: str) -> list[tuple[str, Any]]:
    """View leaves equal to ``path`` with 1+ leading segments dropped."""
    segments = path.split(".")
    out = []
    for k in range(1, len(segments)):
        rest = ".".join(segments[k:])
        found, value = _walk(view, rest.split("."))
        if found and not isinstance(value, dict | list):
            out.append((rest, value))
    return out


def _block_paths(view: Mapping[str, Any]) -> dict[int, str]:
    return {id(node): p for p, node in _container_paths(view)}


def _container_paths(node: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict):
        yield prefix, node
        for key, value in node.items():
            yield from _container_paths(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _container_paths(value, f"{prefix}[{i}]")


def label_candidates(view: Mapping[str, Any], path: str) -> list[tuple[str, Any]]:
    """Leaves with the cited final key inside the block whose label prefixes ``path``."""
    where = _block_paths(view)
    final = path.split(".")[-1]
    out = []
    for block in _source_blocks(view):
        label = block["source_path"]
        if not path.startswith(label + "."):
            continue
        base = where[id(block)]
        for rel, value in leaves(block):
            if rel.split(".")[-1] == final:
                out.append((f"{base}.{rel}" if base else rel, value))
    return out


def classify(view: Mapping[str, Any], text: str, path: str) -> dict[str, Any]:
    """Frozen A/B/C/D classification of one unresolved citation."""
    cited = _cited_value(text, path)
    by_prefix = dict(prefix_candidates(view, path))
    pool = {**by_prefix, **dict(label_candidates(view, path))}
    equal = {p: v for p, v in pool.items() if cited is not None and _same(cited, v)}
    final = path.split(".")[-1]
    same_key = {
        p: v
        for p, v in leaves(view)
        if p.split(".")[-1] == final and cited is not None and _same(cited, v)
    }
    if len(equal) == 1 and len(pool) == 1:
        category, how = "A", "prefix" if by_prefix else "label_block"
    elif len(equal) > 1 or (equal and len(pool) > len(equal)):
        category, how = "B", "multiple_candidates"
    elif not pool and not same_key:
        category, how = "C", "no_supplied_evidence"
    else:
        category, how = "D", "see_candidates"
    return {
        "cited_value": cited,
        "candidates": pool,
        "equal_candidates": equal,
        "same_final_key_equal_value_anywhere": same_key,
        "category": category,
        "mapping": how,
    }


def _key_rows(exp: Path) -> dict[str, dict[str, str]]:
    with (exp / "rating_key.csv").open(newline="", encoding="utf-8") as fh:
        return {r["call_id"]: r for r in csv.DictReader(fh)}


def path_rows(exp: Path = EXP_DIR) -> list[dict[str, Any]]:
    """One row per unresolved evidence path in any Experiment I call record."""
    key = _key_rows(exp)
    rows = []
    for record_path in sorted((exp / "outputs" / "calls").glob("*.json")):
        record = json.loads(record_path.read_text("utf-8"))
        view = split_view((exp / "inputs" / record["file"]).read_text("utf-8"))
        for hit in record["validation"]["unresolved_evidence_paths"]:
            found, _ = _walk(view, hit["path"].split("."))
            c = classify(view, hit["text"], hit["path"])
            rows.append(
                {
                    "output_id": key[record["call_id"]]["output_id"],
                    "call_id": record["call_id"],
                    "match": record["match"].removeprefix("en-live-playerview-"),
                    "death": record["cid"],
                    "arm": record["arm"],
                    "repeat": record["repeat"],
                    "eligible": record["eligible"],
                    "citation": hit["text"],
                    "path": hit["path"],
                    "validator_kind": hit["kind"],
                    "validator_reason": hit["reason"],
                    "literal_path_in_view": found,
                    "coachinput_schema_field": coachinput_field_exists(hit["path"]),
                    **c,
                }
            )
    return rows


def prompt_examples(system_prompt: str) -> list[str]:
    """Quoted citation examples in the EVIDENCE CITATION RULE section."""
    section = system_prompt.split("EVIDENCE CITATION RULE", 1)[1].split(
        "Output format", 1
    )[0]
    return [
        json.loads(line.strip())
        for line in section.splitlines()
        if line.strip().startswith('"') and line.strip().endswith('"')
    ]


def example_resolution(exp: Path = EXP_DIR) -> dict[str, dict[str, int]]:
    """Each system-prompt citation example resolved against every sealed A view."""
    examples = prompt_examples(
        (exp / "inputs" / "system_prompt_A.txt").read_text("utf-8")
    )
    out: dict[str, Counter] = {e: Counter() for e in examples}
    for path in sorted((exp / "inputs" / "A").glob("*.user_prompt.txt")):
        view = split_view(path.read_text("utf-8"))
        for e in examples:
            r = resolve_evidence_reference(view, e)
            out[e][r["kind"] + (f"/{r['reason']}" if "reason" in r else "")] += 1
    return {e: dict(c) for e, c in out.items()}


def _family(path: str) -> str:
    if path.startswith("map_awareness."):
        return "map_awareness (top level)"
    if path.startswith("primary_context.map_awareness."):
        return "primary_context.map_awareness"
    return "primary_context (other)" if path.startswith("primary_context.") else "other"


def citation_forms(exp: Path = EXP_DIR) -> dict[str, Any]:
    """Every evidence citation by arm, path family and validator kind; M forms."""
    kinds: Counter = Counter()
    forms: dict[str, dict[str, str]] = defaultdict(dict)
    for p in sorted((exp / "outputs" / "calls").glob("*.json")):
        record = json.loads(p.read_text("utf-8"))
        fams = set()
        for res in record["validation"]["evidence_path_resolution"]:
            fam = _family(str(res.get("path") or ""))
            kinds[f"{record['arm']} | {fam} | {res['kind']}"] += 1
            fams.add(fam)
        if record["arm"] == "M" and record["eligible"]:
            form = next(
                (
                    f
                    for f in (
                        "primary_context.map_awareness",
                        "map_awareness (top level)",
                    )
                    if f in fams
                ),
                "none",
            )
            death = (
                f"{record['match'].removeprefix('en-live-playerview-')} {record['cid']}"
            )
            forms[death][str(record["repeat"])] = form
    return {
        "citations_by_arm_family_kind": dict(sorted(kinds.items())),
        "eligible_m_map_awareness_form": dict(sorted(forms.items())),
        "eligible_m_form_counts": dict(
            Counter(f for v in forms.values() for f in v.values())
        ),
        "deaths_where_repeats_differ": sum(
            v.get("1") != v.get("2") for v in forms.values()
        ),
    }


# --- ratings ------------------------------------------------------------------------


def _group(r: Mapping[str, str]) -> str:
    if (r["match"], r["cid"]) == NULL_BLOCK:
        return "null_block"
    return "eligible" if r["eligible"] == "True" else "not_eligible"


def joined_ratings(exp: Path = EXP_DIR) -> list[dict[str, str]]:
    """Sealed sheet rows with the key's arm / repeat / eligibility attached."""
    key = {r["output_id"]: r for r in _key_rows(exp).values()}
    with (exp / "rating_sheet.csv").open(newline="", encoding="utf-8") as fh:
        sheet = list(csv.DictReader(fh))
    rows = []
    for r in sheet:
        k = key[r["output_id"]]
        rows.append(
            {
                **r,
                **{f: k[f] for f in ("call_id", "cid", "arm", "repeat")},
                "match": k["match"],
                "eligible": k["eligible"],
            }
        )
    for r in rows:
        r["group"] = _group(r)
        record = json.loads(
            (exp / "outputs" / "calls" / f"{r['call_id']}.json").read_text("utf-8")
        )
        recs = (record.get("assessment") or {}).get("recommendations") or []
        r["has_recommendation"] = str(bool(recs))
    return rows


def _pattern(r: Mapping[str, str]) -> str:
    return " / ".join(r[f] for f in RATING_FIELDS)


def note_template(note: str) -> str:
    """Note with numbers, clock times and quoted text abstracted."""
    t = re.sub(r"\d+:\d{2}", "<clock>", note)
    t = re.sub(r"\d+(?:\.\d+)?", "<n>", t)
    return re.sub(r"\s+", " ", t).strip()


def ratings_diagnostic(rows: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    """Distributions, duplicate patterns, map-use relation and note templates."""
    cells = defaultdict(list)
    for r in rows:
        cells[f"{r['arm']} {r['group']}"].append(r)
    by_death: dict[tuple, dict[str, str]] = defaultdict(dict)
    for r in rows:
        by_death[(r["arm"], r["match"], r["cid"])][r["repeat"]] = _pattern(r)
    use_by_map = defaultdict(list)
    for r in rows:
        use_by_map[r["map_use_correct_incorrect_not_mentioned"]].append(
            int(r["usefulness_1to5"])
        )
    return {
        "distribution": {
            cell: {
                f: dict(sorted(Counter(r[f] for r in rs).items())) for f in RATING_FIELDS
            }
            for cell, rs in sorted(cells.items())
        },
        "patterns": {
            cell: dict(Counter(_pattern(r) for r in rs).most_common())
            for cell, rs in sorted(cells.items())
        },
        "distinct_patterns_overall": len({_pattern(r) for r in rows}),
        "repeat_pairs_identical_pattern": sum(
            v.get("1") == v.get("2") for v in by_death.values()
        ),
        "repeat_pairs": len(by_death),
        "usefulness_by_map_use": {
            k: {"n": len(v), "mean": round(mean(v), 4), "values": dict(Counter(v))}
            for k, v in sorted(use_by_map.items())
        },
        "usefulness_by_arm_group_recommendation": _by_recommendation(rows),
        "notes": _notes(rows),
    }


def _by_recommendation(rows: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    groups = defaultdict(list)
    for r in rows:
        key = f"{r['arm']} {r['group']} has_recommendation={r.get('has_recommendation')}"
        groups[key].append(int(r["usefulness_1to5"]))
    return {
        k: {"n": len(v), "values": dict(sorted(Counter(v).items()))}
        for k, v in sorted(groups.items())
    }


def _notes(rows: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    templates = defaultdict(Counter)
    for r in rows:
        templates[f"{r['arm']} {r['group']}"][note_template(r["notes"])] += 1
    return {
        "distinct_notes": len({r["notes"] for r in rows}),
        "distinct_templates": len({note_template(r["notes"]) for r in rows}),
        "templates_by_cell": {
            cell: dict(c.most_common()) for cell, c in sorted(templates.items())
        },
    }


# --- output -------------------------------------------------------------------------

CSV_FIELDS = (
    "output_id",
    "match",
    "death",
    "arm",
    "repeat",
    "eligible",
    "citation",
    "validator_reason",
    "literal_path_in_view",
    "coachinput_schema_field",
    "cited_value",
    "equal_candidates",
    "category",
    "mapping",
    "notes",
)


def _csv_rows(rows: Sequence[Mapping[str, Any]], notes: Mapping[str, str]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow(
            {
                **{f: r.get(f) for f in CSV_FIELDS},
                "equal_candidates": "; ".join(
                    f"{p} = {json.dumps(v)}" for p, v in r["equal_candidates"].items()
                ),
                "notes": next(
                    (n for prefix, n in notes.items() if r["path"].startswith(prefix)), ""
                ),
            }
        )
    return buf.getvalue()


ROW_NOTES = {
    "primary_context.map_awareness.": (
        "map_awareness is an experiment-only top-level view block (not CoachInput, "
        "no source_path label); the citation places it under the CoachInput "
        "primary_context namespace used by the prompt's examples and by labels"
    ),
    "primary_context.special.": (
        "exact serialized CoachInput path (ScenarioContext special computed field "
        "state_at_anchor); the view projects it to top-level special.state_at_anchor "
        "in a block with no source_path label (only its child readings have one)"
    ),
    "primary_context.timeline.": (
        "hybrid: CoachInput namespace primary_context.timeline (a float there) plus "
        "the view's ClockedDuration '.value'; the view block is labelled 'durations'"
    ),
    "game_clock_samples[label=death].": (
        "exact CoachInput path (GameClockSample.observation.display); the view "
        "clock block carries this label but flattens observation.* into itself"
    ),
}


def run(out: Path = OUT_DIR, exp: Path = EXP_DIR) -> int:
    """Write the diagnostic artifacts; refuses unless Experiment I is unchanged."""
    keep = preservation(exp)
    if not keep["passed"]:
        raise SystemExit(f"Experiment I artifacts changed; no diagnostic: {keep}")
    out.mkdir(parents=True, exist_ok=True)
    rows = path_rows(exp)
    (out / "preservation.json").write_text(json.dumps(keep, indent=1) + "\n", "utf-8")
    (out / "evidence_path_rows.json").write_text(
        json.dumps(rows, indent=1, ensure_ascii=False) + "\n", "utf-8"
    )
    (out / "evidence_path_rows.csv").write_text(_csv_rows(rows, ROW_NOTES), "utf-8")
    forms = {**citation_forms(exp), "system_prompt_examples": example_resolution(exp)}
    (out / "citation_forms.json").write_text(json.dumps(forms, indent=1) + "\n", "utf-8")
    diag = ratings_diagnostic(joined_ratings(exp))
    (out / "ratings_diagnostic.json").write_text(
        json.dumps(diag, indent=1, ensure_ascii=False) + "\n", "utf-8"
    )
    if not preservation(exp)["passed"]:
        raise SystemExit("Experiment I artifacts changed during the diagnostic")
    summary = Counter((r["arm"], r["category"], r["mapping"]) for r in rows)
    sys.stdout.write(
        json.dumps({" ".join(k): n for k, n in sorted(summary.items())}) + "\n"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``run``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("run",))
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    return run(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
