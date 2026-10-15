"""Experiment 3: roster X reach and minimal per-slot ROI search extensions.

Read-only development diagnostic over the sealed roster baseline. A candidate
widens one slot's production ROI by ``R`` native pixels on one side and runs the
unchanged production matcher (same templates, load-time masks, template scale,
masked ``TM_SQDIFF_NORMED`` and 0.03 threshold) on that wider crop. Nothing in
``src/`` or ``configs/`` changes, and no candidate is selected here.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from roster_x_appearance import SLOTS, _load, decode_verified, slot_boxes

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.vision.player_count import (
    MaskedXTemplate,
    _best_masked_sqdiff,
    _fit_template_and_mask,
    _load_masked_templates,
)

THRESHOLD = 0.03
EXTENSIONS = (0, 4, 8, 12, 16, 20, 24)
MAX_EXT = 24
NEIGHBOR_X_TOLERANCE_PX = 8.0
CLUSTER_GAP_PX = 4.0
SEPARATE = {("R029", "o1")}
SCALE = 3


@dataclass(frozen=True)
class TemplateMap:
    """One template's SQDIFF map over the horizontally widened crop."""

    name: str
    result: np.ndarray
    origin_x: int
    width: int
    centroid_x: float
    centroid_y: float
    mask: np.ndarray


@dataclass(frozen=True)
class Placement:
    """Best template placement inside one search window."""

    score: float
    name: str
    left: int
    top: int
    center_x: float
    center_y: float
    width: int
    mask: np.ndarray


@dataclass
class SlotCase:
    """One human-determinable live slot, scorable under any extension."""

    sample_id: str
    slot: str
    index: int
    human: str
    stored_label: str
    box: tuple[int, int, int, int]
    maps: list[TemplateMap]
    production_score: float


def slot_maps(
    frame: np.ndarray, box: tuple[int, int, int, int], templates: list[MaskedXTemplate]
) -> list[TemplateMap]:
    """Production-fitted templates matched over the ROI widened by ``MAX_EXT``.

    Templates are fitted against the production ROI, so scale is unchanged;
    widening only horizontally keeps the production fit identical.
    """
    x1, y1, x2, y2 = box
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    roi = gray[y1:y2, x1:x2]
    ex1, ex2 = max(0, x1 - MAX_EXT), min(gray.shape[1], x2 + MAX_EXT)
    wide = gray[y1:y2, ex1:ex2]
    maps = []
    for item in templates:
        fitted = _fit_template_and_mask(roi, item.image, item.mask)
        if fitted is None:
            continue
        template, mask = fitted
        result = cv2.matchTemplate(wide, template, cv2.TM_SQDIFF_NORMED, mask=mask)
        ys, xs = np.nonzero(mask)
        maps.append(
            TemplateMap(
                item.name,
                result,
                ex1,
                template.shape[1],
                float(xs.mean()),
                float(ys.mean()),
                mask,
            )
        )
    return maps


def best_placement(
    maps: list[TemplateMap], box: tuple[int, int, int, int], left: int, right: int
) -> Placement | None:
    """Production best match over the ROI widened by ``left``/``right`` pixels.

    A template whose window contains NaN is skipped, as ``result.min()`` does
    in production.
    """
    x1, y1, x2, _ = box
    best: Placement | None = None
    for item in maps:
        start = max(0, x1 - left - item.origin_x)
        stop = min(item.result.shape[1], x2 + right - item.width - item.origin_x + 1)
        if start >= stop:
            continue
        window = item.result[:, start:stop]
        score = float(window.min())
        if not np.isfinite(score) or (best is not None and score >= best.score):
            continue
        row, col = np.unravel_index(int(np.argmin(window)), window.shape)
        abs_left = item.origin_x + start + int(col)
        best = Placement(
            score,
            item.name,
            abs_left,
            y1 + int(row),
            abs_left + item.centroid_x,
            y1 + int(row) + item.centroid_y,
            item.width,
            item.mask,
        )
    return best


