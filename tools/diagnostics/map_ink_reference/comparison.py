"""Frozen A1-vs-v3 comparison core (unblinded; only after labels and gate seal).

Applies the production classifier with each pinned calibration to the
extracted frames, maps sealed neutral labels to reference classes, and
computes per-cluster discordance, ``q_all`` and the per-look verdicts.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from map_ink_reference.constants import (
    CANT_TELL,
    MIN_COMBINED_FRAMES,
    MIN_JUDGEABLE_FRAMES_STAGE1,
    MIN_JUDGEABLE_POINTS_PER_FRAME,
    MIN_STAGE2_ADDED_FRAMES,
)
from map_ink_reference.errors import MalformedInput
from map_ink_reference.mapping import ALLY, OPPONENT, OTHER, reference_class
from map_ink_reference.statistics import SignFlipResult, sign_flip
from map_ink_reference.verdicts import INCONCLUSIVE, NOT_EVALUABLE, look_verdict
from splatoon3_ai_coach.config.models import MapInkAnalyzerConfig
from splatoon3_ai_coach.vision.map_ink import MapInkClassifier
from splatoon3_ai_coach.vision.team_color_calibration import TeamColorCalibrationResult

_CLASS_NAMES = (ALLY, OPPONENT, OTHER)


def calibrated_classifier(
    config: MapInkAnalyzerConfig, calibration_payload: Mapping[str, Any] | None
) -> MapInkClassifier:
    """Production classifier with one pinned calibration applied."""
    if not calibration_payload:
        raise MalformedInput("correctness-applicable recording has no calibration")
    classifier = MapInkClassifier(config)
    classifier.apply_calibration(
        TeamColorCalibrationResult.from_dict(dict(calibration_payload))
    )
    return classifier


def class_map(image: np.ndarray, classifier: MapInkClassifier) -> np.ndarray:
    """Per-pixel production class index (0 ally, 1 opponent, 2 other)."""
    ally, opponent, other = classifier.classify_bgr(image)
    if np.any(ally & opponent) or not np.all(ally | opponent | other):
        raise MalformedInput("classifier masks are not a partition")
    out = np.full(image.shape[:2], 2, dtype=np.uint8)
    out[opponent] = 1
    out[ally] = 0
    return out


@dataclass(frozen=True)
class LabelledPoint:
    """A sampled point with every recorded copy of its label."""

    point_id: str
    frame_id: str
    x: int
    y: int
    copies: tuple[str | None, ...]

    @property
    def judgeable_label(self) -> str | None:
        """Agreed non-cant_tell label, else None (unjudgeable)."""
        if not self.copies or any(c is None for c in self.copies):
            return None
        if len(set(self.copies)) != 1 or self.copies[0] == CANT_TELL:
            return None
        return self.copies[0]


@dataclass
class FrameOutcome:
    """Per-cluster (frame) counts at one look."""

    frame_id: str
    judgeable_points: int
    b: int = 0
    c: int = 0
    a1_correct: int = 0
    v3_correct: int = 0
    a1_inverted: int = 0
    v3_inverted: int = 0
    team_points: int = 0
    points: list[dict[str, Any]] = field(default_factory=list)

    @property
    def judgeable(self) -> bool:
        """At least 6 judgeable points."""
        return self.judgeable_points >= MIN_JUDGEABLE_POINTS_PER_FRAME

    @property
    def d(self) -> int:
        """``c - b``."""
        return self.c - self.b


def _inverted(reference: str, produced: str) -> bool:
    return {reference, produced} == {ALLY, OPPONENT}


def frame_outcome(
    frame_id: str,
    points: Sequence[LabelledPoint],
    own_swatch: str,
    a1: np.ndarray,
    v3: np.ndarray,
) -> FrameOutcome:
    """b/c counts and descriptive tallies over the frame's judgeable points."""
    judgeable = [p for p in points if p.judgeable_label is not None]
    out = FrameOutcome(frame_id, len(judgeable))
    for point in judgeable:
        ref = reference_class(point.judgeable_label or "", own_swatch)
        got_a1 = _CLASS_NAMES[int(a1[point.y, point.x])]
        got_v3 = _CLASS_NAMES[int(v3[point.y, point.x])]
        a1_ok, v3_ok = got_a1 == ref, got_v3 == ref
        out.b += int(v3_ok and not a1_ok)
        out.c += int(a1_ok and not v3_ok)
        out.a1_correct += int(a1_ok)
        out.v3_correct += int(v3_ok)
        if ref in (ALLY, OPPONENT):
            out.team_points += 1
            out.a1_inverted += int(_inverted(ref, got_a1))
            out.v3_inverted += int(_inverted(ref, got_v3))
        out.points.append(
            {"point_id": point.point_id, "reference": ref, "a1": got_a1, "v3": got_v3}
        )
    return out


def q_all(
    images: Iterable[np.ndarray],
    domain: Callable[[np.ndarray], np.ndarray],
    a1: MapInkClassifier,
    v3: MapInkClassifier,
) -> tuple[int, int]:
    """(differing, total) candidate pixels over the whole eligible population."""
    differing = total = 0
    for image in images:
        mask = domain(image)
        diff = (class_map(image, a1) != class_map(image, v3)) & mask
        differing += int(np.count_nonzero(diff))
        total += int(np.count_nonzero(mask))
    return differing, total


def identity_holds(differing: int, total: int) -> bool:
    """Identity iff q_all = 0; an empty population is not identity (fail closed)."""
    return total > 0 and differing == 0


def stage1_verdict(
    *,
    swatch_valid: bool,
    own_swatch: str | None,
    frames: Sequence[FrameOutcome],
    identity: bool,
) -> tuple[str, SignFlipResult | None]:
    """Stage 1 look for one recording."""
    judgeable = [f for f in frames if f.judgeable]
    ok = (
        swatch_valid
        and own_swatch is not None
        and len(judgeable) >= MIN_JUDGEABLE_FRAMES_STAGE1
    )
    test = None if (not ok or identity) else sign_flip([f.d for f in judgeable])
    return look_verdict(ok, identity, test), test


def stage2_verdict(
    *,
    own_swatch_all: str | None,
    stage1_frames: Sequence[FrameOutcome],
    stage2_frames: Sequence[FrameOutcome],
    identity: bool,
) -> tuple[str, SignFlipResult | None]:
    """Stage 2 look for a Stage 1 INCONCLUSIVE recording (final)."""
    if own_swatch_all is None:
        return NOT_EVALUABLE, None
    added = [f for f in stage2_frames if f.judgeable]
    if len(added) < MIN_STAGE2_ADDED_FRAMES:
        return INCONCLUSIVE, None
    combined = [f for f in stage1_frames if f.judgeable] + added
    ok = len(combined) >= MIN_COMBINED_FRAMES
    test = None if (not ok or identity) else sign_flip([f.d for f in combined])
    return look_verdict(ok, identity, test), test
