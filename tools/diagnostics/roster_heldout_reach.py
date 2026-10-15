"""Held-out validation of the frozen roster reach candidate (A1 +16, O3 +8).

Pre-registered in ``roster_evidence/heldout_reach/FREEZE.md``. Two methods only:
``production`` (current geometry) and ``candidate`` (A1 right +16, O3 right +8
native px), both through the unchanged production matcher reused from
``roster_roi_reach``. ``build`` scans the frozen pool, samples blind frames and
seals the key; ``score`` runs only after human labels exist. Nothing in
``src/`` or ``configs/`` changes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from roster_roi_reach import (
    MAX_EXT,
    Placement,
    SlotCase,
    best_placement,
    count_metrics,
    label_of,
    reach_interval,
    render_change,
    slot_maps,
    slot_metrics,
    write_sheet,
)
from roster_x_appearance import SLOTS, slot_boxes

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.vision.player_count import (
    _load_masked_templates,
    slot_edge_energy,
)
from splatoon3_ai_coach.vision.roi import crop_roi

CANDIDATE = {"a1": 16, "o3": 8}
SEED = 20261015
SCAN_STEP_S = 1.0
SCAN_OFFSET_S = 0.5
RANDOM_N = 100
RANDOM_PER_VIDEO = 2
DISCORDANT_RUNS = 100
TEMPLATE_DATE = "2026-09-10"
ROW_PAD_PX = 24
IMAGE_WIDTH = 1400
VERDICTS = ("x", "alive", "?")

_WORKER: dict[str, Any] = {}


def right_of(method: str, slot: str) -> int:
    """Right-side search extension of ``slot`` under ``method``."""
    return CANDIDATE.get(slot, 0) if method == "candidate" else 0


def video_date(name: str) -> str:
    """``YYYY-MM-DD`` from a recording name like ``en-2026-09-20 22-38-46.mov``."""
    return name.split(" ")[0][-10:]


def _init_worker(config_path: str) -> None:
    cv2.setNumThreads(1)
    config = load_config(Path(config_path))
    _WORKER["config"] = config
    _WORKER["templates"] = _load_masked_templates(config.vision.player_count.template_dir)


def visible(frame: np.ndarray, config: Any) -> bool:
    """Production roster visibility gate (every slot edge energy >= threshold)."""
    pc = config.vision.player_count
    slots = [*pc.ally_slots, *pc.opponent_slots]
    edges = [slot_edge_energy(crop_roi(frame, box)) for box in slots]
    return min(edges) >= pc.min_slot_edge_energy


def method_scores(
    frame: np.ndarray, boxes: list, templates: list, slots: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    """Production/candidate best score and template per slot, plus X-centre facts."""
    out: dict[str, dict[str, Any]] = {}
    for slot in slots:
        box = boxes[SLOTS.index(slot)]
        maps = slot_maps(frame, box, templates)
        prod = best_placement(maps, box, 0, 0)
        cand = best_placement(maps, box, 0, right_of("candidate", slot))
        free = best_placement(maps, box, MAX_EXT, MAX_EXT)
        lo, hi = reach_interval(maps, box, free.name)
        out[slot] = {
            "production_score": prod.score,
            "production_template": prod.name,
            "candidate_score": cand.score,
            "candidate_template": cand.name,
            "free_center_x": round(free.center_x, 1),
            "free_template": free.name,
            "free_score": free.score,
            "production_reach": [round(lo, 1), round(hi, 1)],
            "candidate_reach": [round(lo, 1), round(hi + right_of("candidate", slot), 1)],
        }
    return out


def read_frame(capture: cv2.VideoCapture, t: float) -> np.ndarray | None:
    """Protocol decode: ``CAP_PROP_POS_MSEC`` seek then one read."""
    capture.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
    ok, frame = capture.read()
    return frame if ok and frame is not None else None


def scan_video(args: tuple[str, float]) -> list[dict[str, Any]]:
    """1 Hz scan of one video: eligible frames with A1/O3 labels for both methods."""
    path, duration = args
    config, templates = _WORKER["config"], _WORKER["templates"]
    capture = cv2.VideoCapture(path)
    rows: list[dict[str, Any]] = []
    t = SCAN_OFFSET_S
    while t < duration:
        frame = read_frame(capture, t)
        if frame is not None and visible(frame, config):
            scores = method_scores(
                frame, slot_boxes(config, frame.shape), templates, tuple(CANDIDATE)
            )
            row = {"video": Path(path).name, "t": round(t, 3), "slots": {}}
            for slot, item in scores.items():
                row["slots"][slot] = {
                    "production": label_of(item["production_score"]),
                    "candidate": label_of(item["candidate_score"]),
                }
            rows.append(row)
        t += SCAN_STEP_S
    capture.release()
    return rows


def discordant_slots(row: dict[str, Any]) -> tuple[str, ...]:
    """Slots on which the two methods disagree in a scanned row."""
    return tuple(
        slot
        for slot, item in row["slots"].items()
        if item["production"] != item["candidate"]
    )


def discordant_runs(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Consecutive discordant scan times (gap <= step, same slot set) per video."""
    runs: list[list[dict[str, Any]]] = []
    for row in sorted(rows, key=lambda r: (r["video"], r["t"])):
        slots = discordant_slots(row)
        if not slots:
            continue
        last = runs[-1][-1] if runs else None
        if (
            last is not None
            and last["video"] == row["video"]
            and row["t"] - last["t"] <= SCAN_STEP_S + 1e-6
            and discordant_slots(last) == slots
        ):
            runs[-1].append(row)
        else:
            runs.append([row])
    return runs


