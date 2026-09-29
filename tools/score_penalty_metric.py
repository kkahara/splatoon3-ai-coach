#!/usr/bin/env python3
"""Stage 0 joint Splat Zones score + penalty consistency metric.

Replays production fusion (``ScoreFrameFuser``) over raw score readings and
checks each side against domain expectations:

- a main-counter decrease is expected only when the penalty is known absent
- a penalty decrease is expected only while that side's main counter holds
- a penalty is expected to appear/grow only while the main counter holds
- both counters step down about one per tick; the main counter never rises

Every irregularity is classed ``confirmed_violation`` (both counters observed
across the step and an expectation breaks), ``insufficient_evidence`` (held /
unknown / not_shown / rejected on either frame) or ``detector_disagreement``
(the two counters tell different stories; neither is blamed). ``not_shown``
counts as zero only inside the ``work_remaining`` check, never elsewhere.

Subcommands:

- ``extract``: decode in-match frames at 2 FPS from library Splat Zones
  videos → raw ``ScoreReading`` JSONL (use when stored manifests predate the
  penalty regions)
- ``report``: fuse (plausibility off = Stage 3.1, on = Stage 3.2) and write
  ``metrics.json`` + ``REPORT.md``. ``--manifest`` reads stored
  ``frame_results`` instead of JSONL.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
from loguru import logger

from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import ScoreDetectorConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT, default_config_path
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    ScoreReading,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.score import ScoreDetector
from splatoon3_ai_coach.vision.score_fusion import (
    ScoreFrameFuser,
    retract_contradicted_scores,
)

LIBRARY = PROJECT_ROOT / "analysis" / "_rescore_v2"
OUT_DIR = PROJECT_ROOT / "analysis" / "score_survey" / "penalty_metric"
READING_GT_METRICS = PROJECT_ROOT / "analysis" / "score_survey" / "metrics.json"
MAX_TICK_RATE = 2.0
CLASSES = (
    "consistent",
    "confirmed_violation",
    "insufficient_evidence",
    "detector_disagreement",
)


@dataclass(frozen=True)
class SideState:
    """One side's fused main counter + penalty at one frame."""

    main: int | None
    main_q: str
    pen: int | None
    pen_q: str

    @property
    def main_observed(self) -> bool:
        return self.main_q == "observed" and self.main is not None

    @property
    def pen_known(self) -> bool:
        return self.pen_q in ("observed", "not_shown")

    @property
    def pen_value(self) -> int:
        """Penalty for the ``work_remaining`` check only (not_shown → 0)."""
        return int(self.pen) if self.pen_q == "observed" and self.pen is not None else 0


def _library_runs(movies: Path) -> list[dict[str, Any]]:
    """Splat Zones library analyses whose source video is in ``movies``."""
    runs: list[dict[str, Any]] = []
    for analysis in sorted(p for p in LIBRARY.iterdir() if p.is_dir()):
        identity = analysis / "match_identity.json"
        if not identity.is_file():
            continue
        if json.loads(identity.read_text()).get("battle_mode_id") != "splat_zones":
            continue
        videos = [movies / f"{analysis.name}{ext}" for ext in (".mov", ".mp4")]
        video = next((v for v in videos if v.is_file()), None)
        if video is None:
            logger.warning("no source video for {}", analysis.name)
            continue
        runs.append({"run": analysis.name, "analysis": analysis, "video": video})
    return runs


def _in_match_bounds(manifest: dict[str, Any]) -> tuple[float, float]:
    """``(t0, t1)`` of in-match snapshots (manifest phase only)."""
    times = [
        float(s["timestamp"])
        for s in manifest.get("state_snapshots", [])
        if s.get("match_phase") == "in_match"
    ]
    if not times:
        raise RuntimeError("no in_match snapshots")
    return min(times), max(times)


def _extract_one(job: tuple[str, str, str, str, float]) -> str:
    """Decode one video's in-match window → JSONL of raw score readings."""
    run, analysis, video, config_path, step = job
    logger.remove()
    config = load_config(Path(config_path))
    detector = ScoreDetector(config.vision.score)
    manifest = json.loads((Path(analysis) / "vision_manifest.json").read_text())
    t0, t1 = _in_match_bounds(manifest)
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    every = max(1, round(fps * step))
    rows: list[str] = []
    index = 0
    while cap.grab():
        t = index / fps
        index += 1
        if t < t0 or (index - 1) % every:
            continue
        if t > t1:
            break
        ok, frame = cap.retrieve()
        if not ok:
            continue
        reading, conf = detector.detect(frame)
        payload = reading.model_dump() if reading is not None else None
        rows.append(
            json.dumps({"t": round(t, 3), "confidence": conf, "reading": payload})
        )
    cap.release()
    out = OUT_DIR / "readings" / f"{run}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return f"{run}: {len(rows)} frames"


