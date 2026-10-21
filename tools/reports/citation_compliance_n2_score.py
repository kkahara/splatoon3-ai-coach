"""Strict N2 citation scoring for the citation-compliance experiment.

Frozen before any model output exists (``DESIGN.md``). Every
``evidence_used`` entry is read directly from the output text and checked
against the exact supplied view, independently of the production validator:
its legacy ``source_path`` label fallback never makes a citation compliant.

A citation is one ``path = value`` segment of an entry. Its class:

* ``n2_scalar_correct``: the path exists literally in the view, ends at a
  scalar (``null`` included) and the asserted value equals the value there.
* ``n2_scalar_wrong_value``: literal scalar path, different value.
* ``n2_object_asserted``: literal path to an object or list, value asserted.
* ``source_path_field``: the literal path ends at a ``source_path`` field
  (citing provenance, which the prompt forbids).
* ``legacy_label``: not a literal path; equals a ``source_path`` label of the
  view, or is a label followed by a remainder (hybrid).
* ``n1_coachinput``: not a literal path; starts at a CoachInput-only root.
* ``relative_path``: not a literal path; a literal path ends with it.
* ``unresolved_path``: starts at a view block, but does not resolve.
* ``unsupported_invented``: names a block the view does not supply.
* ``malformed``: the entry has no ``path = value`` segment.

Values are compared by meaning at the exact location: numbers exactly (no
tolerance), booleans and ``null`` by JSON value, text exactly. The written
form is recorded separately (``exact`` JSON literal, ``remark`` after it,
``lenient`` unquoted / unit suffix); it is a secondary metric, not a gate.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

COMPLIANT = "n2_scalar_correct"
CLASSES = (
    COMPLIANT,
    "n2_scalar_wrong_value",
    "n2_object_asserted",
    "source_path_field",
    "legacy_label",
    "n1_coachinput",
    "relative_path",
    "unresolved_path",
    "unsupported_invented",
    "malformed",
)
GATE_GROUPS: dict[str, tuple[str, ...]] = {
    "unsupported_or_invented": ("unsupported_invented",),
    "wrong_value": ("n2_scalar_wrong_value",),
    "object_path_asserted": ("n2_object_asserted",),
    "legacy_form": ("source_path_field", "legacy_label", "n1_coachinput"),
    "malformed_or_unresolved": ("relative_path", "unresolved_path", "malformed"),
}
GATE_RULES = {
    "all_calls_parsed": "every sealed call has a parseable model reply",
    **{
        f"zero_{group}": f"no citation of class {', '.join(c)}"
        for group, c in GATE_GROUPS.items()
    },
    "support_cited": (
        "every output with a recommendation or a selected claim has at least "
        "one n2_scalar_correct citation"
    ),
}
N1_ROOTS = frozenset(
    {
        "durations",
        "game_clock_samples",
        "playback_clock",
        "player_count_context",
        "player_count_samples",
        "player_count_window",
        "primary_context",
        "primary_scenario",
        "score_facts",
        "zone_control_facts",
    }
)
_N1_RELATED = re.compile(r"^related\[\d+\]\.(?:context|scenario)(?:[.\[]|$)")
_PATH = r"[A-Za-z_]\w*(?:\[[^\]\s]+\])*(?:\.[A-Za-z_]\w*(?:\[[^\]\s]+\])*)*"
_ASSIGN = re.compile(rf"(?<![\w.\[\]])({_PATH})\s*=(?!=)\s*")
_COLON = re.compile(rf"^\s*{_PATH}\s*:\s")
_DOTTED = re.compile(r"[A-Za-z_]\w*(?:\[[^\]\s]+\])*(?:\.[A-Za-z_]\w*(?:\[[^\]\s]+\])*)+")
_SEGMENT = re.compile(r"([A-Za-z_]\w*)((?:\[\d+\])*)")
_INDEX = re.compile(r"\[(\d+)\]")
_SEPARATOR_TAIL = re.compile(r"[\s,;]*(?:\band\b)?[\s,;]*$")
_REMARK = re.compile(r"\s*(?:;|,\s|\s\(|\s-\s).*$")
_UNIT = re.compile(r"(?i)\s*(?:s|secs?|seconds?)$")
_NOTHING = object()


def walk(view: Any, path: str) -> tuple[bool, Any]:
    """The value at a literal view path (``[i]`` list indexes), never via labels."""
    node = view
    for segment in path.split("."):
        match = _SEGMENT.fullmatch(segment)
        if match is None or not isinstance(node, dict) or match.group(1) not in node:
            return False, None
        node = node[match.group(1)]
        for index in _INDEX.findall(match.group(2)):
            if not isinstance(node, list) or int(index) >= len(node):
                return False, None
            node = node[int(index)]
    return True, node


def _node_paths(node: Any, path: str = "") -> list[str]:
    out = [path] if path else []
    if isinstance(node, dict):
        for key, value in node.items():
            out += _node_paths(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            out += _node_paths(value, f"{path}[{index}]")
    return out


@dataclass(frozen=True)
class ViewIndex:
    """Per-view lookups: every node path, labels, top-level keys."""

    nodes: tuple[str, ...]
    labels: tuple[str, ...]
    top: frozenset[str]


def view_index(view: Mapping[str, Any]) -> ViewIndex:
    """Index one view; labels that are themselves literal paths are skipped."""
    nodes = tuple(_node_paths(view))
    labels = {
        str(value)
        for path in nodes
        if path.split(".")[-1] == "source_path"
        and isinstance(value := walk(view, path)[1], str)
        and not walk(view, value)[0]
    }
    return ViewIndex(nodes=nodes, labels=tuple(sorted(labels)), top=frozenset(view))


def _value_end(entry: str, start: int) -> int:
    try:
        return json.JSONDecoder().raw_decode(entry, start)[1]
    except ValueError:
        return start


def segments(entry: str) -> list[tuple[int, str, str]]:
    """``(offset, path, raw value)`` for every ``path = value`` in an entry."""
    out: list[tuple[int, str, str]] = []
    pos = 0
    while (match := _ASSIGN.search(entry, pos)) is not None:
        following = _ASSIGN.search(entry, _value_end(entry, match.end()))
        end = following.start() if following else len(entry)
        raw = _SEPARATOR_TAIL.sub("", entry[match.end() : end])
        out.append((match.start(), match.group(1), raw))
        pos = end
    return out


def _equal(value: Any, actual: Any) -> bool:
    if isinstance(value, bool) or isinstance(actual, bool):
        return type(value) is type(actual) and value == actual
    if isinstance(value, int | float) and isinstance(actual, int | float):
        return float(value) == float(actual)
    return type(value) is type(actual) and value == actual


def _lenient(text: str, actual: Any) -> bool:
    for candidate in (text, _REMARK.sub("", text)):
        cut = candidate.strip().rstrip(".").strip("\"'`")
        if actual is None and cut.lower() in ("null", "none"):
            return True
        if isinstance(actual, bool) and cut.lower() == str(actual).lower():
            return True
        if isinstance(actual, int | float) and not isinstance(actual, bool):
            try:
                if float(_UNIT.sub("", cut)) == float(actual):
                    return True
            except ValueError:
                continue
        if isinstance(actual, str) and cut == actual:
            return True
    return False


def value_form(raw: str, actual: Any) -> str:
    """``exact`` / ``remark`` / ``lenient`` when the value matches, else ``wrong``."""
    text = raw.strip()
    try:
        value, end = json.JSONDecoder().raw_decode(text)
    except ValueError:
        value, end = _NOTHING, 0
    if value is not _NOTHING and _equal(value, actual):
        return "remark" if text[end:].strip(" \t.,;") else "exact"
    return "lenient" if _lenient(text, actual) else "wrong"


def _unresolved_class(path: str, index: ViewIndex) -> str:
    for label in index.labels:
        if path == label or path.startswith((label + ".", label + "[")):
            return "legacy_label"
    head = _SEGMENT.match(path)
    root = head.group(1) if head else path
    if root in N1_ROOTS or _N1_RELATED.match(path):
        return "n1_coachinput"
    if any(node.endswith("." + path) for node in index.nodes):
        return "relative_path"
    return "unresolved_path" if root in index.top else "unsupported_invented"


def classify(
    view: Mapping[str, Any], path: str, raw: str, index: ViewIndex
) -> dict[str, Any]:
    """One ``path = value`` citation: class, literal facts and value form."""
    found, actual = walk(view, path)
    scalar = found and not isinstance(actual, dict | list)
    row: dict[str, Any] = {
        "path": path,
        "asserted": raw,
        "exists_literally": found,
        "terminates_at_scalar": scalar,
        "value_form": None,
        "value_matches": None,
    }
    if not found:
        return {**row, "class": _unresolved_class(path, index)}
    if path.split(".")[-1] == "source_path":
        return {**row, "class": "source_path_field"}
    if not scalar:
        return {**row, "class": "n2_object_asserted"}
    form = value_form(raw, actual)
    correct = form != "wrong"
    return {
        **row,
        "view_value": actual,
        "value_form": form,
        "value_matches": correct,
        "class": COMPLIANT if correct else "n2_scalar_wrong_value",
    }


def _malformed(entry: str, index: ViewIndex) -> dict[str, Any]:
    dotted = _DOTTED.search(entry)
    if _COLON.match(entry):
        reason = "colon_separator"
    elif dotted is not None or entry.strip() in index.top:
        reason = "path_without_value"
    else:
        reason = "no_path"
    return {
        "path": dotted.group(0) if dotted else None,
        "class": "malformed",
        "reason": reason,
        "exists_literally": None,
        "terminates_at_scalar": None,
        "value_form": None,
        "value_matches": None,
    }


def score_entry(view: Mapping[str, Any], entry: str, index: ViewIndex) -> dict[str, Any]:
    """Every citation in one ``evidence_used`` entry, plus form flags."""
    found = segments(entry)
    citations = (
        [classify(view, path, raw, index) for _, path, raw in found]
        if found
        else [_malformed(entry, index)]
    )
    return {
        "text": entry,
        "citations": citations,
        "compliant": all(c["class"] == COMPLIANT for c in citations),
        "prose_prefix": bool(found and entry[: found[0][0]].strip()),
        "multi_fact": len(found) > 1,
    }


def _claim_coverage(
    view: Mapping[str, Any], selected: Sequence[str], cited: set[str]
) -> list[dict[str, Any]]:
    paths = {e["claim_id"]: e["evidence_paths"] for e in view.get("claim_basis") or []}
    return [
        {
            "claim_id": claim,
            "in_claim_basis": claim in paths,
            "evidence_path_cited": bool(set(paths.get(claim, ())) & cited),
        }
        for claim in selected
    ]


def validator_comparison(
    validation: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Production validator verdict per entry beside the strict verdict."""
    r7 = Counter(
        h["text"]
        for h in validation["integrity_hits"]
        if h["rule"] == "R7" and h.get("field") == "evidence_used"
    )
    rows = []
    for entry, res in zip(entries, validation["evidence_path_resolution"], strict=True):
        rows.append(
            {
                "text": entry["text"],
                "validator_kind": res["kind"],
                "validator_location": res.get("location"),
                "validator_accepts": res["kind"] != "unresolved"
                and not r7[entry["text"]],
                "strict_compliant": entry["compliant"],
            }
        )
    return {
        "validation_status": validation["validation_status"],
        "entries": rows,
        "disagreements": [
            r for r in rows if r["validator_accepts"] != r["strict_compliant"]
        ],
    }