def reach_interval(
    maps: list[TemplateMap], box: tuple[int, int, int, int], name: str
) -> tuple[float, float]:
    """Template-centre x range a full placement of ``name`` can reach in the ROI."""
    item = next(m for m in maps if m.name == name)
    return box[0] + item.centroid_x, box[2] - item.width + item.centroid_x


def distance_bin(distance: float) -> str:
    """Bin a signed distance to a reach boundary (negative means beyond)."""
    rounded = round(distance, 1)
    value = abs(rounded)
    where = "beyond" if rounded < 0 else "inside"
    for limit, label in ((1, "0"), (5, "1-4"), (10, "5-9"), (15, "10-14")):
        if value < limit:
            return f"{where} {label}"
    return f"{where} 15+"


def label_of(score: float | None) -> str:
    """Production vocabulary for a score."""
    return "x" if score is not None and score <= THRESHOLD else "alive"


def collect_cases(
    baseline: Path, video_dir: Path, config: AppConfig, templates: list[MaskedXTemplate]
) -> tuple[list[SlotCase], dict[str, Any], dict[str, Any], dict[str, np.ndarray]]:
    """Decode every live sample with a stored reading and build slot cases."""
    manifest, labels, keys = _load(baseline)
    cases: list[SlotCase] = []
    frames: dict[str, np.ndarray] = {}
    for sample in manifest["samples"]:
        sid = sample["sample_id"]
        key = keys[sid]
        if sample["source_class"] != "live_gameplay" or not key["raw_slots"]:
            continue
        frame, verified = decode_verified(sample, key, video_dir, config)
        if not verified:
            raise RuntimeError(f"{sid}: decoded frame does not match sealed hash")
        frames[sid] = frame
        boxes = slot_boxes(config, frame.shape)
        for index, slot in enumerate(SLOTS):
            human = labels[sid][slot]
            if human == "?":
                continue
            box = boxes[index]
            roi = frame[box[1] : box[3], box[0] : box[2]]
            cases.append(
                SlotCase(
                    sid,
                    slot,
                    index,
                    human,
                    key["raw_slots"][index],
                    box,
                    slot_maps(frame, box, templates),
                    _best_masked_sqdiff(roi, templates)[0],
                )
            )
    return cases, labels, manifest, frames


def x_distribution(cases: list[SlotCase]) -> list[dict[str, Any]]:
    """X centre, reach interval and boundary bins for every human-X slot."""
    rows = []
    for case in cases:
        if case.human != "x":
            continue
        free = best_placement(case.maps, case.box, MAX_EXT, MAX_EXT)
        fixed = best_placement(case.maps, case.box, 0, 0)
        lo, hi = reach_interval(case.maps, case.box, free.name)
        rows.append(
            {
                "sample_id": case.sample_id,
                "slot": case.slot,
                "group": "tp" if label_of(fixed.score) == "x" else "fn",
                "separate": (case.sample_id, case.slot) in SEPARATE,
                "center_x": round(free.center_x, 1),
                "free_template": free.name,
                "free_score": free.score,
                "production_score": fixed.score,
                "reach_lo": round(lo, 1),
                "reach_hi": round(hi, 1),
                "left_distance": round(free.center_x - lo, 1),
                "right_distance": round(hi - free.center_x, 1),
                "left_bin": distance_bin(free.center_x - lo),
                "right_bin": distance_bin(hi - free.center_x),
            }
        )
    return sorted(rows, key=lambda row: (row["slot"], row["center_x"]))


def clusters(values: list[float], gap: float = CLUSTER_GAP_PX) -> list[list[float]]:
    """Split sorted 1-D positions wherever consecutive values differ by > ``gap``."""
    groups: list[list[float]] = []
    for value in sorted(values):
        if groups and value - groups[-1][-1] <= gap:
            groups[-1].append(value)
        else:
            groups.append([value])
    return groups


@dataclass
class Candidate:
    """Right-only extension per slot; slots absent stay at production."""

    name: str
    right: dict[str, int]


