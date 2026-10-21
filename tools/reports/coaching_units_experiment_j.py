"""Experiment J (frozen in ``DESIGN.md``): stop point 2A, census order.

Research only. ``census-order`` enumerates the frozen candidate pool, applies
only the recording-level rules that need no gameplay analysis, sorts by the
frozen timestamp/path rule and writes ``census_order.json`` once. Nothing is
analyzed here; the player-view source rule and per-death map observability
are applied at stop point 2B.

Other prior uses of a recording (earlier coaching-output experiments,
death-importance studies, vision corpora) are recorded as annotations. They
never change inclusion or order; only an approved amendment can act on them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.media.video_identity import video_identity

REPORT_DIR = PROJECT_ROOT / "analysis" / "importance_report"
OUT_DIR = REPORT_DIR / "coaching_units_experiment_j"
CENSUS_ORDER = OUT_DIR / "census_order.json"
HELDOUT = PROJECT_ROOT / "analysis" / "game_tape_probe" / "heldout" / "heldout.json"
MOVIES = Path.home() / "Movies"
TESTS_DIR = PROJECT_ROOT / "tests"

SOURCE_HELDOUT = "game_tape_heldout"
SOURCE_LIVE = "live_playerview"
LIVE_NAME = re.compile(r"^en-live-playerview-")
VIDEO_SUFFIXES = (".mov", ".mp4", ".mkv")
TIMESTAMP = re.compile(r"(\d{4}-\d{2}-\d{2})[ _](\d{2})[-_](\d{2})[-_](\d{2})")
REVIEW_NAME = re.compile(r"review|preview|timeline", re.IGNORECASE)

FROZEN_PRIOR_USE = {
    "experiment_h": "coaching_units_experiment_h",
    "experiment_i": "coaching_units_experiment_i",
    "coaching_gap_study": "coaching_gap_study",
    "n2_citation_experiment": "citation_compliance_n2",
}
CITATION_FIXTURE_GLOB = "*citation*.py"
TEXT_SUFFIXES = (".json", ".csv", ".md", ".yaml", ".txt", ".jsonl", ".py")
ORDER_RULE = "timestamp from file name (YYYY-MM-DD HH-MM-SS), ties by full source path"
CENSUS_EXCLUSIONS = OUT_DIR / "census_exclusions.json"
COACHING_OUTPUT_REFS = frozenset(
    {
        "coaching_units_experiment_d",
        "coaching_units_experiment_e",
        "coaching_units_experiment_f",
        "coaching_units_experiment_g",
        "coaching_units_rating_sheet.csv",
        "coaching_units_rating_key.csv",
        "coaching_units_deferred",
        "assessment_layout_fix",
    }
)
NON_JUDGMENT_REFS = frozenset({"roster_evidence", "final30_score_gate"})
DEFERRED_RULES = (
    "resolves to a player-view source after analyze (2B)",
    "per-death sufficient map observability (2B)",
)


@dataclass
class Candidate:
    """One recording in the candidate pool."""

    video: str
    sources: list[str]
    language: str
    timestamp: str | None
    bytes: int | None
    expected_bytes: int | None = None
    reasons: list[str] = field(default_factory=list)
    other_references: list[str] = field(default_factory=list)


def recording_timestamp(name: str) -> str | None:
    """``YYYY-MM-DD HH-MM-SS`` from a file or folder name, else ``None``."""
    match = TIMESTAMP.search(name)
    if match is None:
        return None
    day, hh, mm, ss = match.groups()
    return f"{day} {hh}-{mm}-{ss}"


def language(name: str) -> str:
    """UI language from the file-name prefix only (GAME! tape convention)."""
    if re.match(r"^en[-_]", name):
        return "en"
    if re.match(r"^(ja|jp)[-_]", name):
        return "ja"
    return "unknown"


def referenced_timestamps(paths: Iterable[Path]) -> set[str]:
    """Every recording timestamp written in these files (recursively)."""
    found: set[str] = set()
    for path in paths:
        files = [path] if path.is_file() else sorted(path.rglob("*"))
        for item in files:
            if not item.is_file() or item.suffix.lower() not in TEXT_SUFFIXES:
                continue
            text = item.read_text(encoding="utf-8", errors="ignore")
            for match in TIMESTAMP.finditer(text):
                found.add(recording_timestamp(match.group(0)) or "")
    found.discard("")
    return found


def frozen_prior_use(report_dir: Path, tests_dir: Path) -> dict[str, set[str]]:
    """Recordings used by the studies the frozen rule excludes."""
    used = {
        name: referenced_timestamps([report_dir / folder])
        for name, folder in FROZEN_PRIOR_USE.items()
    }
    used["citation_contract_fixtures"] = referenced_timestamps(
        sorted(tests_dir.glob(CITATION_FIXTURE_GLOB))
    )
    return used


def other_references(report_dir: Path) -> dict[str, set[str]]:
    """Annotation only: other report entries naming a recording."""
    skip = set(FROZEN_PRIOR_USE.values()) | {OUT_DIR.name}
    return {
        entry.name: referenced_timestamps([entry])
        for entry in sorted(report_dir.iterdir())
        if entry.name not in skip
    }


def pool(heldout: Sequence[Mapping[str, Any]], movies: Path) -> list[Candidate]:
    """Union of the GAME! held-out list and live player-view recordings."""
    by_name: dict[str, Candidate] = {}
    for row in heldout:
        name = str(row["video"])
        by_name[name] = _candidate(name, movies, SOURCE_HELDOUT)
        by_name[name].expected_bytes = int(row["bytes"])
    live = [
        p.name
        for p in sorted(movies.iterdir())
        if LIVE_NAME.match(p.name) and p.suffix.lower() in VIDEO_SUFFIXES
    ]
    for name in live:
        if name in by_name:
            by_name[name].sources.append(SOURCE_LIVE)
        else:
            by_name[name] = _candidate(name, movies, SOURCE_LIVE)
    return list(by_name.values())


def _candidate(name: str, movies: Path, source: str) -> Candidate:
    path = movies / name
    size = path.stat().st_size if path.is_file() else None
    return Candidate(name, [source], language(name), recording_timestamp(name), size)


def apply_rules(
    candidates: Sequence[Candidate],
    used: Mapping[str, set[str]],
    others: Mapping[str, set[str]],
) -> None:
    """Recording-level rules that need no analysis; annotations on the side."""
    for c in candidates:
        if c.bytes is None:
            c.reasons.append("file_missing")
        elif c.expected_bytes is not None and c.bytes != c.expected_bytes:
            c.reasons.append("size_differs_from_heldout_list")
        if c.timestamp is None:
            c.reasons.append("no_parseable_timestamp")
        else:
            c.reasons += [f"used_in:{k}" for k, v in used.items() if c.timestamp in v]
            c.other_references = [k for k, v in others.items() if c.timestamp in v]
        if REVIEW_NAME.search(c.video):
            c.reasons.append("review_source_name")
        if c.language == "unknown":
            c.reasons.append("unknown_ui_language")


def ordered(candidates: Sequence[Candidate], movies: Path) -> list[Candidate]:
    """Included candidates by (timestamp, full source path)."""
    keep = [c for c in candidates if not c.reasons]
    return sorted(keep, key=lambda c: (c.timestamp or "", str(movies / c.video)))


def display_path(path: Path) -> str:
    """Path with the home directory written as ``~`` (no user name on disk)."""
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home) :] if text.startswith(home + "/") else text


def _record(c: Candidate, movies: Path) -> dict[str, Any]:
    return {
        "video": c.video,
        "path": display_path(movies / c.video),
        "sources": c.sources,
        "language": c.language,
        "timestamp": c.timestamp,
        "bytes": c.bytes,
        "other_prior_references": c.other_references,
    }


def build_census_order(
    heldout: Sequence[Mapping[str, Any]],
    movies: Path,
    report_dir: Path,
    tests_dir: Path,
    identity: Any = video_identity,
) -> dict[str, Any]:
    """The full ``census_order.json`` document (pure apart from file reads)."""
    candidates = pool(heldout, movies)
    used = frozen_prior_use(report_dir, tests_dir)
    others = other_references(report_dir)
    apply_rules(candidates, used, others)
    order = ordered(candidates, movies)
    recordings = []
    for rank, c in enumerate(order, start=1):
        logger.info("identity {}/{}: {}", rank, len(order), c.video)
        recordings.append(
            {
                "order": rank,
                **_record(c, movies),
                "video_identity": identity(movies / c.video),
            }
        )
    excluded = [
        {**_record(c, movies), "reasons": c.reasons}
        for c in sorted(candidates, key=lambda c: c.video)
        if c.reasons
    ]
    return {
        "experiment": "J",
        "stop_point": "2A",
        "design_sha256": _design_hashes(),
        "pool": {
            SOURCE_HELDOUT: str(HELDOUT.relative_to(PROJECT_ROOT)),
            SOURCE_LIVE: display_path(movies / "en-live-playerview-*"),
        },
        "order_rule": ORDER_RULE,
        "rules_applied": [
            "file present with the listed size",
            "parseable timestamp",
            "not used in Experiment H, Experiment I, coaching gap study, N2 "
            "citation experiment or citation-contract fixtures (timestamps "
            "referenced in their folders / tests/*citation*.py)",
            "no review / preview / timeline in the file name",
            "known UI language from the file-name prefix (en / ja / jp)",
            "public-site submissions: none in the pool (local recordings only)",
        ],
        "rules_deferred_to_2b": list(DEFERRED_RULES),
        "frozen_prior_use": {k: sorted(v) for k, v in used.items()},
        "counts": _counts(candidates, order),
        "recordings": recordings,
        "excluded": excluded,
    }


def _counts(
    candidates: Sequence[Candidate], order: Sequence[Candidate]
) -> dict[str, Any]:
    reasons: dict[str, int] = {}
    for c in candidates:
        for r in c.reasons:
            reasons[r] = reasons.get(r, 0) + 1
    return {
        "pool": len(candidates),
        "ordered": len(order),
        "excluded": len(candidates) - len(order),
        "exclusion_reasons": dict(sorted(reasons.items())),
        "ordered_by_source": _tally(order, lambda c: "+".join(c.sources)),
        "ordered_by_language": _tally(order, lambda c: c.language),
        "ordered_with_other_prior_references": sum(
            bool(c.other_references) for c in order
        ),
    }


def _tally(items: Iterable[Candidate], key: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        out[key(item)] = out.get(key(item), 0) + 1
    return dict(sorted(out.items()))


def _design_hashes() -> dict[str, str]:
    return {
        name: hashlib.sha256((OUT_DIR / name).read_bytes()).hexdigest()
        for name in ("DESIGN.md", "design.json")
        if (OUT_DIR / name).is_file()
    }


def prior_judgment_category(references: Iterable[str]) -> str | None:
    """Amendment 2: ``coaching_output``, ``death_importance`` or ``None``.

    Roster-detector and final-30 design references alone are not judgment
    of coaching output or death importance.
    """
    refs = set(references)
    if refs & COACHING_OUTPUT_REFS:
        return "coaching_output"
    if refs - NON_JUDGMENT_REFS:
        return "death_importance"
    return None


def build_census_exclusions(order_doc: Mapping[str, Any]) -> dict[str, Any]:
    """Amendment 2 exclusions and the resulting 2B sequence (order unchanged)."""
    excluded, sequence = [], []
    for rec in order_doc["recordings"]:
        category = prior_judgment_category(rec["other_prior_references"])
        brief = {k: rec[k] for k in ("order", "video", "video_identity")}
        if category is None:
            sequence.append(brief)
        else:
            refs = rec["other_prior_references"]
            excluded.append({**brief, "category": category, "prior_references": refs})
    return {
        "experiment": "J",
        "amendment": 2,
        "census_order_sha256": hashlib.sha256(
            json.dumps(order_doc, indent=1, ensure_ascii=False).encode("utf-8") + b"\n"
        ).hexdigest(),
        "excluded": excluded,
        "counts": {
            "excluded": len(excluded),
            "coaching_output": sum(e["category"] == "coaching_output" for e in excluded),
            "death_importance": sum(
                e["category"] == "death_importance" for e in excluded
            ),
            "sequence_2b": len(sequence),
        },
        "sequence_2b": sequence,
    }


def write_census_order(doc: Mapping[str, Any], out: Path = CENSUS_ORDER) -> None:
    """Write once; an existing order file is frozen."""
    if out.exists():
        raise SystemExit(f"{out} exists; the census order is frozen")
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    census = sub.add_parser("census-order", help="stop point 2A: write census_order.json")
    census.add_argument("--movies", type=Path, default=MOVIES)
    sub.add_parser("census-exclusions", help="amendment 2: write census_exclusions.json")
    args = parser.parse_args(argv)
    if args.command == "census-exclusions":
        order_doc = json.loads(CENSUS_ORDER.read_text(encoding="utf-8"))
        doc = build_census_exclusions(order_doc)
        write_census_order(doc, CENSUS_EXCLUSIONS)
        sys.stdout.write(f"{json.dumps(doc['counts'])} -> {CENSUS_EXCLUSIONS}\n")
    if args.command == "census-order":
        if CENSUS_ORDER.exists():
            raise SystemExit(f"{CENSUS_ORDER} exists; the census order is frozen")
        heldout = json.loads(HELDOUT.read_text(encoding="utf-8"))
        doc = build_census_order(heldout, args.movies, REPORT_DIR, TESTS_DIR)
        write_census_order(doc)
        ordered_n, excluded_n = doc["counts"]["ordered"], doc["counts"]["excluded"]
        sys.stdout.write(
            f"{ordered_n} ordered, {excluded_n} excluded -> {CENSUS_ORDER}\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
