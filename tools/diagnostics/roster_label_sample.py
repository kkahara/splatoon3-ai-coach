"""Freeze and score blind gameplay-HUD roster labels.

``build`` selects gameplay detector frames by predeclared strata, decodes the
source videos, writes blind image sheets plus ``labels.csv``, and puts detector
outputs only in ``key.json``. ``score`` compares a *sealed* completed sheet
with that key. Review timeline lamps are never read.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
import roster_baseline as rb
from pydantic import BaseModel, Field

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.media.vision_manifest import load_vision_manifest
from splatoon3_ai_coach.vision.models import VisionFrameResult
from splatoon3_ai_coach.vision.roi import crop_roi

Stratum = Literal[
    "pre_death",
    "fused_change",
    "flicker",
    "margin",
    "hud_hidden",
    "steady",
]
Label = Literal["x", "alive", "?"]
VisibilityLabel = Literal["visible", "hidden", "?"]
STRATA: tuple[Stratum, ...] = (
    "pre_death",
    "fused_change",
    "flicker",
    "margin",
    "hud_hidden",
    "steady",
)
SLOT_NAMES = ("a1", "a2", "a3", "a4", "o1", "o2", "o3", "o4")
LABEL_FIELDS = ("sample_id", "hud_visible", *SLOT_NAMES, "notes")
VIDEO_SUFFIXES = (".mov", ".mp4", ".mkv", ".avi")


class BlindSample(BaseModel):
    """Public sample provenance without detector predictions."""

    sample_id: str
    image: str
    source_class: rb.SourceClass
    match: str
    video_time: float
    source_frame_id: str
    source_pts: int | None = None
    stratum: Stratum


class SampleManifest(BaseModel):
    """Frozen blind sample definition."""

    schema_version: int = 1
    seed: int
    requested_n: int
    selected_n: int
    config_path: str
    config_sha256: str
    roots: list[str]
    replay_roots: list[str]
    video_dir: str
    samples: list[BlindSample]
    skipped_analyses_without_video: list[str] = Field(default_factory=list)


class KeySample(BaseModel):
    """Hidden detector/fusion values for one blind sample."""

    sample_id: str
    source_class: rb.SourceClass
    stratum: Stratum
    raw_visible: bool
    raw_slots: list[Literal["x", "alive"]] | None
    raw_avb: list[int] | None
    fused_avb: list[int] | None
    pre_death: bool
    scores: list[float]
    edge_energy: list[float]
    decoded_video_time: float
    image_sha256: str


class SampleKey(BaseModel):
    """Frozen private predictions corresponding to ``SampleManifest``."""

    schema_version: int = 1
    seed: int
    samples: list[KeySample]


class ScoreGroup(BaseModel):
    """Accuracy and ambiguity counts for one fixed sample group."""

    samples: int = 0
    human_visible: int = 0
    human_hidden: int = 0
    human_visibility_ambiguous: int = 0
    human_determinable_slots: int = 0
    human_ambiguous_slots: int = 0
    detector_x_on_human_ambiguous: int = 0
    detector_alive_on_human_ambiguous: int = 0
    slot_tp_x: int = 0
    slot_fp_x: int = 0
    slot_fn_x: int = 0
    slot_tn_alive: int = 0
    side_count_comparable: int = 0
    side_count_exact: int = 0
    raw_avb_comparable: int = 0
    raw_avb_exact: int = 0
    fused_avb_comparable: int = 0
    fused_avb_exact: int = 0
    pre_death_fused_avb_comparable: int = 0
    pre_death_fused_avb_exact: int = 0
    visibility_tp: int = 0
    visibility_fp: int = 0
    visibility_fn: int = 0
    visibility_tn: int = 0
    human_determinable_rate: float = 0.0
    x_precision: float | None = None
    x_recall: float | None = None
    side_count_accuracy: float | None = None
    raw_avb_accuracy: float | None = None
    fused_avb_accuracy: float | None = None
    pre_death_fused_avb_accuracy: float | None = None
    visibility_precision: float | None = None
    visibility_recall: float | None = None


class LabeledArtifact(BaseModel):
    """Scored hand-label baseline; generated only after label sealing."""

    schema_version: int = 1
    labels_sha256: str
    overall: ScoreGroup
    by_source: dict[str, ScoreGroup]
    by_stratum: dict[str, ScoreGroup]
    notes: list[str]


class Candidate(BaseModel):
    """Internal selectable detector frame."""

    analysis_dir: str
    video_path: str
    source_class: rb.SourceClass
    frame_index: int
    frame_id: str
    video_time: float
    source_pts: int | None
    strata: list[Stratum]
    pre_death: bool
    raw_visible: bool
    raw_slots: list[Literal["x", "alive"]] | None
    raw_avb: list[int] | None
    fused_avb: list[int] | None
    scores: list[float]
    edge_energy: list[float]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_video(folder: Path, video_dir: Path) -> Path | None:
    """Resolve an analysis folder to a same-stem source video."""
    direct = [
        video_dir / f"{folder.name}{suffix}"
        for suffix in VIDEO_SUFFIXES
        if (video_dir / f"{folder.name}{suffix}").exists()
    ]
    if len(direct) == 1:
        return direct[0]
    matches = [
        path
        for path in video_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in VIDEO_SUFFIXES
        and path.stem == folder.name
    ]
    return matches[0] if len(matches) == 1 else None


def _frame_candidates(
    manifest_path: Path,
    video_path: Path,
    config: AppConfig,
    source_class: rb.SourceClass,
) -> list[Candidate]:
    manifest = load_vision_manifest(manifest_path)
    frames = sorted(manifest.frame_results, key=lambda item: item.timestamp)
    points = rb.raw_points(frames, config)
    point_by_time = {point.video_time: point for point in points}
    deaths = rb._death_times(manifest)
    pre_times = {
        _nearest_frame_time(
            frames,
            death - config.coach.death_factor_thresholds.roster_pre_death_offset_seconds,
        )
        for death in deaths
    }
    fused = rb.fuse(frames, config)
    fused_by_time = {
        item.timestamp: (
            [item.ally_alive_count, item.opponent_alive_count]
            if item.ally_alive_count is not None and item.opponent_alive_count is not None
            else None
        )
        for item in fused
    }
    fused_changes = _fused_change_times(fused_by_time)
    return _candidates_for_points(
        manifest_path,
        video_path,
        frames,
        point_by_time,
        pre_times,
        fused_changes,
        fused_by_time,
        config,
        source_class,
    )


def _nearest_frame_time(
    frames: Sequence[VisionFrameResult], target: float
) -> float | None:
    eligible = [
        frame.timestamp
        for frame in frames
        if rb._player_result(frame) is not None and frame.timestamp <= target
    ]
    return max(eligible) if eligible else None


def _fused_change_times(fused_by_time: dict[float, list[int] | None]) -> set[float]:
    changed: set[float] = set()
    previous: list[int] | None = None
    for video_time, avb in sorted(fused_by_time.items()):
        if avb is not None and previous is not None and avb != previous:
            changed.add(video_time)
        if avb is not None:
            previous = avb
    return changed


def _candidates_for_points(
    manifest_path,
    video_path,
    frames,
    point_by_time,
    pre_times,
    fused_changes,
    fused_by_time,
    config,
    source_class,
) -> list[Candidate]:
    roster_frames = [frame for frame in frames if frame.timestamp in point_by_time]
    out: list[Candidate] = []
    for index, frame in enumerate(roster_frames):
        point = point_by_time[frame.timestamp]
        strata = _strata(
            index, roster_frames, point_by_time, pre_times, fused_changes, config
        )
        if not strata:
            continue
        slots = _slots(point, config.vision.player_count.sqdiff_match_threshold)
        reading = rb._player_result(frame).reading
        out.append(
            Candidate(
                analysis_dir=str(manifest_path.parent),
                video_path=str(video_path),
                source_class=source_class,
                frame_index=index,
                frame_id=frame.frame_id,
                video_time=float(frame.timestamp),
                source_pts=frame.source_pts,
                strata=strata,
                pre_death=frame.timestamp in pre_times,
                raw_visible=point.visible,
                raw_slots=slots,
                raw_avb=list(point.avb) if point.avb else None,
                fused_avb=fused_by_time.get(frame.timestamp),
                scores=list(point.scores),
                edge_energy=list(getattr(reading, "slot_edge_energy", ())),
            )
        )
    return out


def _strata(index, frames, points, pre_times, fused_changes, config) -> list[Stratum]:
    frame, point = frames[index], points[frames[index].timestamp]
    result: list[Stratum] = []
    if frame.timestamp in pre_times:
        result.append("pre_death")
    if frame.timestamp in fused_changes:
        result.append("fused_change")
    if _is_flicker(index, frames, points):
        result.append("flicker")
    threshold = config.vision.player_count.sqdiff_match_threshold
    if point.scores and any(abs(score - threshold) <= 0.01 for score in point.scores):
        result.append("margin")
    if not point.visible:
        result.append("hud_hidden")
    if _is_steady(index, frames, points):
        result.append("steady")
    return result


def _is_flicker(index, frames, points) -> bool:
    if not 0 < index < len(frames) - 1:
        return False
    before = points[frames[index - 1].timestamp].avb
    middle = points[frames[index].timestamp].avb
    after = points[frames[index + 1].timestamp].avb
    return None not in (before, middle, after) and before == after != middle


def _is_steady(index, frames, points) -> bool:
    if not 0 < index < len(frames) - 1:
        return False
    values = [
        points[frames[item].timestamp].avb for item in (index - 1, index, index + 1)
    ]
    return values[0] is not None and values[0] == values[1] == values[2]


def _slots(
    point: rb.RawRosterPoint, threshold: float
) -> list[Literal["x", "alive"]] | None:
    if point.avb is None or len(point.scores) != 8:
        return None
    # Exact slot identity comes from scores, not count order.
    return ["x" if score <= threshold else "alive" for score in point.scores]


def collect_candidates(
    roots: Iterable[Path],
    replay_roots: Iterable[Path],
    video_dir: Path,
    config: AppConfig,
) -> tuple[list[Candidate], list[str]]:
    """Candidates from unique analyses whose source video is present."""
    candidates: list[Candidate] = []
    skipped: list[str] = []
    seen: set[str] = set()
    groups = ((roots, "live_gameplay"), (replay_roots, "replay_derived"))
    for grouped_roots, source_class in groups:
        for path in rb.find_manifests(grouped_roots):
            manifest = load_vision_manifest(path)
            if manifest.video_identity in seen:
                continue
            seen.add(manifest.video_identity)
            video = resolve_video(path.parent, video_dir)
            if video is None:
                skipped.append(str(path.parent))
                continue
            candidates.extend(_frame_candidates(path, video, config, source_class))
    return candidates, skipped


def select_candidates(
    candidates: Sequence[Candidate],
    n: int,
    seed: int,
    *,
    replay_n: int | None = None,
) -> list[tuple[Candidate, Stratum]]:
    """Seeded equal-quota strata with a predeclared replay sub-quota."""
    rng = random.Random(seed)
    pools = {
        stratum: [c for c in candidates if stratum in c.strata] for stratum in STRATA
    }
    for pool in pools.values():
        rng.shuffle(pool)
    base, extra = divmod(n, len(STRATA))
    replay_n = round(n / 5) if replay_n is None else replay_n
    replay_base, replay_extra = divmod(replay_n, len(STRATA))
    seen: set[tuple[str, str]] = set()
    replay_choices: dict[Stratum, list[Candidate]] = {}
    replay_order = sorted(
        enumerate(STRATA),
        key=lambda item: sum(
            candidate.source_class == "replay_derived" for candidate in pools[item[1]]
        ),
    )
    for index, stratum in replay_order:
        target = replay_base + (index < replay_extra)
        choices = [
            candidate
            for candidate in pools[stratum]
            if candidate.source_class == "replay_derived"
            and _candidate_id(candidate) not in seen
        ][:target]
        replay_choices[stratum] = choices
        seen.update(_candidate_id(candidate) for candidate in choices)
    selected: list[tuple[Candidate, Stratum]] = []
    for index, stratum in enumerate(STRATA):
        target = base + (index < extra)
        replay = replay_choices[stratum]
        live = [
            c
            for c in pools[stratum]
            if c.source_class == "live_gameplay" and _candidate_id(c) not in seen
        ][: target - len(replay)]
        choices = [*replay, *live]
        chosen_ids = {_candidate_id(item) for item in choices}
        if len(choices) < target:
            choices.extend(
                c
                for c in pools[stratum]
                if _candidate_id(c) not in seen and _candidate_id(c) not in chosen_ids
            )
        for candidate in choices[:target]:
            seen.add(_candidate_id(candidate))
            selected.append((candidate, stratum))
    remainder = [c for c in candidates if _candidate_id(c) not in seen]
    rng.shuffle(remainder)
    for candidate in remainder[: max(0, n - len(selected))]:
        assigned = candidate.strata[0]
        selected.append((candidate, assigned))
    rng.shuffle(selected)
    return selected[:n]


def _candidate_id(candidate: Candidate) -> tuple[str, str]:
    return candidate.analysis_dir, candidate.frame_id


def decode_frame(candidate: Candidate) -> tuple[np.ndarray, float]:
    """Decode one frame near its canonical analysis timestamp."""
    capture = cv2.VideoCapture(candidate.video_path)
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video: {candidate.video_path}")
    capture.set(cv2.CAP_PROP_POS_MSEC, candidate.video_time * 1000.0)
    ok, frame = capture.read()
    decoded = float(capture.get(cv2.CAP_PROP_POS_MSEC)) / 1000.0
    capture.release()
    if not ok or frame is None:
        raise RuntimeError(
            f"cannot decode {candidate.video_path} at {candidate.video_time:.3f}s"
        )
    return frame, decoded


def sample_image(frame: np.ndarray, config: AppConfig, sample_id: str) -> np.ndarray:
    """Blind context plus enlarged 8-slot strip; no detector result shown."""
    height, width = frame.shape[:2]
    context = frame[: max(1, int(height * 0.18)), :]
    target_width = 1280
    scale = target_width / context.shape[1]
    context = cv2.resize(
        context,
        (target_width, max(1, round(context.shape[0] * scale))),
        interpolation=cv2.INTER_AREA,
    )
    boxes = [
        *config.vision.player_count.ally_slots,
        *config.vision.player_count.opponent_slots,
    ]
    crops = [crop_roi(frame, box) for box in boxes]
    strip = _slot_strip(crops)
    header = np.full((48, target_width, 3), 245, dtype=np.uint8)
    cv2.putText(
        header,
        f"{sample_id}   A1 A2 A3 A4 | O1 O2 O3 O4",
        (16, 32),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (15, 15, 15),
        2,
        cv2.LINE_AA,
    )
    return np.vstack([header, context, strip])


def _slot_strip(crops: Sequence[np.ndarray]) -> np.ndarray:
    cells = []
    for name, crop in zip(SLOT_NAMES, crops, strict=True):
        resized = cv2.resize(crop, (144, 144), interpolation=cv2.INTER_CUBIC)
        cell = np.full((180, 154, 3), 245, dtype=np.uint8)
        cell[:144, 5:149] = resized
        cv2.putText(
            cell,
            name.upper(),
            (54, 170),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (20, 20, 20),
            2,
            cv2.LINE_AA,
        )
        cells.append(cell)
    strip = np.hstack(cells)
    return cv2.copyMakeBorder(
        strip, 0, 0, 0, max(0, 1280 - strip.shape[1]), cv2.BORDER_CONSTANT, value=245
    )


def build_sample(
    roots: list[Path],
    replay_roots: list[Path],
    video_dir: Path,
    out: Path,
    config_path: Path,
    *,
    n: int,
    seed: int,
) -> SampleManifest:
    """Freeze samples, blind sheets, label template and detector key."""
    if out.exists():
        raise SystemExit(f"{out} exists; sample is never overwritten")
    config = load_config(config_path)
    candidates, skipped = collect_candidates(roots, replay_roots, video_dir, config)
    selected = select_candidates(candidates, n, seed)
    if len(selected) < n:
        raise SystemExit(f"only {len(selected)} unique candidates for requested {n}")
    out.mkdir(parents=True)
    images = out / "images"
    images.mkdir()
    public: list[BlindSample] = []
    keys: list[KeySample] = []
    rendered: list[tuple[str, np.ndarray]] = []
    for index, (candidate, stratum) in enumerate(selected, start=1):
        sample_id = f"R{index:03d}"
        frame, decoded = decode_frame(candidate)
        image = sample_image(frame, config, sample_id)
        image_path = images / f"{sample_id}.png"
        cv2.imwrite(str(image_path), image)
        rendered.append((sample_id, image))
        public.append(_blind_sample(sample_id, image_path, candidate, stratum, out))
        keys.append(_key_sample(sample_id, candidate, stratum, decoded, image_path))
    _write_sheets(rendered, out / "sheets")
    manifest = SampleManifest(
        seed=seed,
        requested_n=n,
        selected_n=len(public),
        config_path=str(config_path),
        config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
        roots=[str(root) for root in roots],
        replay_roots=[str(root) for root in replay_roots],
        video_dir=str(video_dir),
        samples=public,
        skipped_analyses_without_video=skipped,
    )
    _write_json(out / "sample_manifest.json", manifest)
    _write_json(out / "key.json", SampleKey(seed=seed, samples=keys))
    _write_labels(out / "labels.csv", public)
    _write_instructions(out / "LABELING.md", manifest)
    return manifest


def _blind_sample(sample_id, image_path, candidate, stratum, out) -> BlindSample:
    return BlindSample(
        sample_id=sample_id,
        image=str(image_path.relative_to(out)),
        source_class=candidate.source_class,
        match=Path(candidate.analysis_dir).name,
        video_time=candidate.video_time,
        source_frame_id=candidate.frame_id,
        source_pts=candidate.source_pts,
        stratum=stratum,
    )


def _key_sample(sample_id, candidate, stratum, decoded, image_path) -> KeySample:
    return KeySample(
        sample_id=sample_id,
        source_class=candidate.source_class,
        stratum=stratum,
        raw_visible=candidate.raw_visible,
        raw_slots=candidate.raw_slots,
        raw_avb=candidate.raw_avb,
        fused_avb=candidate.fused_avb,
        pre_death=candidate.pre_death,
        scores=candidate.scores,
        edge_energy=candidate.edge_energy,
        decoded_video_time=decoded,
        image_sha256=_sha256(image_path),
    )


def _write_json(path: Path, model: BaseModel) -> None:
    path.write_text(json.dumps(model.model_dump(mode="json"), indent=2) + "\n", "utf-8")


def _write_labels(path: Path, samples: Sequence[BlindSample]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LABEL_FIELDS, lineterminator="\n")
        writer.writeheader()
        for sample in samples:
            writer.writerow({"sample_id": sample.sample_id})


def _write_instructions(path: Path, manifest: SampleManifest) -> None:
    path.write_text(
        "# Gameplay HUD roster labels\n\n"
        f"Frozen sample: {manifest.selected_n} frames, seed {manifest.seed}.\n\n"
        "For each row in `labels.csv`, inspect `images/<sample_id>.png` or the "
        "contact sheets. Set `hud_visible` to `visible`, `hidden`, or `?`. "
        "Set each `a1..a4` and `o1..o4` to `x`, `alive`, or `?`. Use `?` "
        "whenever you cannot determine the slot; ambiguity is a result, not an "
        "error. Do not open `key.json` before labels are sealed.\n\n"
        "A = ally row, O = opponent row, slots left-to-right. Do not add, "
        "delete, reorder, or replace samples.\n",
        "utf-8",
    )


def _write_sheets(rendered: Sequence[tuple[str, np.ndarray]], folder: Path) -> None:
    folder.mkdir()
    for start in range(0, len(rendered), 10):
        batch = rendered[start : start + 10]
        image = np.vstack([item[1] for item in batch])
        cv2.imwrite(str(folder / f"sheet_{start // 10 + 1:02d}.jpg"), image)


def read_labels(path: Path) -> dict[str, dict[str, str]]:
    """Validate completed labels without coercing ambiguity."""
    with path.open(newline="", encoding="utf-8") as handle:
        rows = {row["sample_id"]: row for row in csv.DictReader(handle)}
    for sample_id, row in rows.items():
        if row["hud_visible"] not in {"visible", "hidden", "?"}:
            raise ValueError(f"{sample_id}: invalid hud_visible")
        for slot in SLOT_NAMES:
            if row[slot] not in {"x", "alive", "?"}:
                raise ValueError(f"{sample_id}: invalid {slot}")
    return rows


def score_labels(labels_path: Path, key_path: Path) -> LabeledArtifact:
    """Score only a complete frozen label sheet against its private key."""
    labels = read_labels(labels_path)
    key = SampleKey.model_validate_json(key_path.read_text("utf-8"))
    expected = {item.sample_id for item in key.samples}
    if set(labels) != expected:
        raise ValueError("labels sample IDs differ from frozen key")
    groups: dict[str, list[tuple[KeySample, dict[str, str]]]] = defaultdict(list)
    pairs = [(item, labels[item.sample_id]) for item in key.samples]
    groups["overall"] = pairs
    for item, row in pairs:
        groups[f"source:{item.source_class}"].append((item, row))
        groups[f"stratum:{item.stratum}"].append((item, row))
    scored = {name: _score_group(items) for name, items in groups.items()}
    return LabeledArtifact(
        labels_sha256=_sha256(labels_path),
        overall=scored["overall"],
        by_source={
            name.removeprefix("source:"): value
            for name, value in scored.items()
            if name.startswith("source:")
        },
        by_stratum={
            name.removeprefix("stratum:"): value
            for name, value in scored.items()
            if name.startswith("stratum:")
        },
        notes=[
            "Question-mark labels are human ambiguity, never errors.",
            "Review timeline lamps and alignment are not inputs.",
        ],
    )


def _score_group(items: Sequence[tuple[KeySample, dict[str, str]]]) -> ScoreGroup:
    counts = Counter()
    counts["samples"] = len(items)
    for key, row in items:
        _score_visibility(counts, key, row)
        _score_slots(counts, key, row)
        _score_counts(counts, key, row)
    return _finalize(counts)


def _score_visibility(counts: Counter, key: KeySample, row: dict[str, str]) -> None:
    human = row["hud_visible"]
    counts[f"human_{human}" if human != "?" else "human_visibility_ambiguous"] += 1
    if human == "?":
        return
    predicted = key.raw_visible
    actual = human == "visible"
    counts["visibility_tp"] += predicted and actual
    counts["visibility_fp"] += predicted and not actual
    counts["visibility_fn"] += not predicted and actual
    counts["visibility_tn"] += not predicted and not actual


def _score_slots(counts: Counter, key: KeySample, row: dict[str, str]) -> None:
    for index, slot in enumerate(SLOT_NAMES):
        human = row[slot]
        predicted = key.raw_slots[index] if key.raw_slots is not None else None
        if human == "?":
            counts["human_ambiguous_slots"] += 1
            if predicted:
                counts[f"detector_{predicted}_on_human_ambiguous"] += 1
            continue
        counts["human_determinable_slots"] += 1
        if predicted is None:
            continue
        counts["slot_tp_x"] += predicted == human == "x"
        counts["slot_fp_x"] += predicted == "x" and human == "alive"
        counts["slot_fn_x"] += predicted == "alive" and human == "x"
        counts["slot_tn_alive"] += predicted == human == "alive"


def _score_counts(counts: Counter, key: KeySample, row: dict[str, str]) -> None:
    human_avb = _human_avb(row)
    for side, indexes in (("ally", range(4)), ("opponent", range(4, 8))):
        values = [row[SLOT_NAMES[index]] for index in indexes]
        if "?" not in values and key.raw_avb is not None:
            expected = sum(value == "alive" for value in values)
            predicted = key.raw_avb[0 if side == "ally" else 1]
            counts["side_count_comparable"] += 1
            counts["side_count_exact"] += expected == predicted
    if human_avb is None:
        return
    _avb_hit(counts, "raw_avb", key.raw_avb, human_avb)
    _avb_hit(counts, "fused_avb", key.fused_avb, human_avb)
    if key.pre_death:
        _avb_hit(counts, "pre_death_fused_avb", key.fused_avb, human_avb)


def _human_avb(row: dict[str, str]) -> list[int] | None:
    values = [row[name] for name in SLOT_NAMES]
    if "?" in values:
        return None
    return [
        sum(value == "alive" for value in values[:4]),
        sum(value == "alive" for value in values[4:]),
    ]


def _avb_hit(counts: Counter, prefix: str, predicted, expected) -> None:
    if predicted is None:
        return
    counts[f"{prefix}_comparable"] += 1
    counts[f"{prefix}_exact"] += list(predicted) == list(expected)


def _finalize(counts: Counter) -> ScoreGroup:
    values = {
        name: int(counts[name])
        for name in ScoreGroup.model_fields
        if name
        not in {
            "human_determinable_rate",
            "x_precision",
            "x_recall",
            "side_count_accuracy",
            "raw_avb_accuracy",
            "fused_avb_accuracy",
            "pre_death_fused_avb_accuracy",
            "visibility_precision",
            "visibility_recall",
        }
    }
    slots = counts["human_determinable_slots"] + counts["human_ambiguous_slots"]
    values["human_determinable_rate"] = (
        _ratio_or_none(counts["human_determinable_slots"], slots) or 0.0
    )
    values["x_precision"] = _ratio_or_none(
        counts["slot_tp_x"], counts["slot_tp_x"] + counts["slot_fp_x"]
    )
    values["x_recall"] = _ratio_or_none(
        counts["slot_tp_x"], counts["slot_tp_x"] + counts["slot_fn_x"]
    )
    for prefix in ("side_count", "raw_avb", "fused_avb", "pre_death_fused_avb"):
        values[f"{prefix}_accuracy"] = _ratio_or_none(
            counts[f"{prefix}_exact"], counts[f"{prefix}_comparable"]
        )
    values["visibility_precision"] = _ratio_or_none(
        counts["visibility_tp"], counts["visibility_tp"] + counts["visibility_fp"]
    )
    values["visibility_recall"] = _ratio_or_none(
        counts["visibility_tp"], counts["visibility_tp"] + counts["visibility_fn"]
    )
    return ScoreGroup.model_validate(values)


def _ratio_or_none(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def labeled_markdown(artifact: LabeledArtifact) -> str:
    """Human-readable reliability ladder, preserving source separation."""
    lines = [
        "# Gameplay HUD roster hand-labeled baseline",
        "",
        f"- Labels sha256: `{artifact.labels_sha256}`",
        "- `?` is human ambiguity, not an incorrect label.",
        "",
        *_group_markdown("Overall", artifact.overall),
    ]
    for name, group in artifact.by_source.items():
        lines.extend(_group_markdown(f"Source: {name}", group))
    for name, group in artifact.by_stratum.items():
        lines.extend(_group_markdown(f"Stratum: {name}", group))
    return "\n".join(lines) + "\n"


def _group_markdown(title: str, group: ScoreGroup) -> list[str]:
    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.1%}"

    return [
        f"## {title}",
        "",
        f"- Samples: {group.samples}",
        f"- Human-determinable slots: {group.human_determinable_slots} / "
        f"{group.human_determinable_slots + group.human_ambiguous_slots} "
        f"({group.human_determinable_rate:.1%})",
        f"- Detector X / alive on ambiguous slots: "
        f"{group.detector_x_on_human_ambiguous} / "
        f"{group.detector_alive_on_human_ambiguous}",
        f"- Slot X precision / recall: {pct(group.x_precision)} / {pct(group.x_recall)}",
        f"- Exact side count: {pct(group.side_count_accuracy)} "
        f"({group.side_count_exact}/{group.side_count_comparable})",
        f"- Exact raw AvB: {pct(group.raw_avb_accuracy)} "
        f"({group.raw_avb_exact}/{group.raw_avb_comparable})",
        f"- Exact fused AvB: {pct(group.fused_avb_accuracy)} "
        f"({group.fused_avb_exact}/{group.fused_avb_comparable})",
        f"- Exact pre-death fused AvB: {pct(group.pre_death_fused_avb_accuracy)} "
        f"({group.pre_death_fused_avb_exact}/"
        f"{group.pre_death_fused_avb_comparable})",
        f"- Visibility precision / recall: {pct(group.visibility_precision)} / "
        f"{pct(group.visibility_recall)}",
        "",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--root", action="append", type=Path, default=[])
    build.add_argument("--replay-root", action="append", type=Path, default=[])
    build.add_argument("--video-dir", type=Path, default=Path.home() / "Movies")
    build.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT
        / "analysis"
        / "importance_report"
        / "roster_evidence"
        / "baseline"
        / "labels",
    )
    build.add_argument("--n", type=int, default=120)
    build.add_argument("--seed", type=int, default=20261014)
    build.add_argument("--config", type=Path, default=default_config_path())
    score = sub.add_parser("score")
    score.add_argument("--labels", type=Path, required=True)
    score.add_argument("--key", type=Path, required=True)
    score.add_argument("--json", type=Path, required=True)
    score.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        roots = args.root or [
            PROJECT_ROOT / "analysis" / "_rescore_v2",
            PROJECT_ROOT / "analysis" / "_sz_corpus_v1",
        ]
        default_replay = (
            PROJECT_ROOT / "analysis" / "en-preview-full-fast-2026-10-04 21-32-55"
        )
        replay_roots = args.replay_root or (
            [default_replay] if default_replay.exists() else []
        )
        build_sample(
            [root.resolve() for root in roots],
            [root.resolve() for root in replay_roots],
            args.video_dir.resolve(),
            args.out.resolve(),
            args.config.resolve(),
            n=args.n,
            seed=args.seed,
        )
        return 0
    artifact = score_labels(args.labels, args.key)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    _write_json(args.json, artifact)
    args.markdown.write_text(labeled_markdown(artifact), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