def cmd_extract(args: argparse.Namespace) -> int:
    """Decode library Splat Zones videos in parallel."""
    runs = _library_runs(Path(args.movies_dir))
    jobs = [
        (r["run"], str(r["analysis"]), str(r["video"]), str(args.config), args.step)
        for r in runs
    ]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for line in pool.map(_extract_one, jobs):
            logger.info(line)
    return 0


def _frames_from_jsonl(path: Path) -> list[VisionFrameResult]:
    """Rebuild minimal frame results (one score detection each) from JSONL."""
    frames: list[VisionFrameResult] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        row = json.loads(line)
        detections: list[DetectorResult] = []
        if row.get("reading"):
            detections.append(
                DetectorResult(
                    id=f"score:{i}",
                    detector_name="score",
                    detector_version="score@metric",
                    confidence=float(row["confidence"]),
                    reading=ScoreReading.model_validate(row["reading"]),
                )
            )
        frames.append(
            VisionFrameResult(
                frame_id=f"frame:{i}",
                timestamp=float(row["t"]),
                source="cadence",
                detections=detections,
            )
        )
    return frames


def _frames_from_manifest(path: Path) -> list[VisionFrameResult]:
    """In-match stored frame results from a vision manifest."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    in_match = {
        float(s["timestamp"])
        for s in raw.get("state_snapshots", [])
        if s.get("match_phase") == "in_match"
    }
    return [
        VisionFrameResult.model_validate(f)
        for f in raw.get("frame_results", [])
        if float(f["timestamp"]) in in_match
    ]


def fuse_sides(
    frames: list[VisionFrameResult], config: ScoreDetectorConfig
) -> list[tuple[float, SideState, SideState]]:
    """Run production fusion over in-match frames (mode gate satisfied)."""
    fuser = ScoreFrameFuser(config, config.battle_mode_id)
    ordered = sorted(frames, key=lambda f: f.timestamp)
    fusions = [fuser.step(frame, "in_match") for frame in ordered]
    if fuser.plausibility.enabled:
        fusions = retract_contradicted_scores(
            fusions, ["in_match"] * len(fusions), config.score_drop_slack
        )
    out: list[tuple[float, SideState, SideState]] = []
    for frame, fused in zip(ordered, fusions, strict=True):
        ally = SideState(
            fused.ally_remaining,
            fused.ally_score_quality,
            fused.ally_penalty,
            fused.ally_penalty_quality,
        )
        opponent = SideState(
            fused.opponent_remaining,
            fused.opponent_score_quality,
            fused.opponent_penalty,
            fused.opponent_penalty_quality,
        )
        out.append((frame.timestamp, ally, opponent))
    return out


def _main_rules(prev: SideState, cur: SideState, max_step: int) -> list[tuple[str, str]]:
    """Main-counter expectations for one step."""
    if not (prev.main_observed and cur.main_observed):
        return []
    assert prev.main is not None and cur.main is not None
    results: list[tuple[str, str]] = []
    if cur.main > prev.main:
        results.append(("main_rise", "confirmed_violation"))
    elif prev.main - cur.main > max_step:
        results.append(("main_jump", "confirmed_violation"))
    if cur.main < prev.main:
        results.append(
            ("main_tick_needs_penalty_absent", _tick_vs_penalty(prev, cur, max_step))
        )
    return results


def _tick_vs_penalty(prev: SideState, cur: SideState, max_step: int) -> str:
    """A main tick is expected only when the penalty is known absent."""
    if prev.pen_q == "observed" and cur.pen_q == "observed":
        return "detector_disagreement"
    if prev.pen_q == "not_shown" and cur.pen_q == "not_shown":
        return "consistent"
    if prev.pen_q == "observed" and cur.pen_q == "not_shown":
        cleared = prev.pen is not None and prev.pen <= max_step
        return "consistent" if cleared else "insufficient_evidence"
    return "insufficient_evidence"


def _penalty_rules(
    prev: SideState, cur: SideState, max_step: int
) -> list[tuple[str, str]]:
    """Penalty expectations for one step."""
    main_known = prev.main_observed and cur.main_observed
    main_changed = main_known and cur.main != prev.main
    results: list[tuple[str, str]] = []
    both_seen = prev.pen_q == "observed" and cur.pen_q == "observed"
    if both_seen and cur.pen is not None and prev.pen is not None and cur.pen < prev.pen:
        if prev.pen - cur.pen > max_step:
            results.append(("penalty_jump", "confirmed_violation"))
        elif not main_known:
            results.append(("penalty_tick_needs_main_hold", "insufficient_evidence"))
        else:
            cls = "detector_disagreement" if main_changed else "consistent"
            results.append(("penalty_tick_needs_main_hold", cls))
    grew = cur.pen_q == "observed" and (
        prev.pen_q == "not_shown"
        or (
            both_seen
            and cur.pen is not None
            and prev.pen is not None
            and cur.pen > prev.pen
        )
    )
    if grew:
        if not main_known:
            results.append(("penalty_appears_needs_main_hold", "insufficient_evidence"))
        else:
            ticked = (
                cur.main is not None and prev.main is not None and cur.main < prev.main
            )
            cls = "detector_disagreement" if ticked else "consistent"
            results.append(("penalty_appears_needs_main_hold", cls))
    return results


def _work_rule(prev: SideState, cur: SideState) -> list[tuple[str, str]]:
    """``work_remaining = remaining + penalty`` may only rise when +N appears."""
    if not (
        prev.main_observed and cur.main_observed and prev.pen_known and cur.pen_known
    ):
        return []
    assert prev.main is not None and cur.main is not None
    work_prev = prev.main + prev.pen_value
    work_cur = cur.main + cur.pen_value
    if work_cur <= work_prev or cur.pen_value > prev.pen_value:
        return [("work_remaining_no_unexplained_rise", "consistent")]
    same_penalty_state = prev.pen_q == cur.pen_q
    cls = "confirmed_violation" if same_penalty_state else "insufficient_evidence"
    return [("work_remaining_no_unexplained_rise", cls)]


def classify_step(prev: SideState, cur: SideState, gap: float) -> list[tuple[str, str]]:
    """All (rule, class) results for one side across one step."""
    max_step = 1 + math.ceil(MAX_TICK_RATE * max(gap, 0.0))
    return [
        *_main_rules(prev, cur, max_step),
        *_penalty_rules(prev, cur, max_step),
        *_work_rule(prev, cur),
    ]


def evaluate(series: list[tuple[float, SideState, SideState]]) -> dict[str, Any]:
    """Rule/class tallies, coverage and examples for one fused series."""
    tallies: dict[str, Counter[str]] = {}
    coverage = {"main": Counter[str](), "penalty": Counter[str]()}
    examples: list[dict[str, Any]] = []
    for i, (t, ally, opponent) in enumerate(series):
        for state in (ally, opponent):
            coverage["main"][state.main_q] += 1
            coverage["penalty"][state.pen_q] += 1
        if i == 0:
            continue
        t_prev, ally_prev, opp_prev = series[i - 1]
        for side, prev, cur in (
            ("ally", ally_prev, ally),
            ("opponent", opp_prev, opponent),
        ):
            for rule, cls in classify_step(prev, cur, t - t_prev):
                tallies.setdefault(rule, Counter())[cls] += 1
                if cls != "consistent" and len(examples) < 400:
                    examples.append(
                        {
                            "t": t,
                            "side": side,
                            "rule": rule,
                            "class": cls,
                            "prev": prev.__dict__,
                            "cur": cur.__dict__,
                        }
                    )
    return {
        "frames": len(series),
        "tallies": {rule: dict(c) for rule, c in sorted(tallies.items())},
        "coverage": {k: dict(v) for k, v in coverage.items()},
        "examples": examples,
    }


def _merge(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum tallies and coverage across runs."""
    tallies: dict[str, Counter[str]] = {}
    coverage = {"main": Counter[str](), "penalty": Counter[str]()}
    frames = 0
    for r in results:
        frames += r["frames"]
        for rule, counts in r["tallies"].items():
            tallies.setdefault(rule, Counter()).update(counts)
        for k in coverage:
            coverage[k].update(r["coverage"][k])
    return {
        "frames": frames,
        "tallies": {rule: dict(c) for rule, c in sorted(tallies.items())},
        "coverage": {k: dict(v) for k, v in coverage.items()},
    }