def score_output(view: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    """Strict citation scoring of one call record (never rewritten)."""
    base = {"call_id": record["call_id"], "match": record["match"], "cid": record["cid"]}
    assessment = record.get("assessment")
    if assessment is None:
        return {**base, "parsed": False, "entries": [], "output_compliant": False}
    index = view_index(view)
    entries = [score_entry(view, str(e), index) for e in assessment["evidence_used"]]
    citations = [c for e in entries for c in e["citations"]]
    compliant_paths = {c["path"] for c in citations if c["class"] == COMPLIANT}
    requires = bool(assessment["recommendations"] or assessment["selected_claim_ids"])
    support_ok = bool(compliant_paths) or not requires
    return {
        **base,
        "parsed": True,
        "entries": entries,
        "requires_support": requires,
        "support_ok": support_ok,
        "output_compliant": all(e["compliant"] for e in entries) and support_ok,
        "claim_coverage": _claim_coverage(
            view, assessment["selected_claim_ids"], compliant_paths
        ),
        "validator": validator_comparison(record["validation"], entries),
    }


def _citations(outputs: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [c for o in outputs for e in o["entries"] for c in e["citations"]]


def gate(outputs: Sequence[Mapping[str, Any]], expected_calls: int) -> dict[str, Any]:
    """The frozen pass / fail rule over all scored outputs."""
    classes = Counter(c["class"] for c in _citations(outputs))
    groups = {g: sum(classes[c] for c in members) for g, members in GATE_GROUPS.items()}
    parsed = sum(1 for o in outputs if o["parsed"])
    checks = {
        "all_calls_parsed": len(outputs) == expected_calls and parsed == expected_calls,
        **{f"zero_{group}": count == 0 for group, count in groups.items()},
        "support_cited": all(o["support_ok"] for o in outputs if o["parsed"]),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "group_counts": groups,
        "class_counts": {c: classes[c] for c in CLASSES},
    }


def _family(path: str | None) -> str:
    if not path:
        return "(none)"
    head = _SEGMENT.match(path)
    return head.group(1) if head else path


def summary(outputs: Sequence[Mapping[str, Any]], expected_calls: int) -> dict[str, Any]:
    """Gate plus per-citation, per-entry, per-output and secondary metrics."""
    parsed = [o for o in outputs if o["parsed"]]
    citations = _citations(outputs)
    entries = [e for o in outputs for e in o["entries"]]
    by_unit: dict[tuple[str, str], list[bool]] = {}
    for o in outputs:
        by_unit.setdefault((o["match"], o["cid"]), []).append(o["output_compliant"])
    disagreements = [
        {"call_id": o["call_id"], **d}
        for o in parsed
        for d in o["validator"]["disagreements"]
    ]
    return {
        "gate": gate(outputs, expected_calls),
        "outputs": {
            "total": len(outputs),
            "parsed": len(parsed),
            "compliant": sum(o["output_compliant"] for o in outputs),
        },
        "entries": {
            "total": len(entries),
            "compliant": sum(e["compliant"] for e in entries),
            "prose_prefix": sum(e["prose_prefix"] for e in entries),
            "multi_fact": sum(e["multi_fact"] for e in entries),
        },
        "citations": {
            "total": len(citations),
            "compliant": sum(c["class"] == COMPLIANT for c in citations),
        },
        "value_forms": dict(
            Counter(c["value_form"] for c in citations if c["value_form"])
        ),
        "malformed_reasons": dict(
            Counter(c["reason"] for c in citations if "reason" in c)
        ),
        "families": dict(Counter(_family(c["path"]) for c in citations)),
        "outputs_without_citations": sum(1 for o in parsed if not o["entries"]),
        "units_compliant_every_repeat": sum(all(v) for v in by_unit.values()),
        "units": len(by_unit),
        "selected_claims": {
            "total": sum(len(o["claim_coverage"]) for o in parsed),
            "evidence_path_cited": sum(
                c["evidence_path_cited"] for o in parsed for c in o["claim_coverage"]
            ),
        },
        "validator_status": dict(
            Counter(o["validator"]["validation_status"] for o in parsed)
        ),
        "validator_disagreements": disagreements,
    }
