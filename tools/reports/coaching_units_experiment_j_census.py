"""Experiment J stop point 2B: deterministic census and relevance worksheet.

Research only; no model calls. ``run`` walks the Amendment 2 sequence
(``census_exclusions.json``) in sealed order, runs ``s3-coach analyze`` on
each recording and derives each death's map-awareness facts with Experiment
I's frozen arithmetic. It stops after the recording that holds the 50th
mechanically eligible death has been processed completely. ``select`` then
fixes the populations and applies the frozen seeds: the first 50 eligible
deaths, the not-eligible frame, the 50-death sample, the P3 draw, and the
shuffled worksheet with its separate key.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import coaching_units_experiment_i as xi
import coaching_units_experiment_j as xj
from importance_report import _score
from loguru import logger

from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT

CENSUS_DIR = PROJECT_ROOT / "analysis" / "experiment_j_census"
CENSUS = xj.OUT_DIR / "census.json"
WORKSHEET = xj.OUT_DIR / "relevance_worksheet.csv"
WORKSHEET_KEY = xj.OUT_DIR / "relevance_worksheet_key.json"
DONE_MARKER = "census_done.json"
TARGET_ELIGIBLE = 50
SAMPLE_SIZE = 50
P3_SIZE = 20
C_MENU = ("death_low_ink", "death_own_splat_before")
SEED_KEYS = {
    "sample": "not_eligible_worksheet_sample",
    "shuffle": "worksheet_row_shuffle",
    "p3": "p3_draw",
}
WORKSHEET_COLUMNS = (
    "row_id",
    "recording",
    "death_time",
    "death_seconds",
    "footage",
    "map_relevant",
    "ideal_coaching_note",
)

Analyzer = Callable[[Path, Path, str], tuple[bool, str | None]]


def seeds() -> dict[str, int]:
    """The frozen seeds, read from the sealed ``design.json``."""
    design = json.loads((xj.OUT_DIR / "design.json").read_text(encoding="utf-8"))
    return {k: int(design["seeds"][v]) for k, v in SEED_KEYS.items()}


def ui_language(video: str) -> str:
    """``--language`` for analyze: ja for ja/jp prefixes, else en."""
    return "ja" if xj.language(video) == "ja" else "en"


def analysis_dir(rec: Mapping[str, Any], root: Path = CENSUS_DIR) -> Path:
    """Per-recording analysis output folder."""
    return root / f"{int(rec['order']):03d} {Path(str(rec['video'])).stem}"


def run_analyze(video: Path, out: Path, language: str) -> tuple[bool, str | None]:
    """``s3-coach analyze`` (the production CLI); log kept beside the output."""
    out.mkdir(parents=True, exist_ok=True)
    s3 = Path(sys.executable).with_name("s3-coach")
    argv = [str(s3), "analyze", str(video), "--out", str(out), "--language", language]
    argv += ["--config", str(default_config_path())]
    with (out / "analyze.log").open("w", encoding="utf-8") as log:
        code = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, check=False)
    return (
        (True, None)
        if code.returncode == 0
        else (False, f"analyze_exit_{code.returncode}")
    )


def c_point(claim_ids: set[str]) -> str | None:
    """First C-menu claim in the death's production claim basis."""
    return next((c for c in C_MENU if c in claim_ids), None)


def death_records(
    analysis: Path, rec: Mapping[str, Any], config: AppConfig
) -> list[dict]:
    """Mechanical facts for every death in one analyzed recording."""
    _, scoring = _score(analysis, config)
    thresholds = config.coach.death_factor_thresholds
    out = []
    for unit in scoring.units:
        cid = unit.candidate_id
        view = build_coach_llm_view(scoring.coach_inputs[cid], unit, thresholds)
        view_json = view.model_dump(mode="json")
        block = (view_json.get("map") or {}).get("map_check_before_death") or {}
        observable = xi.map_observable(view_json)
        try:
            facts = xi.facts_for(analysis, cid)
        except StopIteration:
            facts, observable = {"error": "no_in_match_snapshot"}, False
        claims = {str(e.get("claim_id")) for e in view_json.get("claim_basis") or []}
        out.append(
            {
                "order": int(rec["order"]),
                "video": rec["video"],
                "cid": cid,
                "death_time": xi.death_time(analysis, cid),
                **facts,
                "map_check_status": block.get("status"),
                "coverage_episode": (block.get("coverage") or {}).get("episode"),
                "map_observable": observable,
                "eligible": bool(observable and facts.get("eligible")),
                "c_point": c_point(claims),
            }
        )
    return sorted(out, key=lambda d: d["death_time"])


def _manifest_video(analysis: Path) -> tuple[str | None, str | None]:
    manifest = json.loads((analysis / "vision_manifest.json").read_text(encoding="utf-8"))
    return (manifest.get("video") or {}).get("source"), manifest.get("video_identity")


def process_recording(
    rec: Mapping[str, Any], movies: Path, root: Path, config: AppConfig, analyze: Analyzer
) -> tuple[dict, list[dict]]:
    """Analyze (or reuse a completed analysis of) one recording."""
    out = analysis_dir(rec, root)
    status = {"order": rec["order"], "video": rec["video"], "analysis_dir": out.name}
    if not (out / DONE_MARKER).is_file():
        ok, reason = analyze(
            movies / str(rec["video"]), out, ui_language(str(rec["video"]))
        )
        if not ok:
            return {**status, "status": "skipped", "reason": reason}, []
        (out / DONE_MARKER).write_text(json.dumps({"ok": True}) + "\n", encoding="utf-8")
    source, identity = _manifest_video(out)
    if identity != rec["video_identity"]:
        return {**status, "status": "skipped", "reason": "video_identity_mismatch"}, []
    if source == "review":
        return {
            **status,
            "status": "skipped",
            "reason": "review_source_after_analyze",
        }, []
    deaths = death_records(out, rec, config)
    eligible = sum(d["eligible"] for d in deaths)
    observed = sum(d["map_observable"] for d in deaths)
    summary = {"source": source, "deaths": len(deaths), "observable": observed}
    return {**status, "status": "analyzed", **summary, "eligible": eligible}, deaths