def _sources(args: argparse.Namespace) -> dict[str, list[VisionFrameResult]]:
    """Per-run frames from manifests (``--manifest``) or extracted JSONL."""
    if args.manifest:
        return {
            Path(m).parent.name: _frames_from_manifest(Path(m)) for m in args.manifest
        }
    paths = sorted((OUT_DIR / "readings").glob("*.jsonl"))
    return {p.stem: _frames_from_jsonl(p) for p in paths}


def cmd_report(args: argparse.Namespace) -> int:
    """Fuse with plausibility off/on, evaluate, write metrics + report."""
    base = load_config(Path(args.config)).vision.score
    variants = {
        "stage_3_1_no_plausibility": base.model_copy(
            update={"score_plausibility_enabled": False}
        ),
        "stage_3_2_plausibility": base.model_copy(
            update={"score_plausibility_enabled": True}
        ),
    }
    sources = _sources(args)
    if not sources:
        logger.error("no readings; run extract first (or pass --manifest)")
        return 1
    payload: dict[str, Any] = {"runs": sorted(sources), "variants": {}}
    for name, cfg in variants.items():
        per_run = {
            run: evaluate(fuse_sides(frames, cfg)) for run, frames in sources.items()
        }
        payload["variants"][name] = {
            "total": _merge(list(per_run.values())),
            "per_run": {
                run: {k: v for k, v in r.items() if k != "examples"}
                for run, r in per_run.items()
            },
            "examples": {run: r["examples"][:40] for run, r in per_run.items()},
        }
    if READING_GT_METRICS.is_file():
        gt = json.loads(READING_GT_METRICS.read_text(encoding="utf-8"))
        payload["reading_gt"] = {k: v for k, v in gt.items() if k != "failures"}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    (out / "REPORT.md").write_text(render_report(payload), encoding="utf-8")
    logger.info("wrote {}", out / "REPORT.md")
    return 0