class Evaluator:
    """Score candidates on all human-determinable live slots."""

    def __init__(self, cases: list[SlotCase], labels: dict[str, Any]) -> None:
        self.cases = cases
        self.labels = labels
        self.by_key = {(c.sample_id, c.slot): c for c in cases}
        self._cache: dict[tuple[str, str, int], Placement] = {}
        self.fixed = {
            (c.sample_id, c.slot): label_of(self.placement(c, 0).score) for c in cases
        }
        self.centers = {
            (c.sample_id, c.slot): best_placement(
                c.maps, c.box, MAX_EXT, MAX_EXT
            ).center_x
            for c in cases
            if c.human == "x"
        }

    def placement(self, case: SlotCase, right: int) -> Placement:
        """Cached best placement with a right-only extension."""
        key = (case.sample_id, case.slot, right)
        if key not in self._cache:
            self._cache[key] = best_placement(case.maps, case.box, 0, right)
        return self._cache[key]

    def predict(self, candidate: Candidate) -> dict[tuple[str, str], str]:
        """Labels for every determinable slot under a candidate."""
        return {
            (c.sample_id, c.slot): (
                label_of(self.placement(c, candidate.right[c.slot]).score)
                if candidate.right.get(c.slot)
                else self.fixed[(c.sample_id, c.slot)]
            )
            for c in self.cases
        }

    def attribute(self, case: SlotCase, placement: Placement) -> dict[str, Any]:
        """Where the winning placement landed: target X, neighbour X or other."""
        neighbor = SLOTS[case.index + 1] if case.index % 4 != 3 else None
        nkey = (case.sample_id, neighbor) if neighbor else None
        neighbor_human = self.labels[case.sample_id][neighbor] if neighbor else None
        target_center = self.centers.get((case.sample_id, case.slot))
        neighbor_center = self.centers.get(nkey) if nkey else None
        if (
            target_center is not None
            and abs(placement.center_x - target_center) <= NEIGHBOR_X_TOLERANCE_PX
        ):
            category = "target_x"
        elif (
            neighbor_center is not None
            and abs(placement.center_x - neighbor_center) <= NEIGHBOR_X_TOLERANCE_PX
        ):
            category = "neighbor_x"
        else:
            category = "other_content"
        return {
            "category": category,
            "neighbor": neighbor,
            "neighbor_human": neighbor_human,
            "placement_center_x": round(placement.center_x, 1),
            "placement_template": placement.name,
            "placement_score": placement.score,
            "beyond_production_roi_px": max(
                0, placement.left + placement.width - case.box[2]
            ),
            "target_x_center": round(target_center, 1) if target_center else None,
            "neighbor_x_center": round(neighbor_center, 1) if neighbor_center else None,
        }

    def evaluate(self, candidate: Candidate) -> dict[str, Any]:
        """Slot, side-count, AvB metrics and paired transitions versus production."""
        predicted = self.predict(candidate)
        result: dict[str, Any] = {"name": candidate.name, "right": candidate.right}
        result.update(slot_metrics(self.cases, predicted))
        result.update(count_metrics(self.labels, self.fixed, predicted))
        result["changes"] = self._changes(candidate, predicted)
        result["alive_headroom"] = self.alive_headroom(candidate)
        return result

    def alive_headroom(self, candidate: Candidate, lowest: int = 5) -> list[dict]:
        """Lowest scores on human-alive slots a candidate extends (collateral margin)."""
        rows = []
        for case in self.cases:
            right = candidate.right.get(case.slot)
            if case.human != "alive" or not right:
                continue
            placement = self.placement(case, right)
            rows.append(
                {
                    "sample_id": case.sample_id,
                    "slot": case.slot,
                    "score": placement.score,
                    "production_score": self.placement(case, 0).score,
                    "category": self.attribute(case, placement)["category"],
                }
            )
        return sorted(rows, key=lambda row: row["score"])[:lowest]

    def fine_sweep(self, slot: str) -> list[dict[str, Any]]:
        """1 px right-only sweep for one slot: recoveries, new FPs, alive minimum."""
        slot_cases = [c for c in self.cases if c.slot == slot]
        rows = []
        for right in range(MAX_EXT + 1):
            scores = {
                (c.sample_id, c.human): self.placement(c, right).score for c in slot_cases
            }
            alive = [s for (_, human), s in scores.items() if human == "alive"]
            rows.append(
                {
                    "right": right,
                    "x_detected": sum(
                        label_of(s) == "x" for (_, h), s in scores.items() if h == "x"
                    ),
                    "fp": sum(label_of(s) == "x" for s in alive),
                    "alive_min_score": min(alive) if alive else None,
                }
            )
        return rows

    def _changes(
        self, candidate: Candidate, predicted: dict[tuple[str, str], str]
    ) -> dict[str, Any]:
        """Recovered FNs, new FPs and retained FPs, each with attribution."""
        recovered, new_fp, retained_fp = [], [], []
        for case in self.cases:
            key = (case.sample_id, case.slot)
            before, after = self.fixed[key], predicted[key]
            if before == "x" and case.human == "alive":
                retained_fp.append(
                    {"sample_id": key[0], "slot": key[1], "still_x": after == "x"}
                )
            if before == after:
                continue
            item = {"sample_id": key[0], "slot": key[1], "human": case.human}
            item.update(
                self.attribute(case, self.placement(case, candidate.right[case.slot]))
            )
            if case.human == "x":
                item["separate"] = key in SEPARATE
                recovered.append(item)
            else:
                new_fp.append(item)
        return {
            "recovered_fn": recovered,
            "new_fp": new_fp,
            "existing_fp": retained_fp,
        }