def select_samples(rows: list[dict[str, Any]], seed: int = SEED) -> list[dict[str, Any]]:
    """Pre-registered random + discordant strata, shuffled, with blind IDs."""
    rng = random.Random(seed)
    ordered = sorted(rows, key=lambda r: (r["video"], r["t"]))
    shuffled = ordered[:]
    rng.shuffle(shuffled)
    per_video: Counter = Counter()
    chosen: dict[tuple[str, float], set[str]] = defaultdict(set)
    for row in shuffled:
        if sum(per_video.values()) >= RANDOM_N:
            break
        if per_video[row["video"]] < RANDOM_PER_VIDEO:
            per_video[row["video"]] += 1
            chosen[(row["video"], row["t"])].add("random")
    runs = discordant_runs(ordered)
    if len(runs) > DISCORDANT_RUNS:
        runs = rng.sample(runs, DISCORDANT_RUNS)
    for run in runs:
        pick = rng.choice(run)
        chosen[(pick["video"], pick["t"])].add("discordant")
    keys = sorted(chosen)
    rng.shuffle(keys)
    return [
        {
            "sample_id": f"H{i + 1:03d}",
            "video": video,
            "t": t,
            "strata": sorted(chosen[(video, t)]),
        }
        for i, (video, t) in enumerate(keys)
    ]