def _class_table(total: dict[str, Any]) -> list[str]:
    """Markdown table of rule × class counts."""
    lines = [
        "| Rule | " + " | ".join(CLASSES) + " |",
        "|------|" + "|".join("---:" for _ in CLASSES) + "|",
    ]
    for rule, counts in total["tallies"].items():
        cells = " | ".join(str(counts.get(c, 0)) for c in CLASSES)
        lines.append(f"| `{rule}` | {cells} |")
    return lines


def _coverage_lines(total: dict[str, Any]) -> list[str]:
    """Side-frame quality shares for main counter and penalty."""
    lines: list[str] = []
    for key in ("main", "penalty"):
        counts = total["coverage"][key]
        n = sum(counts.values()) or 1
        parts = ", ".join(f"{q} {c} ({c / n:.1%})" for q, c in sorted(counts.items()))
        lines.append(f"- {key} side-frames: {parts}")
    return lines


def _headline(total: dict[str, Any]) -> tuple[int, int]:
    """(confirmed violations overall, confirmed work_remaining rises)."""
    confirmed = sum(c.get("confirmed_violation", 0) for c in total["tallies"].values())
    work = total["tallies"].get("work_remaining_no_unexplained_rise", {})
    return confirmed, int(work.get("confirmed_violation", 0))


def _reading_gt_lines(gt: dict[str, Any] | None) -> list[str]:
    """Per-frame reading accuracy summary from score_reading_study."""
    if not gt:
        return [
            "- reading GT metrics missing (run `tools/score_reading_study.py compare`)"
        ]
    pen = gt.get("penalty", {})
    return [
        f"- frames: {gt.get('n_frames')}; main both-sides exact: "
        f"{gt.get('both_sides_exact_accuracy', 0):.3f}",
        f"- penalty sides: {pen.get('sides')}; exact {pen.get('exact')}; "
        f"present exact {pen.get('present_exact')}/{pen.get('present_gt')}; "
        f"absent correct {pen.get('absent_ok')}/{pen.get('absent_gt')}; "
        f"missed {pen.get('missed')}; false positive {pen.get('false_positive')}; "
        f"wrong value {pen.get('wrong_value')}",
    ]


def render_report(payload: dict[str, Any]) -> str:
    """Markdown report for the joint metric."""
    lines = [
        "# Splat Zones score + penalty joint metric",
        "",
        "Generated by `tools/score_penalty_metric.py report`. Expectations are domain",
        "expectations, not hard invariants; see the module docstring for classes.",
        "",
        f"Runs ({len(payload['runs'])}): " + ", ".join(f"`{r}`" for r in payload["runs"]),
        "",
        "## Reading accuracy against ground truth",
        "",
        *_reading_gt_lines(payload.get("reading_gt")),
        "",
    ]
    for name, variant in payload["variants"].items():
        total = variant["total"]
        confirmed, work = _headline(total)
        lines += [
            f"## Fusion: `{name}`",
            "",
            f"**Headline:** {work} confirmed `work_remaining` rises; "
            f"{confirmed} confirmed violations overall across {total['frames']} frames.",
            "",
            *_class_table(total),
            "",
            "Coverage:",
            "",
            *_coverage_lines(total),
            "",
        ]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    """CLI parser with extract / report subcommands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=default_config_path())
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_ext = sub.add_parser("extract", help="Decode library SZ videos → JSONL")
    p_ext.add_argument("--movies-dir", type=Path, default=Path.home() / "Movies")
    p_ext.add_argument("--step", type=float, default=0.5)
    p_ext.add_argument("--workers", type=int, default=4)
    p_ext.set_defaults(func=cmd_extract)
    p_rep = sub.add_parser("report", help="Fuse + evaluate → metrics.json, REPORT.md")
    p_rep.add_argument("--manifest", type=Path, action="append", default=None)
    p_rep.add_argument("--out", type=Path, default=OUT_DIR)
    p_rep.set_defaults(func=cmd_report)
    return parser


def main() -> int:
    """Entry point."""
    logger.remove()
    logger.add(lambda m: print(m, end=""), level="INFO")
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