def slot_metrics(
    cases: list[SlotCase], predicted: dict[tuple[str, str], str]
) -> dict[str, Any]:
    """Confusion counts, X precision/recall and per-slot accuracy."""
    totals: Counter = Counter()
    per_slot: dict[str, Counter] = defaultdict(Counter)
    for case in cases:
        label = predicted[(case.sample_id, case.slot)]
        kind = {
            ("x", "x"): "tp",
            ("alive", "x"): "fp",
            ("x", "alive"): "fn",
            ("alive", "alive"): "tn",
        }[(case.human, label)]
        totals[kind] += 1
        per_slot[case.slot][kind] += 1
    return {
        "tp": totals["tp"],
        "fp": totals["fp"],
        "fn": totals["fn"],
        "tn": totals["tn"],
        "x_precision": _ratio(totals["tp"], totals["tp"] + totals["fp"]),
        "x_recall": _ratio(totals["tp"], totals["tp"] + totals["fn"]),
        "slot_accuracy": _ratio(totals["tp"] + totals["tn"], sum(totals.values())),
        "per_slot_accuracy": {
            slot: _ratio(c["tp"] + c["tn"], sum(c.values()))
            for slot, c in sorted(per_slot.items())
        },
    }


def count_metrics(
    labels: dict[str, Any],
    fixed: dict[tuple[str, str], str],
    predicted: dict[tuple[str, str], str],
) -> dict[str, Any]:
    """Exact side-count and raw AvB accuracy, plus AvB paired transitions."""
    samples = sorted({key[0] for key in predicted})
    side_hits = side_total = avb_hits = avb_total = 0
    transitions: Counter = Counter()
    for sid in samples:
        row = labels[sid]
        sides = []
        for indexes in (range(4), range(4, 8)):
            names = [SLOTS[i] for i in indexes]
            if any(row[name] == "?" for name in names):
                sides.append(None)
                continue
            human = sum(row[name] == "alive" for name in names)
            new = 4 - sum(predicted[(sid, name)] == "x" for name in names)
            old = 4 - sum(fixed[(sid, name)] == "x" for name in names)
            side_total += 1
            side_hits += new == human
            sides.append((human, new, old))
        if None in sides:
            continue
        avb_total += 1
        new_ok = all(human == new for human, new, _ in sides)
        old_ok = all(human == old for human, _, old in sides)
        avb_hits += new_ok
        transitions[
            f"{'correct' if old_ok else 'wrong'}->{'correct' if new_ok else 'wrong'}"
        ] += 1
    return {
        "side_count_exact": side_hits,
        "side_count_comparable": side_total,
        "side_count_accuracy": _ratio(side_hits, side_total),
        "raw_avb_exact": avb_hits,
        "raw_avb_comparable": avb_total,
        "raw_avb_accuracy": _ratio(avb_hits, avb_total),
        "avb_transitions": dict(sorted(transitions.items())),
    }