def blind_image(frame: np.ndarray, boxes: list, sample_id: str) -> np.ndarray:
    """Top HUD context plus both roster rows enlarged; no boxes or method output."""
    context = frame[0 : int(frame.shape[0] * 0.18), :]
    parts = [_to_width(context, cv2.INTER_AREA)]
    for side in (boxes[:4], boxes[4:]):
        x1 = max(0, side[0][0] - ROW_PAD_PX)
        x2 = min(frame.shape[1], side[3][2] + ROW_PAD_PX)
        y1 = max(0, min(b[1] for b in side) - 8)
        y2 = min(frame.shape[0], max(b[3] for b in side) + 8)
        parts.append(_to_width(frame[y1:y2, x1:x2], cv2.INTER_CUBIC))
    header = np.zeros((40, IMAGE_WIDTH, 3), dtype=np.uint8)
    cv2.putText(
        header,
        f"{sample_id}   rows: ALLY (A1..A4), then OPPONENT (O1..O4)",
        (8, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    gap = np.zeros((6, IMAGE_WIDTH, 3), dtype=np.uint8)
    stacked = [header]
    for part in parts:
        stacked += [part, gap]
    return np.vstack(stacked)


def _to_width(image: np.ndarray, interpolation: int) -> np.ndarray:
    height = round(image.shape[0] * IMAGE_WIDTH / image.shape[1])
    return cv2.resize(image, (IMAGE_WIDTH, height), interpolation=interpolation)


LABELING = """# Held-out roster labels

Frozen held-out sample for the A1 +16 / O3 +8 validation (see
`../FREEZE.md`), seed {seed}, {n} frames.

For each row in `labels.csv`, inspect `images/<sample_id>.png` or the contact
sheets. Set `hud_visible` to `visible`, `hidden`, or `?`. Set each `a1..a4`
and `o1..o4` to `x`, `alive`, or `?`. Use `?` whenever you cannot determine
the slot; ambiguity is a result, not an error. Dark-silhouette icons are
labeled `x`. Do not open `key.json` before labels are sealed.

Each image shows the top HUD, then the ally row enlarged, then the opponent
row enlarged. A = ally row, O = opponent row, slots left-to-right. Do not add,
delete, reorder, or replace samples.
"""


def scan_pool(
    pool: list[dict[str, Any]], video_dir: Path, config_path: Path, workers: int
) -> list[dict[str, Any]]:
    """Scan every pool video in parallel; rows sorted by (video, t)."""
    jobs = [(str(video_dir / item["video"]), item["duration_s"]) for item in pool]
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(
        workers, initializer=_init_worker, initargs=(str(config_path),)
    ) as executor:
        for found in executor.map(scan_video, jobs):
            rows.extend(found)
    return sorted(rows, key=lambda r: (r["video"], r["t"]))


def seal_sample(
    sample: dict[str, Any], video_dir: Path, config: Any, templates: list
) -> tuple[np.ndarray, dict[str, Any]]:
    """Decode one sampled frame; blind image plus its sealed key entry."""
    capture = cv2.VideoCapture(str(video_dir / sample["video"]))
    frame = read_frame(capture, sample["t"])
    capture.release()
    if frame is None or not visible(frame, config):
        raise RuntimeError(f"{sample['sample_id']}: frame not reproducible")
    boxes = slot_boxes(config, frame.shape)
    image = blind_image(frame, boxes, sample["sample_id"])
    entry = dict(sample)
    entry["image_sha256"] = hashlib.sha256(
        cv2.imencode(".png", image)[1].tobytes()
    ).hexdigest()
    entry["slots"] = method_scores(frame, boxes, templates, SLOTS)
    return image, entry


def build(
    output: Path, video_dir: Path, config_path: Path, workers: int
) -> dict[str, Any]:
    """Scan the frozen pool, draw the strata, write blind images and sealed key."""
    pool = json.loads((output / "pool.json").read_text("utf-8"))["pool"]
    rows = scan_pool(pool, video_dir, config_path, workers)
    sealed = output / "sealed"
    sealed.mkdir(parents=True, exist_ok=True)
    (sealed / "scan.json").write_text(json.dumps(rows) + "\n", "utf-8")
    samples = select_samples(rows)
    config = load_config(config_path)
    templates = _load_masked_templates(config.vision.player_count.template_dir)
    labels_dir = output / "labels"
    (labels_dir / "images").mkdir(parents=True, exist_ok=True)
    images, entries = [], []
    for sample in samples:
        image, entry = seal_sample(sample, video_dir, config, templates)
        cv2.imwrite(str(labels_dir / "images" / f"{sample['sample_id']}.png"), image)
        images.append(image)
        entries.append(entry)
    _write_label_files(labels_dir, [s["sample_id"] for s in samples], images)
    summary = _scan_summary(pool, rows, samples)
    key = {"schema_version": 1, "candidate": CANDIDATE, "seed": SEED}
    key.update({"scan_summary": summary, "samples": entries})
    (sealed / "key.json").write_text(json.dumps(key, indent=1) + "\n", "utf-8")
    return summary


def _write_label_files(labels_dir: Path, ids: list[str], images: list) -> None:
    with (labels_dir / "labels.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["sample_id", "hud_visible", *SLOTS, "notes"])
        for sid in ids:
            writer.writerow([sid, "", *[""] * len(SLOTS), ""])
    (labels_dir / "LABELING.md").write_text(
        LABELING.format(seed=SEED, n=len(ids)), "utf-8"
    )
    for start in range(0, len(images), 8):
        write_sheet(
            images[start : start + 8],
            labels_dir / "sheets" / f"sheet_{start // 8 + 1:02d}.png",
            f"{ids[start]}..{ids[min(start + 8, len(ids)) - 1]}",
        )


def _scan_summary(
    pool: list[dict[str, Any]], rows: list[dict[str, Any]], samples: list[dict]
) -> dict[str, Any]:
    strata = Counter(s for sample in samples for s in sample["strata"])
    return {
        "pool_videos": len(pool),
        "videos_with_eligible_frames": len({r["video"] for r in rows}),
        "eligible_frames": len(rows),
        "discordant_frames": sum(bool(discordant_slots(r)) for r in rows),
        "discordant_runs": len(discordant_runs(rows)),
        "samples": len(samples),
        "strata": dict(strata),
        "both_strata": sum(len(s["strata"]) == 2 for s in samples),
    }


def read_labels(path: Path) -> dict[str, dict[str, str]]:
    """Validated human labels keyed by sample id."""
    labels: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["hud_visible"] not in ("visible", "hidden", "?"):
                raise ValueError(f"{row['sample_id']}: bad hud_visible")
            for slot in SLOTS:
                if row[slot] not in VERDICTS:
                    raise ValueError(f"{row['sample_id']} {slot}: bad label")
            labels[row["sample_id"]] = row
    return labels


def slot_rows(
    labels: dict[str, dict[str, str]], key: dict[str, Any]
) -> list[dict[str, Any]]:
    """Every human-determinable slot on visible frames with both methods' labels."""
    rows = []
    for entry in key["samples"]:
        row = labels[entry["sample_id"]]
        if row["hud_visible"] != "visible":
            continue
        for index, slot in enumerate(SLOTS):
            if row[slot] == "?":
                continue
            item = entry["slots"][slot]
            rows.append(
                {
                    "sample_id": entry["sample_id"],
                    "video": entry["video"],
                    "t": entry["t"],
                    "strata": entry["strata"],
                    "slot": slot,
                    "index": index,
                    "human": row[slot],
                    "production": label_of(item["production_score"]),
                    "candidate": label_of(item["candidate_score"]),
                    **item,
                }
            )
    return rows


class _Case:
    """Minimal stand-in for ``SlotCase`` in the shared metric helpers."""

    def __init__(self, row: dict[str, Any]) -> None:
        self.sample_id = row["sample_id"]
        self.slot = row["slot"]
        self.human = row["human"]


def paired(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Production -> candidate correctness transitions over slots."""
    counts: Counter = Counter()
    for row in rows:
        before = "correct" if row["production"] == row["human"] else "wrong"
        after = "correct" if row["candidate"] == row["human"] else "wrong"
        counts[f"{before}->{after}"] += 1
    order = ("correct->correct", "wrong->correct", "correct->wrong", "wrong->wrong")
    return {name: counts[name] for name in order}


def metrics(rows: list[dict[str, Any]], labels: dict[str, Any]) -> dict[str, Any]:
    """Paired transitions, slot/side/AvB metrics for both methods, A1/O3 detail."""
    cases = [_Case(r) for r in rows]
    fixed = {(r["sample_id"], r["slot"]): r["production"] for r in rows}
    cand = {(r["sample_id"], r["slot"]): r["candidate"] for r in rows}
    result: dict[str, Any] = {
        "frames": len({r["sample_id"] for r in rows}),
        "determinable_slots": len(rows),
        "human_x_slots": sum(r["human"] == "x" for r in rows),
        "paired": paired(rows),
        "production": slot_metrics(cases, fixed),
        "candidate": slot_metrics(cases, cand),
    }
    result["production"].update(count_metrics(labels, fixed, fixed))
    result["candidate"].update(count_metrics(labels, fixed, cand))
    for slot in CANDIDATE:
        sub = [r for r in rows if r["slot"] == slot]
        sub_cases = [_Case(r) for r in sub]
        result[slot] = {
            "paired": paired(sub),
            "production": _confusion(slot_metrics(sub_cases, fixed)),
            "candidate": _confusion(slot_metrics(sub_cases, cand)),
        }
    return result


def _confusion(m: dict[str, Any]) -> dict[str, Any]:
    keys = ("tp", "fp", "fn", "tn", "x_precision", "x_recall")
    return {k: m[k] for k in keys}


def x_reach(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """X centre and reachability for every held-out human X on A1/O3."""
    out = []
    for row in rows:
        if row["slot"] not in CANDIDATE or row["human"] != "x":
            continue
        lo, hi = row["production_reach"]
        hi_c = row["candidate_reach"][1]
        center = row["free_center_x"]
        out.append(
            {
                "sample_id": row["sample_id"],
                "slot": row["slot"],
                "strata": row["strata"],
                "center_x": center,
                "free_template": row["free_template"],
                "free_score": row["free_score"],
                "production_reach": [lo, hi],
                "candidate_reach": [lo, hi_c],
                "reachable_production": lo <= center <= hi,
                "reachable_candidate": lo <= center <= hi_c,
                "free_window_is_x": label_of(row["free_score"]) == "x",
                "production": row["production"],
                "candidate": row["candidate"],
            }
        )
    return sorted(out, key=lambda r: (r["slot"], r["center_x"]))


def render_transitions(
    rows: list[dict[str, Any]], video_dir: Path, config_path: Path, output: Path
) -> list[dict[str, Any]]:
    """Overlay every slot whose label differs between methods; return FP records."""
    config = load_config(config_path)
    templates = _load_masked_templates(config.vision.player_count.template_dir)
    records, sheets = [], defaultdict(list)
    for row in (r for r in rows if r["production"] != r["candidate"]):
        capture = cv2.VideoCapture(str(video_dir / row["video"]))
        frame = read_frame(capture, row["t"])
        capture.release()
        boxes = slot_boxes(config, frame.shape)
        box = boxes[row["index"]]
        maps = slot_maps(frame, box, templates)
        right = right_of("candidate", row["slot"])
        placement: Placement = best_placement(maps, box, 0, right)
        kind = "recovered_fn" if row["human"] == "x" else "candidate_fp"
        case = SlotCase(
            row["sample_id"],
            row["slot"],
            row["index"],
            row["human"],
            row["production"],
            box,
            maps,
            row["production_score"],
        )
        title = (
            f"{row['sample_id']} {row['slot']} {kind} R+{right} {placement.name} "
            f"{placement.score:.4f} (prod {row['production_score']:.4f})"
        )
        image = render_change(frame, boxes, case, placement, right, title)
        name = f"{kind}_{row['sample_id']}_{row['slot']}.png"
        cv2.imwrite(str(output / "overlays" / name), image)
        sheets[kind].append(image)
        records.append(_transition_record(row, kind, placement, box, name))
    for kind, images in sheets.items():
        write_sheet(
            images,
            output / "sheets" / f"{kind}.png",
            f"{kind}: green prod ROI | cyan extension | magenta winning mask",
        )
    return records


def _transition_record(
    row: dict[str, Any], kind: str, placement: Placement, box: tuple, name: str
) -> dict[str, Any]:
    return {
        "kind": kind,
        "sample_id": row["sample_id"],
        "video": row["video"],
        "t": row["t"],
        "strata": row["strata"],
        "slot": row["slot"],
        "human": row["human"],
        "production_score": row["production_score"],
        "candidate_score": placement.score,
        "candidate_template": placement.name,
        "placement_center_x": round(placement.center_x, 1),
        "beyond_production_roi_px": max(0, placement.left + placement.width - box[2]),
        "overlay": f"overlays/{name}",
    }


def score(output: Path, video_dir: Path, config_path: Path) -> dict[str, Any]:
    """Score both methods against the sealed labels; render every transition."""
    labels = read_labels(output / "labels" / "labels.csv")
    key = json.loads((output / "sealed" / "key.json").read_text("utf-8"))
    for entry in key["samples"]:
        image = cv2.imread(
            str(output / "labels" / "images" / f"{entry['sample_id']}.png")
        )
        digest = hashlib.sha256(cv2.imencode(".png", image)[1].tobytes()).hexdigest()
        if digest != entry["image_sha256"]:
            raise RuntimeError(f"{entry['sample_id']}: image changed since sealing")
    rows = slot_rows(labels, key)
    late = [r for r in rows if video_date(r["video"]) > TEMPLATE_DATE]
    (output / "overlays").mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "candidate": CANDIDATE,
        "scan_summary": key["scan_summary"],
        "hud_visible": dict(
            Counter(labels[e["sample_id"]]["hud_visible"] for e in key["samples"])
        ),
        "all": metrics(rows, labels),
        "random": metrics([r for r in rows if "random" in r["strata"]], labels),
        "discordant": metrics([r for r in rows if "discordant" in r["strata"]], labels),
        "after_template_date": metrics(late, labels),
        "x_reach": x_reach(rows),
        "transitions": render_transitions(rows, video_dir, config_path, output),
    }
    (output / "heldout_results.json").write_text(
        json.dumps(payload, indent=2) + "\n", "utf-8"
    )
    return payload


def main() -> int:
    """``build`` the blind held-out set, or ``score`` it after labeling."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("build", "score"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--video-dir", type=Path, default=Path.home() / "Movies")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    if args.stage == "build":
        summary = build(args.output, args.video_dir, args.config, args.workers)
        print(json.dumps(summary, indent=2))
    else:
        score(args.output, args.video_dir, args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