def walk(
    sequence: Sequence[Mapping[str, Any]],
    movies: Path,
    root: Path,
    config: AppConfig,
    analyze: Analyzer = run_analyze,
    target: int = TARGET_ELIGIBLE,
) -> dict[str, Any]:
    """Walk the sequence until the recording holding the ``target``-th eligible
    death is complete; never start a later recording."""
    recordings, deaths, eligible = [], [], 0
    for rec in sequence:
        logger.info(
            "census {} (order {}), eligible so far {}",
            rec["video"],
            rec["order"],
            eligible,
        )
        status, found = process_recording(rec, movies, root, config, analyze)
        recordings.append(status)
        deaths += found
        eligible += sum(d["eligible"] for d in found)
        if eligible >= target:
            break
    return {
        "experiment": "J",
        "stop_point": "2B",
        "target_eligible": target,
        "reached_target": eligible >= target,
        "terminal": recordings[-1] if recordings and eligible >= target else None,
        "recordings": recordings,
        "deaths": deaths,
    }


def death_key(d: Mapping[str, Any]) -> tuple[int, float]:
    """Frozen death order: (recording order, death video time)."""
    return int(d["order"]), float(d["death_time"])


def death_id(d: Mapping[str, Any]) -> str:
    """Stable id for one death."""
    return f"{int(d['order']):03d}|{d['cid']}"


def select(census: Mapping[str, Any], frozen_seeds: Mapping[str, int]) -> dict[str, Any]:
    """Populations and seeded draws, applied only after the frames are fixed."""
    deaths = sorted(census["deaths"], key=death_key)
    eligible = [d for d in deaths if d["eligible"]]
    first50 = eligible[:TARGET_ELIGIBLE]
    frame = [d for d in deaths if d["map_observable"] and not d["eligible"]]
    sample = (
        random.Random(frozen_seeds["sample"]).sample(frame, SAMPLE_SIZE)
        if len(frame) >= SAMPLE_SIZE
        else list(frame)
    )
    sample = sorted(sample, key=death_key)
    p3 = (
        random.Random(frozen_seeds["p3"]).sample(sample, P3_SIZE)
        if len(sample) >= P3_SIZE
        else list(sample)
    )
    rows = sorted([*first50, *sample], key=death_key)
    random.Random(frozen_seeds["shuffle"]).shuffle(rows)
    p3_ids = {death_id(d) for d in p3}
    first_ids = {death_id(d) for d in first50}
    key = []
    for n, d in enumerate(rows, start=1):
        group = "eligible_first50" if death_id(d) in first_ids else "not_eligible_sample"
        key.append(
            {"row_id": f"r{n:03d}", "group": group, "in_p3": death_id(d) in p3_ids, **d}
        )
    return {
        "seeds": dict(frozen_seeds),
        "first50": [death_id(d) for d in first50],
        "eligible_after_first50": [death_id(d) for d in eligible[TARGET_ELIGIBLE:]],
        "frame": [death_id(d) for d in frame],
        "sample": [death_id(d) for d in sample],
        "p3": sorted(p3_ids),
        "rows": key,
    }


def _clock(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def worksheet_rows(key_rows: Sequence[Mapping[str, Any]], movies: Path) -> list[dict]:
    """Blind rows: opaque id, recording, death time, footage only."""
    return [
        {
            "row_id": r["row_id"],
            "recording": r["video"],
            "death_time": _clock(float(r["death_time"])),
            "death_seconds": f"{float(r['death_time']):.1f}",
            "footage": xj.display_path(movies / str(r["video"])),
            "map_relevant": "",
            "ideal_coaching_note": "",
        }
        for r in key_rows
    ]


def write_once(path: Path, text: str) -> None:
    """Write a sealed artifact once."""
    if path.exists():
        raise SystemExit(f"{path} exists; it is sealed")
    path.write_text(text, encoding="utf-8")


def write_worksheet(rows: Sequence[Mapping[str, Any]], path: Path = WORKSHEET) -> None:
    """The blind relevance worksheet (CSV)."""
    if path.exists():
        raise SystemExit(f"{path} exists; it is sealed")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=WORKSHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode("utf-8")).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="walk the sequence and write census.json")
    sub.add_parser("select", help="write the worksheet and its key from census.json")
    args = parser.parse_args(argv)
    if args.command == "run":
        if CENSUS.exists():
            raise SystemExit(f"{CENSUS} exists; it is sealed")
        sequence = json.loads(xj.CENSUS_EXCLUSIONS.read_text(encoding="utf-8"))[
            "sequence_2b"
        ]
        config = load_config(default_config_path())
        config.coach.roster_evidence = False
        census = walk(sequence, xj.MOVIES, CENSUS_DIR, config)
        write_once(CENSUS, json.dumps(census, indent=1, ensure_ascii=False) + "\n")
    else:
        census = json.loads(CENSUS.read_text(encoding="utf-8"))
        chosen = select(census, seeds())
        again = select(census, seeds())
        if _sha(chosen) != _sha(again):
            raise SystemExit("selection is not deterministic")
        write_worksheet(worksheet_rows(chosen["rows"], xj.MOVIES))
        write_once(WORKSHEET_KEY, json.dumps(chosen, indent=1, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