def min_extension(evaluator: Evaluator) -> list[dict[str, Any]]:
    """Smallest right extension (1 px steps) recovering each production FN."""
    rows = []
    for case in evaluator.cases:
        key = (case.sample_id, case.slot)
        if case.human != "x" or evaluator.fixed[key] == "x":
            continue
        needed = next(
            (
                right
                for right in range(MAX_EXT + 1)
                if label_of(evaluator.placement(case, right).score) == "x"
            ),
            None,
        )
        placement = evaluator.placement(case, needed if needed is not None else MAX_EXT)
        rows.append(
            {
                "sample_id": case.sample_id,
                "slot": case.slot,
                "separate": key in SEPARATE,
                "min_right_px": needed,
                "score_at_min": placement.score,
                "category": evaluator.attribute(case, placement)["category"],
            }
        )
    return rows


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def candidates() -> list[Candidate]:
    """Per-slot right-only sweeps, uniform right-only and the A1 x O3 grid."""
    items = [Candidate("production", {})]
    for slot in SLOTS:
        for right in EXTENSIONS[1:]:
            items.append(Candidate(f"{slot}+R{right}", {slot: right}))
    for right in EXTENSIONS[1:]:
        items.append(Candidate(f"all+R{right}", dict.fromkeys(SLOTS, right)))
    for a1 in EXTENSIONS[1:]:
        for o3 in EXTENSIONS[1:]:
            items.append(Candidate(f"a1+R{a1},o3+R{o3}", {"a1": a1, "o3": o3}))
    return items


def _up(image: np.ndarray) -> np.ndarray:
    return cv2.resize(image, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_NEAREST)


