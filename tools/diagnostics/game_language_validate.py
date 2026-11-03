"""Characterize and validate the pre-scan game-language probe on real recordings.

Subcommands (run from the repo root with ``.venv/bin/python``):

- ``characterize``: score both gates on every sampled frame of the first
  ``--seconds`` of each recording (the probe does not stop), with the timer
  phase and, when a prior analysis exists, the local player lifecycle. Used to
  choose ROI, thresholds, margins and fallback bounds.
- ``probe``: run the production probe per recording in ``normal`` and/or
  ``forced-fallback`` (Gate 1 disabled) mode and report Gate 1 and Gate 2
  separately against weak labels.

Weak labels: filename prefix (``en``/``ja``/``jp``/``fr``) and, as a secondary
check, a prior ``analysis/<stem>/vision_manifest.json`` ``analysis.language``.
Corpus exclusions (sealed F11 Arm R, the seven-recording corpus, pilot pools,
non-gameplay Review/preview captures) are applied by :func:`build_corpus`.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from loguru import logger

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.extraction.sampler import FrameSampler
from splatoon3_ai_coach.media.video import VideoLoader
from splatoon3_ai_coach.vision.game_language import (
    GameLanguageProbe,
    probe_game_language,
)
from splatoon3_ai_coach.vision.models import GameLanguageCueReading

REPO_ROOT = Path(__file__).resolve().parents[2]
VIDEO_SUFFIXES = {".mov", ".mp4", ".mkv", ".m4v"}
# Sealed / reserved recordings, matched on the timestamp token in the filename.
EXCLUDED_TOKENS = (
    # F11 Arm R six.
    "2026-09-23 21-13-04",
    "2026-10-12 20-17-32",
    "2026-09-20 20-02-59",
    "2026-09-16 19-49-36",
    "2026-09-16 19-19-16",
    "2026-09-16 19-12-39",
    # Seven-recording corpus.
    "2026-10-11 18-18-00",
    "2026-10-11 18-25-16",
    "2026-10-11 20-33-31",
    "2026-10-11 22-20-42",
    "2026-10-11 22-31-53",
    "2026-10-11 22-37-47",
    "2026-10-11 22-57-30",
    # Alignment pilot pools / pilot sources.
    "2026-10-24",
)
NON_GAMEPLAY_WORDS = ("review", "preview", "timeline", "topdown")
PREFIX_LABELS = {"en": "en", "ja": "ja", "jp": "ja", "fr": "fr"}
# Earlier runs of this recording were recorded as ja; never trust its prefix.
CONFLICTED_LABELS = {"en-2026-07-05 17-34-46"}
# Filename prefix contradicted by the on-screen UI, checked by eye on the intro
# label and the D-pad signal label (2026-11-02 characterization).
VISUAL_LABEL_OVERRIDES = {
    "ja_turf--2026-10-21 20-19-13": "en",
    "en-2026-09-20 16-20-26": "ja",
}


def build_corpus(videos_dir: Path) -> list[dict[str, Any]]:
    """Top-level gameplay recordings with weak labels, exclusions applied."""
    corpus: list[dict[str, Any]] = []
    for path in sorted(videos_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        stem = path.stem
        lower = stem.lower()
        if any(token in stem for token in EXCLUDED_TOKENS):
            continue
        if any(word in lower for word in NON_GAMEPLAY_WORDS):
            continue
        corpus.append(_labelled(path))
    return corpus


def _labelled(path: Path) -> dict[str, Any]:
    """Attach prefix and prior-manifest weak labels to one recording."""
    stem = path.stem
    match = re.match(r"^([a-z]{2})[-_]", stem.lower())
    prefix = PREFIX_LABELS.get(match.group(1)) if match else None
    manifest = _prior_manifest_language(stem)
    label = prefix
    flag = None
    if stem in VISUAL_LABEL_OVERRIDES:
        label, flag = VISUAL_LABEL_OVERRIDES[stem], "visual_override_of_prefix"
    elif stem in CONFLICTED_LABELS:
        label, flag = None, "conflicted_prior_runs"
    elif prefix is None:
        label, flag = manifest, "manifest_only" if manifest else "unlabeled"
    elif manifest is not None and manifest != prefix:
        flag = "prefix_manifest_disagree"
    return {
        "video": str(path),
        "stem": stem,
        "label": label,
        "prefix_label": prefix,
        "manifest_label": manifest,
        "label_flag": flag,
    }


def _prior_manifest_language(stem: str) -> str | None:
    """``analysis.language`` of a prior analysis of this recording, if any."""
    path = REPO_ROOT / "analysis" / stem / "vision_manifest.json"
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            head = handle.read(4096)
        found = re.search(r'"language":\s*"([a-z]+)"', head)
        if found:
            return found.group(1)
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("analysis", {}).get("language")
    except (OSError, ValueError):
        return None


def _scores(reading: GameLanguageCueReading) -> dict[str, float | None]:
    """Per-language best scores from one both-language reading."""
    out: dict[str, float | None] = {"en": None, "ja": None}
    if reading.candidate_language is not None:
        out[reading.candidate_language] = reading.score
    if reading.runner_up_language is not None:
        out[reading.runner_up_language] = reading.runner_up_score
    return out


def _lifecycle_lookup(stem: str) -> list[tuple[float, str]]:
    """``(timestamp, player_lifecycle)`` from a prior analysis, if present."""
    path = REPO_ROOT / "analysis" / stem / "vision_manifest.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [
        (float(s["timestamp"]), str(s.get("player_lifecycle")))
        for s in data.get("state_snapshots", [])
    ]


def _lifecycle_at(table: list[tuple[float, str]], t: float) -> str | None:
    """Nearest prior-analysis lifecycle within 0.5 s of ``t``."""
    best = None
    for ts, state in table:
        if abs(ts - t) <= 0.5 and (best is None or abs(ts - t) < best[0]):
            best = (abs(ts - t), state)
    return None if best is None else best[1]


def characterize_one(item: dict[str, Any], seconds: float) -> dict[str, Any]:
    """Score both gates on every sampled frame of the first ``seconds``."""
    logger.remove()
    config = load_config(default_config_path())
    probe = GameLanguageProbe(config.vision)
    deadline = config.vision.match_intro.intro_deadline_seconds
    lifecycle = _lifecycle_lookup(item["stem"])
    frames: list[dict[str, Any]] = []
    sampler = FrameSampler(1.0 / config.vision.hud_cadence_fps)
    with VideoLoader(
        Path(item["video"]),
        max_width=config.video.max_width,
        max_height=config.video.max_height,
    ) as loader:
        meta = loader.open()
        for frame in sampler.sample(loader.frames()):
            if frame.timestamp > seconds:
                break
            timer_s = probe._timer_seconds(frame)
            phase = probe.fuser.step(frame.timestamp, timer_s)
            row: dict[str, Any] = {
                "t": round(frame.timestamp, 3),
                "phase": phase,
                "timer": timer_s,
                "lifecycle": _lifecycle_at(lifecycle, frame.timestamp),
                "callout": _scores(probe.callout_matcher.observe(frame.image)),
            }
            if frame.timestamp <= deadline:
                row["intro"] = _scores(probe.intro_matcher.observe(frame.image))
            frames.append(row)
    return {**item, "height": meta.height, "width": meta.width, "frames": frames}


def probe_one(item: dict[str, Any], modes: list[str]) -> dict[str, Any]:
    """Run the production probe in each requested mode."""
    logger.remove()
    config = load_config(default_config_path())
    runs: dict[str, Any] = {}
    for mode in modes:
        outcome = probe_game_language(
            Path(item["video"]),
            config.vision,
            max_width=config.video.max_width,
            max_height=config.video.max_height,
            skip_intro=mode == "forced-fallback",
        )
        reading = outcome.establishing_reading
        frame = outcome.establishing_frame
        runs[mode] = {
            "language": outcome.language,
            "source": outcome.source,
            "termination": outcome.termination,
            "established_at": None if frame is None else frame.timestamp,
            "cue_id": None if reading is None else reading.cue_id,
            "score": None if reading is None else reading.score,
            "runner_up_score": None if reading is None else reading.runner_up_score,
            "intro": outcome.intro.model_dump(mode="json"),
            "fallback": outcome.fallback.model_dump(mode="json"),
            "probe_seconds": round(outcome.stats.probe_seconds, 3),
            "decode_seconds": round(outcome.stats.decode_seconds, 3),
            "video_duration_seconds": outcome.stats.video_duration_seconds,
        }
    return {**item, "runs": runs}


def _run_parallel(
    func,
    items: list[dict[str, Any]],
    workers: int,
    *args,
    checkpoint: Path | None = None,
) -> list:
    """Map ``func`` over recordings in input order; failures recorded.

    Each finished recording is appended to ``checkpoint`` (JSON lines) as it
    completes, so an interrupted run keeps its finished results.
    """
    by_stem: dict[str, dict[str, Any]] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(func, item, *args): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - report and continue
                result = {**item, "error": f"{type(exc).__name__}: {exc}"}
            by_stem[item["stem"]] = result
            if checkpoint is not None:
                with checkpoint.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(result, default=str) + "\n")
            logger.info("done {}/{} {}", len(by_stem), len(items), item["stem"])
    return [by_stem[item["stem"]] for item in items]


def summarize_probe(results: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    """Per-label, per-gate counts for one probe mode."""
    rows = [r for r in results if mode in r.get("runs", {})]
    summary: dict[str, Any] = {"mode": mode, "recordings": len(rows)}
    for label in ("en", "ja", "fr", None):
        group = [r for r in rows if r["label"] == label]
        if not group:
            continue
        key = label or "unlabeled"
        summary[key] = _group_summary(group, mode)
    return summary


def _group_summary(group: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    """Outcome counts and timing for one label group."""
    out: dict[str, Any] = {"n": len(group), "by_source": {}, "wrong": [], "unknown": []}
    times: dict[str, list[float]] = {"intro_mode": [], "gameplay_callout": []}
    after_entry: list[float] = []
    cost: list[float] = []
    for r in group:
        run = r["runs"][mode]
        out["by_source"][run["source"]] = out["by_source"].get(run["source"], 0) + 1
        cost.append(run["probe_seconds"])
        if run["language"] == "unknown":
            out["unknown"].append({"stem": r["stem"], "termination": run["termination"]})
            continue
        if r["label"] is not None and run["language"] != r["label"]:
            out["wrong"].append({"stem": r["stem"], "detected": run["language"]})
        times[run["source"]].append(run["established_at"])
        first = run["fallback"].get("first_time")
        if run["source"] == "gameplay_callout" and first is not None:
            after_entry.append(run["established_at"] - first)
    out["established_at"] = {k: _stats(v) for k, v in times.items() if v}
    out["callout_seconds_after_first_eligible"] = _stats(after_entry)
    out["probe_seconds"] = _stats(cost)
    return out


def _stats(values: list[float]) -> dict[str, float] | None:
    """Min / median / p90 / max of a list."""
    if not values:
        return None
    ordered = sorted(values)
    p90 = ordered[min(len(ordered) - 1, int(0.9 * (len(ordered) - 1) + 0.5))]
    return {
        "n": len(ordered),
        "min": round(ordered[0], 3),
        "median": round(statistics.median(ordered), 3),
        "p90": round(p90, 3),
        "max": round(ordered[-1], 3),
    }


THRESHOLDS = (0.60, 0.65, 0.70, 0.75, 0.80)
MARGINS = (0.05, 0.10, 0.15, 0.20, 0.30)


def _gate_frames(rec: dict[str, Any], gate: str, deadline: float) -> list[dict]:
    """Frames where a gate would run: intro window, or eligible in-match frames."""
    if gate == "intro":
        out = []
        for f in rec["frames"]:
            if f["phase"] == "in_match" or f["t"] > deadline or "intro" not in f:
                break
            out.append(f)
        return out
    return [f for f in rec["frames"] if f["phase"] == "in_match" and f["timer"]]


def _pair(frame: dict[str, Any], key: str, label: str) -> tuple[float, float]:
    """(correct-language score, best other-language score) for one frame."""
    scores = frame[key]
    other = "ja" if label == "en" else "en"
    return scores.get(label) or 0.0, scores.get(other) or 0.0


def _first_accept(
    frames: list[dict], key: str, thr: float, margin: float
) -> tuple[str | None, float | None, int]:
    """Language, time and observation count of the first accepted frame."""
    for count, frame in enumerate(frames, start=1):
        scores = {k: v or 0.0 for k, v in frame[key].items()}
        best = max(scores, key=scores.get)
        others = [v for k, v in scores.items() if k != best]
        if scores[best] >= thr and scores[best] - max(others, default=0.0) >= margin:
            return best, frame["t"], count
    return None, None, len(frames)


def characterization_report(
    records: list[dict], deadline: float, policy: dict[str, tuple[float, float]]
) -> dict[str, Any]:
    """Score ceilings, margins and a threshold/margin grid for both gates.

    ``policy`` maps gate to the configured ``(threshold, margin)``.
    """
    usable = [r for r in records if "frames" in r]
    report: dict[str, Any] = {"recordings": len(usable), "policy": policy}
    for gate, key in (("intro", "intro"), ("callout", "callout")):
        report[gate] = _gate_report(usable, gate, key, deadline, policy[gate])
    report["callout_on_dead_frames"] = _dead_frame_report(usable)
    return report


def _gate_report(
    records: list[dict],
    gate: str,
    key: str,
    deadline: float,
    policy: tuple[float, float],
) -> dict[str, Any]:
    """Per-gate measurements over labelled and unlabelled recordings."""
    labelled = [r for r in records if r["label"] in ("en", "ja")]
    correct, wrong, margins_hi, wrong_frames = [], [], [], []
    for rec in labelled:
        for frame in _gate_frames(rec, gate, deadline):
            c, w = _pair(frame, key, rec["label"])
            correct.append(c)
            wrong.append(w)
            wrong_frames.append((round(w, 3), round(c, 3), rec["stem"], frame["t"]))
            if c >= 0.70:
                margins_hi.append(c - w)
    unlabelled_max = [
        max((max(v or 0.0 for v in f[key].values()) for f in _gate_frames(r, gate,
            deadline)), default=0.0)
        for r in records if r["label"] not in ("en", "ja")
    ]
    out: dict[str, Any] = {
        "labelled_recordings": len(labelled),
        "frames": len(correct),
        "wrong_language_score": _stats(wrong),
        "wrong_language_top10": [
            {"wrong": w, "correct": c, "stem": s, "t": t}
            for w, c, s, t in sorted(wrong_frames, reverse=True)[:10]
        ],
        "slowest_or_unresolved_at_policy": _slowest(
            labelled, gate, key, deadline, policy
        ),
        "correct_language_score": _stats(correct),
        "margin_when_correct_ge_0_70": _stats(margins_hi),
        "non_en_ja_or_unlabelled_max_score": {
            r["stem"]: round(m, 3)
            for r, m in zip(
                [r for r in records if r["label"] not in ("en", "ja")],
                unlabelled_max,
                strict=True,
            )
        },
        "grid": _grid(labelled, gate, key, deadline),
    }
    return out


def _slowest(
    labelled: list[dict],
    gate: str,
    key: str,
    deadline: float,
    policy: tuple[float, float],
) -> list[dict]:
    """Recordings slowest to accept (or unresolved) at the configured policy."""
    rows = []
    for rec in labelled:
        frames = _gate_frames(rec, gate, deadline)
        lang, t, count = _first_accept(frames, key, *policy)
        delay = None if t is None or not frames else round(t - frames[0]["t"], 3)
        best = max(
            (max(v or 0.0 for v in f[key].values()) for f in frames), default=0.0
        )
        rows.append({
            "stem": rec["stem"],
            "label": rec["label"],
            "accepted": lang,
            "seconds_from_first_gate_frame": delay,
            "observations": count,
            "gate_frames": len(frames),
            "best_score": round(best, 3),
        })
    rows.sort(key=_slowness)
    return rows[:15]


def _slowness(row: dict[str, Any]) -> float:
    """Sort key: unresolved first, then longest time to acceptance."""
    if row["accepted"] is None:
        return -1e9
    return -(row["seconds_from_first_gate_frame"] or 0.0)


def _grid(labelled: list[dict], gate: str, key: str, deadline: float) -> list[dict]:
    """Outcome counts for each candidate threshold/margin pair."""
    rows = []
    for thr in THRESHOLDS:
        for margin in MARGINS:
            right = wrong = unresolved = 0
            to_accept, obs = [], []
            for rec in labelled:
                frames = _gate_frames(rec, gate, deadline)
                lang, t, count = _first_accept(frames, key, thr, margin)
                if lang is None:
                    unresolved += 1
                    continue
                right += lang == rec["label"]
                wrong += lang != rec["label"]
                if frames:
                    to_accept.append(t - frames[0]["t"])
                    obs.append(count)
            rows.append({
                "threshold": thr,
                "margin": margin,
                "correct": right,
                "wrong": wrong,
                "unresolved": unresolved,
                "seconds_from_first_gate_frame": _stats(to_accept),
                "observations_to_accept": _stats([float(o) for o in obs]),
            })
    return rows


def _dead_frame_report(records: list[dict]) -> dict[str, Any]:
    """Callout scores on frames a prior analysis marked local-player dead."""
    scores = []
    for rec in records:
        for frame in rec["frames"]:
            if frame.get("lifecycle") in ("dead", "countdown"):
                scores.append(max(v or 0.0 for v in frame["callout"].values()))
    return {"frames": len(scores), "max_any_language_score": _stats(scores)}


def _select(corpus: list[dict[str, Any]], args: argparse.Namespace) -> list:
    """Apply ``--only`` / ``--labels`` / ``--limit-per-label`` filters."""
    items = corpus
    if args.only:
        items = [i for i in items if any(s in i["stem"] for s in args.only)]
    if args.labels:
        wanted = set(args.labels)
        items = [i for i in items if (i["label"] or "unlabeled") in wanted]
    if args.limit_per_label:
        picked: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for item in items:
            key = item["label"] or "unlabeled"
            if counts.get(key, 0) < args.limit_per_label:
                picked.append(item)
                counts[key] = counts.get(key, 0) + 1
        items = picked
    return items


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command",
        choices=["corpus", "characterize", "characterize-report", "probe", "merge"],
    )
    parser.add_argument(
        "--input", type=Path, help="characterize JSON (report) or base probe JSON (merge)"
    )
    parser.add_argument(
        "--overlay", type=Path, help="probe JSON whose recordings replace the base's"
    )
    parser.add_argument("--videos-dir", type=Path, default=Path.home() / "Movies")
    parser.add_argument("--out", type=Path, required=False)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seconds", type=float, default=180.0)
    parser.add_argument(
        "--mode",
        choices=["normal", "forced-fallback", "both"],
        default="both",
    )
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--labels", nargs="*", default=None)
    parser.add_argument("--limit-per-label", type=int, default=None)
    return parser


def merge_probe_runs(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Replace base recordings with overlay recordings (by stem); re-summarize."""
    replaced = {r["stem"]: r for r in overlay["results"]}
    results = [replaced.get(r["stem"], r) for r in base["results"]]
    modes = sorted({m for r in results for m in r.get("runs", {})})
    return {
        "summaries": [summarize_probe(results, m) for m in modes],
        "overlay_stems": sorted(replaced),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = _parser().parse_args(argv)
    if args.command == "merge":
        base = json.loads(args.input.read_text(encoding="utf-8"))
        overlay = json.loads(args.overlay.read_text(encoding="utf-8"))
        merged = json.dumps(merge_probe_runs(base, overlay), indent=2, sort_keys=True)
        args.out.write_text(merged + "\n", encoding="utf-8")
        logger.info("wrote {}", args.out)
        return 0
    if args.command == "characterize-report":
        records = json.loads(args.input.read_text(encoding="utf-8"))
        for record in records:
            record.update(_labelled(Path(record["video"])))
        vision = load_config(default_config_path()).vision
        lang = vision.game_language
        payload_report = characterization_report(
            records,
            vision.match_intro.intro_deadline_seconds,
            {
                "intro": (lang.intro_threshold, lang.intro_language_margin),
                "callout": (lang.threshold, lang.language_margin),
            },
        )
        sys.stdout.write(json.dumps(payload_report, indent=2, default=str) + "\n")
        return 0
    items = _select(build_corpus(args.videos_dir), args)
    if args.command == "corpus":
        payload: Any = items
    elif args.command == "characterize":
        payload = _run_parallel(characterize_one, items, args.workers, args.seconds)
    else:
        modes = ["normal", "forced-fallback"] if args.mode == "both" else [args.mode]
        checkpoint = None
        if args.out is not None:
            checkpoint = args.out.with_suffix(".partial.jsonl")
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            checkpoint.unlink(missing_ok=True)
        results = _run_parallel(
            probe_one, items, args.workers, modes, checkpoint=checkpoint
        )
        payload = {
            "summaries": [summarize_probe(results, m) for m in modes],
            "results": results,
        }
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        logger.info("wrote {}", args.out)
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