def render_change(
    frame: np.ndarray,
    boxes: list[tuple[int, int, int, int]],
    case: SlotCase,
    placement: Placement,
    right: int,
    title: str,
) -> np.ndarray:
    """Production ROI, extended search, placement, target centre/territory, neighbour."""
    x1, y1, x2, y2 = case.box
    neighbor = boxes[case.index + 1] if case.index % 4 != 3 else None
    left = max(0, x1 - 30)
    stop = min(frame.shape[1], (neighbor[2] if neighbor else x2) + 40)
    top, bottom = max(0, y1 - 12), min(frame.shape[0], y2 + 12)
    canvas = _up(frame[top:bottom, left:stop].copy())

    def sx(x: float) -> int:
        return round((x - left) * SCALE)

    def sy(y: float) -> int:
        return round((y - top) * SCALE)

    cv2.rectangle(canvas, (sx(x2), sy(y1)), (sx(x2 + right), sy(y2)), (255, 255, 0), 2)
    if neighbor:
        cv2.rectangle(
            canvas,
            (sx(neighbor[0]), sy(neighbor[1]) + 4),
            (sx(neighbor[2]), sy(neighbor[3]) - 4),
            (0, 140, 255),
            2,
        )
        divider = ((x1 + x2) / 2 + (neighbor[0] + neighbor[2]) / 2) / 2
        cv2.line(
            canvas, (sx(divider), 0), (sx(divider), canvas.shape[0]), (0, 255, 255), 1
        )
    cv2.rectangle(canvas, (sx(x1), sy(y1)), (sx(x2), sy(y2)), (0, 255, 0), 2)
    center = (x1 + x2) / 2
    cv2.line(canvas, (sx(center), 0), (sx(center), canvas.shape[0]), (255, 80, 0), 1)
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    h, w = placement.mask.shape
    mask[placement.top : placement.top + h, placement.left : placement.left + w] = (
        placement.mask
    )
    mask = _up(mask[top:bottom, left:stop])
    canvas[(mask - cv2.erode(mask, np.ones((3, 3)))) > 0] = (255, 0, 255)
    cv2.putText(
        canvas,
        title,
        (6, 22),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return canvas


def write_sheet(images: list[np.ndarray], path: Path, title: str) -> None:
    """Stack images under a title bar, padding widths."""
    if not images:
        return
    width = max(image.shape[1] for image in images)
    rows = [
        cv2.copyMakeBorder(i, 0, 6, 0, width - i.shape[1], cv2.BORDER_CONSTANT)
        for i in images
    ]
    header = np.zeros((36, width, 3), dtype=np.uint8)
    cv2.putText(
        header,
        title,
        (8, 26),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.vstack([header, *rows]))


def distribution_plot(
    distribution: list[dict[str, Any]], cases: list[SlotCase], path: Path
) -> None:
    """Per-slot strip: ROI span, x-1/x-3 reach bars, TP (green) / FN (red) centres."""
    px = 4
    panels = []
    for slot in SLOTS:
        case = next(c for c in cases if c.slot == slot)
        x1, _, x2, _ = case.box
        lo_all, hi_all = x1 - MAX_EXT, x2 + MAX_EXT
        canvas = np.full((120, (hi_all - lo_all) * px + 20, 3), 30, dtype=np.uint8)

        def sx(x: float, lo: int = lo_all) -> int:
            return 10 + round((x - lo) * px)

        cv2.line(canvas, (sx(x1), 30), (sx(x2), 30), (200, 200, 200), 3)
        for row_y, name, color in (
            (45, "player-x-1", (255, 200, 0)),
            (55, "player-x-3", (0, 200, 255)),
        ):
            lo, hi = reach_interval(case.maps, case.box, name)
            cv2.line(canvas, (sx(lo), row_y), (sx(hi), row_y), color, 3)
        for row in (r for r in distribution if r["slot"] == slot):
            color = (
                (255, 0, 255)
                if row["separate"]
                else ((0, 0, 255) if row["group"] == "fn" else (0, 220, 0))
            )
            y = 80 if row["group"] == "fn" else 70
            cv2.circle(canvas, (sx(row["center_x"]), y), 4, color, -1)
        cv2.putText(
            canvas,
            f"{slot} ROI {x1}-{x2} grey; reach x-1 cyan, x-3 orange; TP green FN red",
            (10, 112),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        panels.append(canvas)
    write_sheet(
        panels, path, "Observed X centres vs template-centre reach (native px x4)"
    )


def match_identity(roots: list[str], match: str) -> dict[str, Any]:
    """Battle mode and stage for a match, when recorded."""
    for root in roots:
        path = Path(root) / match / "match_identity.json"
        if path.exists():
            data = json.loads(path.read_text("utf-8"))
            return {"mode": data.get("battle_mode_id"), "stage": data.get("stage_id")}
    return {"mode": None, "stage": None}


def layout_states(
    distribution: list[dict[str, Any]], labels: dict[str, Any], manifest: dict[str, Any]
) -> dict[str, Any]:
    """Cluster X centres per slot and test candidate observable layout predictors."""
    samples = {s["sample_id"]: s for s in manifest["samples"]}
    result: dict[str, Any] = {}
    for slot in SLOTS:
        rows = [r for r in distribution if r["slot"] == slot and not r["separate"]]
        groups = clusters([r["center_x"] for r in rows])
        cluster_of = {}
        for index, group in enumerate(groups):
            for row in rows:
                if group[0] <= row["center_x"] <= group[-1]:
                    cluster_of[row["sample_id"]] = index
        side = SLOTS[:4] if slot.startswith("a") else SLOTS[4:]
        other = SLOTS[4:] if slot.startswith("a") else SLOTS[:4]
        members = []
        for row in rows:
            sid = row["sample_id"]
            ident = match_identity(manifest["roots"], samples[sid]["match"])
            members.append(
                {
                    "sample_id": sid,
                    "cluster": cluster_of[sid],
                    "center_x": row["center_x"],
                    "group": row["group"],
                    "own_side_pattern": _pattern(labels[sid], side),
                    "other_side_pattern": _pattern(labels[sid], other),
                    "own_alive": sum(labels[sid][s] == "alive" for s in side),
                    "other_alive": sum(labels[sid][s] == "alive" for s in other),
                    "mode": ident["mode"],
                    "stage": ident["stage"],
                    "match": samples[sid]["match"],
                }
            )
        result[slot] = {
            "clusters": [{"min": g[0], "max": g[-1], "n": len(g)} for g in groups],
            "members": members,
            "predictors": {
                feature: _purity(members, feature)
                for feature in (
                    "own_side_pattern",
                    "other_side_pattern",
                    "own_alive",
                    "other_alive",
                    "mode",
                    "stage",
                    "match",
                )
            },
        }
    return result


def _pattern(row: dict[str, str], names: tuple[str, ...]) -> str:
    return "".join({"x": "X", "alive": ".", "?": "?"}[row[name]] for name in names)


def _purity(members: list[dict[str, Any]], feature: str) -> dict[str, Any]:
    """How well one feature value determines the cluster (1.0 = fully determined)."""
    by_value: dict[Any, Counter] = defaultdict(Counter)
    for member in members:
        by_value[member[feature]][member["cluster"]] += 1
    majority = sum(counter.most_common(1)[0][1] for counter in by_value.values())
    ambiguous = sum(len(counter) > 1 for counter in by_value.values())
    return {
        "purity": _ratio(majority, len(members)),
        "values": len(by_value),
        "values_spanning_clusters": ambiguous,
    }


def row_strips(
    frames: dict[str, np.ndarray], layout: dict[str, Any], slot: str, path: Path
) -> None:
    """Top-HUD strips grouped by X-centre cluster for one slot."""
    images = []
    for member in sorted(layout[slot]["members"], key=lambda m: m["center_x"]):
        strip = frames[member["sample_id"]][0:130, 380:1540].copy()
        cv2.putText(
            strip,
            f"{member['sample_id']} {slot} c{member['cluster']} x={member['center_x']}"
            f" {member['group']}",
            (5, 124),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )
        images.append(strip)
    write_sheet(images, path, f"{slot}: HUD rows ordered by X-centre cluster")


def render_changes(
    evaluator: Evaluator,
    results: list[dict[str, Any]],
    frames: dict[str, np.ndarray],
    config: AppConfig,
    output: Path,
) -> None:
    """Overlay every per-slot-sweep change at the smallest extension producing it."""
    first: dict[tuple[str, str], tuple[int, str]] = {}
    for result in results:
        right = result["right"]
        if len(right) != 1:
            continue
        ((slot, extension),) = right.items()
        for kind in ("recovered_fn", "new_fp"):
            for item in result["changes"][kind]:
                key = (item["sample_id"], item["slot"])
                if key not in first or extension < first[key][0]:
                    first[key] = (extension, kind)
    sheets: dict[str, list[np.ndarray]] = defaultdict(list)
    for (sid, slot), (extension, kind) in sorted(first.items()):
        case = evaluator.by_key[(sid, slot)]
        placement = evaluator.placement(case, extension)
        attribution = evaluator.attribute(case, placement)
        title = (
            f"{sid} {slot} {kind} first at R+{extension} {placement.name} "
            f"{placement.score:.4f} -> {attribution['category']}"
        )
        image = render_change(
            frames[sid],
            slot_boxes(config, frames[sid].shape),
            case,
            placement,
            extension,
            title,
        )
        cv2.imwrite(str(output / "overlays" / f"{kind}_{sid}_{slot}.png"), image)
        sheets[kind].append(image)
    for slot in SLOTS:
        alive = [c for c in evaluator.cases if c.slot == slot and c.human == "alive"]
        moved = [
            c
            for c in alive
            if evaluator.placement(c, MAX_EXT).score < evaluator.placement(c, 0).score
        ]
        if not moved:
            continue
        case = min(moved, key=lambda c: evaluator.placement(c, MAX_EXT).score)
        placement = evaluator.placement(case, MAX_EXT)
        title = (
            f"{case.sample_id} {slot} alive closest at R+{MAX_EXT} {placement.name} "
            f"{placement.score:.4f} (prod {evaluator.placement(case, 0).score:.4f}) -> "
            f"{evaluator.attribute(case, placement)['category']}"
        )
        sheets["alive_headroom"].append(
            render_change(
                frames[case.sample_id],
                slot_boxes(config, frames[case.sample_id].shape),
                case,
                placement,
                MAX_EXT,
                title,
            )
        )
    legend = (
        "green prod ROI | cyan extension | magenta winning mask | blue target centre"
        " | yellow target/neighbour divider | orange neighbour ROI"
    )
    for kind, images in sheets.items():
        write_sheet(images, output / "sheets" / f"{kind}.png", f"{kind}: {legend}")


def run(
    baseline: Path, video_dir: Path, output: Path, config_path: Path
) -> dict[str, Any]:
    """Measure reach, sweep extensions, render overlays and persist results."""
    config = load_config(config_path)
    templates = _load_masked_templates(config.vision.player_count.template_dir)
    cases, labels, manifest, frames = collect_cases(
        baseline, video_dir, config, templates
    )
    evaluator = Evaluator(cases, labels)
    mismatch = [
        (c.sample_id, c.slot)
        for c in cases
        if abs(evaluator.placement(c, 0).score - c.production_score) > 1e-6
    ]
    stored_diff = [
        {
            "sample_id": c.sample_id,
            "slot": c.slot,
            "stored": c.stored_label,
            "recomputed": evaluator.fixed[(c.sample_id, c.slot)],
        }
        for c in cases
        if c.stored_label != evaluator.fixed[(c.sample_id, c.slot)]
    ]
    distribution = x_distribution(cases)
    results = [evaluator.evaluate(candidate) for candidate in candidates()]
    layout = layout_states(distribution, labels, manifest)
    (output / "overlays").mkdir(parents=True, exist_ok=True)
    render_changes(evaluator, results, frames, config, output)
    distribution_plot(distribution, cases, output / "sheets" / "x_center_reach.png")
    for slot in ("a1", "o3"):
        row_strips(frames, layout, slot, output / "sheets" / f"layout_rows_{slot}.png")
    payload = {
        "schema_version": 1,
        "development_only": True,
        "threshold": THRESHOLD,
        "extensions": list(EXTENSIONS),
        "population": {
            "samples": len({c.sample_id for c in cases}),
            "determinable_slots": len(cases),
            "x_slots": sum(c.human == "x" for c in cases),
        },
        "production_equivalence_mismatches": mismatch,
        "stored_vs_recomputed_label_differences": stored_diff,
        "separate_cases": [list(item) for item in sorted(SEPARATE)],
        "x_distribution": distribution,
        "layout": layout,
        "fine_sweep": {slot: evaluator.fine_sweep(slot) for slot in SLOTS},
        "fn_min_extension": min_extension(evaluator),
        "candidates": results,
    }
    (output / "roi_reach.json").write_text(json.dumps(payload, indent=2) + "\n", "utf-8")
    return payload


def main() -> int:
    """Run Experiment 3."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--video-dir", type=Path, default=Path.home() / "Movies")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=default_config_path())
    args = parser.parse_args()
    run(args.baseline, args.video_dir, args.output, args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
