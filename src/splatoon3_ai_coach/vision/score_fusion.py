"""Splat Zones score and penalty fusion (left→ally, right→opponent).

Readings stay screen left/right; team mapping, hold, plausibility and the
battle-mode gate live here, never in ``ScoreDetector``. Nothing is
interpolated and no ``GameEvent`` is emitted. See
``analysis/score_survey/STAGE_3_2_NOTE.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from splatoon3_ai_coach.config.models import ScoreDetectorConfig
from splatoon3_ai_coach.vision.match_intro import MatchIdentityTracker
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    MatchIntroReading,
    MatchPhase,
    PenaltyQuality,
    ScoreQuality,
    ScoreReading,
    ScoreSideReading,
    VisionFrameResult,
)


@dataclass
class _ScoreSideMemory:
    """Last accepted observation for one score side (ally or opponent)."""

    value: int | None = None
    last_at: float | None = None
    evidence_ids: list[str] = field(default_factory=list)
    pending: int | None = None
    pending_count: int = 0
    pending_at: float | None = None
    pending_ids: list[str] = field(default_factory=list)


@dataclass
class _PenaltySideMemory:
    """Last observed ``+N`` for one side, for the short visibility hold."""

    value: int | None = None
    last_at: float | None = None
    evidence_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScorePlausibility:
    """Splat Zones trajectory rules applied in fusion (off = Stage 3.1)."""

    enabled: bool = False
    max_drop_per_second: float = 2.0
    drop_slack: int = 2
    confirm_readings: int = 3

    @classmethod
    def from_config(cls, config: ScoreDetectorConfig) -> ScorePlausibility:
        """Build rules from ``vision.score`` settings."""
        return cls(
            enabled=config.score_plausibility_enabled,
            max_drop_per_second=config.score_max_drop_per_second,
            drop_slack=config.score_drop_slack,
            confirm_readings=config.score_confirm_readings,
        )

    def allowed_drop(self, gap_seconds: float) -> float:
        """Largest drop accepted without confirmation after ``gap_seconds``."""
        return self.drop_slack + self.max_drop_per_second * max(gap_seconds, 0.0)


@dataclass(frozen=True)
class ScoreFrameFusion:
    """Fused score + penalty state for one frame."""

    ally_remaining: int | None = None
    ally_score_quality: ScoreQuality = "unknown"
    opponent_remaining: int | None = None
    opponent_score_quality: ScoreQuality = "unknown"
    ally_penalty: int | None = None
    ally_penalty_quality: PenaltyQuality = "unknown"
    opponent_penalty: int | None = None
    opponent_penalty_quality: PenaltyQuality = "unknown"
    evidence_ids: list[str] = field(default_factory=list)

    @property
    def asserts_anything(self) -> bool:
        """True when any score or penalty value is asserted this frame."""
        return any(
            v is not None
            for v in (
                self.ally_remaining,
                self.opponent_remaining,
                self.ally_penalty,
                self.opponent_penalty,
            )
        )


def resolve_battle_mode(frame_results: list[VisionFrameResult]) -> str | None:
    """Battle mode latched from stored ``match_intro`` readings.

    Same latch rule as ``match_identity.json`` (best template score before
    resolution), replayed from ``frame_results`` so ``refuse`` needs no sidecar.
    """
    tracker = MatchIdentityTracker(intro_deadline_seconds=float("inf"))
    for frame in sorted(frame_results, key=lambda item: item.timestamp):
        for detection in frame.detections:
            if isinstance(detection.reading, MatchIntroReading):
                tracker.update(detection.reading, video_time=frame.timestamp)
    return tracker.identity.battle_mode_id


def fuse_score_sides_at(
    *,
    timestamp: float,
    match_phase: MatchPhase,
    reading: ScoreReading | None,
    reading_usable: bool,
    evidence_id: str | None,
    max_hold_seconds: float,
    ally_mem: _ScoreSideMemory,
    opponent_mem: _ScoreSideMemory,
    plausibility: ScorePlausibility | None = None,
) -> tuple[
    int | None,
    ScoreQuality,
    int | None,
    ScoreQuality,
    list[str],
    _ScoreSideMemory,
    _ScoreSideMemory,
]:
    """Pure per-side score fusion (left→ally, right→opponent).

    Hold last observation only — never invent unobserved values.
    Outside ``in_match``, both sides clear to unknown.
    """
    if match_phase != "in_match":
        cleared = _ScoreSideMemory()
        return None, "unknown", None, "unknown", [], cleared, _ScoreSideMemory()

    rules = plausibility or ScorePlausibility()
    left_side = reading.left if reading_usable and reading is not None else None
    right_side = reading.right if reading_usable and reading is not None else None
    ally_val, ally_q, ally_mem = _fuse_one_score_side(
        timestamp, left_side, ally_mem, evidence_id, max_hold_seconds, rules
    )
    opp_val, opp_q, opponent_mem = _fuse_one_score_side(
        timestamp, right_side, opponent_mem, evidence_id, max_hold_seconds, rules
    )
    ids: list[str] = []
    if ally_q == "observed" or opp_q == "observed":
        if evidence_id:
            ids = [evidence_id]
    elif ally_q == "held" or opp_q == "held":
        held_ids: list[str] = []
        if ally_q == "held":
            held_ids.extend(ally_mem.evidence_ids)
        if opp_q == "held":
            held_ids.extend(opponent_mem.evidence_ids)
        ids = list(dict.fromkeys(held_ids))
    return ally_val, ally_q, opp_val, opp_q, ids, ally_mem, opponent_mem


def _fuse_one_score_side(
    timestamp: float,
    side: ScoreSideReading | None,
    mem: _ScoreSideMemory,
    evidence_id: str | None,
    max_hold_seconds: float,
    rules: ScorePlausibility,
) -> tuple[int | None, ScoreQuality, _ScoreSideMemory]:
    """Observe, reject or hold one counter; never interpolate."""
    if side is not None and side.visible and side.value is not None:
        ids = [evidence_id] if evidence_id else []
        value = int(side.value)
        if not rules.enabled or _plausible(mem, value, timestamp, rules):
            return value, "observed", _accepted(value, timestamp, ids)
        if mem.value is not None and value < mem.value:
            _clear_pending(mem)
            return None, "rejected_implausible", mem
        if _confirm_pending(mem, value, timestamp, ids, rules):
            return (
                int(side.value),
                "observed",
                _accepted(side.value, timestamp, mem.pending_ids),
            )
        return None, "rejected_implausible", mem

    if rules.enabled:
        _clear_pending(mem)
    if (
        mem.value is not None
        and mem.last_at is not None
        and timestamp - mem.last_at <= max_hold_seconds
    ):
        return mem.value, "held", mem
    return None, "unknown", mem


def _accepted(value: int, timestamp: float, ids: list[str]) -> _ScoreSideMemory:
    """Fresh memory for a newly accepted observation."""
    return _ScoreSideMemory(value=int(value), last_at=timestamp, evidence_ids=list(ids))


def _plausible(
    mem: _ScoreSideMemory, value: int, timestamp: float, rules: ScorePlausibility
) -> bool:
    """Fits the accepted trajectory: never up, never faster than allowed."""
    if mem.value is None or mem.last_at is None:
        return False
    if value > mem.value:
        return False
    return mem.value - value <= rules.allowed_drop(timestamp - mem.last_at)


def _confirm_pending(
    mem: _ScoreSideMemory,
    value: int,
    timestamp: float,
    ids: list[str],
    rules: ScorePlausibility,
) -> bool:
    """Track an unconfirmed seed or rise; True once enough agreeing readings arrive.

    Only the first value of a match and rises above the accepted value (a
    wrong low value being corrected) are confirmable. Drops faster than the
    counter can tick are never confirmed: sustained highlighted-pod misreads
    (1 / 11) are consecutive too. Agreeing = consecutive readings that
    themselves step plausibly from the pending value.
    """
    agrees = (
        mem.pending is not None
        and mem.pending_at is not None
        and value <= mem.pending
        and mem.pending - value <= rules.allowed_drop(timestamp - mem.pending_at)
    )
    if agrees:
        mem.pending_count += 1
        mem.pending_ids.extend(ids)
    else:
        mem.pending_count, mem.pending_ids = 1, list(ids)
    mem.pending, mem.pending_at = value, timestamp
    return mem.pending_count >= rules.confirm_readings


def _clear_pending(mem: _ScoreSideMemory) -> None:
    """Drop any unconfirmed jump (a gap breaks consecutiveness)."""
    mem.pending, mem.pending_count, mem.pending_at, mem.pending_ids = None, 0, None, []


def fuse_penalty_side(
    *,
    timestamp: float,
    value: int | None,
    main_visible: bool,
    evaluated: bool,
    mem: _PenaltySideMemory,
    evidence_id: str | None,
    max_hold_seconds: float,
) -> tuple[int | None, PenaltyQuality]:
    """Observe, mark ``not_shown``, or briefly hold one side's ``+N``.

    ``not_shown`` needs the penalty region inspected while that side's main
    counter was visible; it clears the hold because the ``+N`` may really
    have gone. It is never reported as zero.
    """
    if value is not None:
        mem.value, mem.last_at = int(value), timestamp
        mem.evidence_ids = [evidence_id] if evidence_id else []
        return int(value), "observed"
    if evaluated and main_visible:
        mem.value, mem.last_at, mem.evidence_ids = None, None, []
        return None, "not_shown"
    if (
        mem.value is not None
        and mem.last_at is not None
        and timestamp - mem.last_at <= max_hold_seconds
    ):
        return mem.value, "held"
    return None, "unknown"


class ScoreFrameFuser:
    """Per-match score + penalty fusion, gated on the Splat Zones battle mode."""

    def __init__(self, config: ScoreDetectorConfig, battle_mode_id: str | None) -> None:
        self.config = config
        self.enabled = (
            battle_mode_id is not None and battle_mode_id == config.battle_mode_id
        )
        self.plausibility = ScorePlausibility.from_config(config)
        self._reset()

    def _reset(self) -> None:
        """Forget all accepted observations (outside ``in_match``)."""
        self.ally = _ScoreSideMemory()
        self.opponent = _ScoreSideMemory()
        self.ally_penalty = _PenaltySideMemory()
        self.opponent_penalty = _PenaltySideMemory()

    def step(self, frame: VisionFrameResult, match_phase: MatchPhase) -> ScoreFrameFusion:
        """Fuse one frame; non-Splat-Zones or out-of-match is all unknown."""
        if not self.enabled:
            return ScoreFrameFusion()
        if match_phase != "in_match":
            self._reset()
            return ScoreFrameFusion()
        best = _best_score(frame)
        reading = best.reading if best is not None else None
        assert reading is None or isinstance(reading, ScoreReading)
        usable = best is not None and best.confidence >= self.config.min_usable_confidence
        evidence_id = best.id if best is not None else None
        a, aq, o, oq, ids, self.ally, self.opponent = fuse_score_sides_at(
            timestamp=frame.timestamp,
            match_phase=match_phase,
            reading=reading,
            reading_usable=usable,
            evidence_id=evidence_id if usable else None,
            max_hold_seconds=self.config.score_max_hold_seconds,
            ally_mem=self.ally,
            opponent_mem=self.opponent,
            plausibility=self.plausibility,
        )
        ap, apq = self._penalty(frame.timestamp, reading, usable, "left", evidence_id)
        op, opq = self._penalty(frame.timestamp, reading, usable, "right", evidence_id)
        penalty_ids = _penalty_ids(
            apq, opq, evidence_id, self.ally_penalty, self.opponent_penalty
        )
        return ScoreFrameFusion(
            ally_remaining=a,
            ally_score_quality=aq,
            opponent_remaining=o,
            opponent_score_quality=oq,
            ally_penalty=ap,
            ally_penalty_quality=apq,
            opponent_penalty=op,
            opponent_penalty_quality=opq,
            evidence_ids=list(dict.fromkeys([*ids, *penalty_ids])),
        )

    def _penalty(
        self,
        timestamp: float,
        reading: ScoreReading | None,
        main_usable: bool,
        side: str,
        evidence_id: str | None,
    ) -> tuple[int | None, PenaltyQuality]:
        """Fuse one side's penalty; readable even when the main read is not."""
        mem = self.ally_penalty if side == "left" else self.opponent_penalty
        value = None
        main_visible = False
        evaluated = False
        if reading is not None:
            value = reading.left_penalty if side == "left" else reading.right_penalty
            main_side = reading.left if side == "left" else reading.right
            main_visible = main_usable and main_side.visible
            evaluated = reading.penalty_evaluated
        return fuse_penalty_side(
            timestamp=timestamp,
            value=value,
            main_visible=main_visible,
            evaluated=evaluated,
            mem=mem,
            evidence_id=evidence_id,
            max_hold_seconds=self.config.score_max_hold_seconds,
        )


def retract_contradicted_scores(
    fusions: list[ScoreFrameFusion],
    match_phases: list[MatchPhase],
    slack: int,
) -> list[ScoreFrameFusion]:
    """Backward pass: retract values contradicted by a later accepted observation.

    Counters never rise, so a fused value more than ``slack`` below any later
    observed value in the same match was a misread (typically a highlighted
    pod read as 1 / 11 after a long gap). Observed → ``rejected_implausible``,
    held → ``unknown``; later values are never altered by earlier ones.
    """
    out = list(fusions)
    for value_key, quality_key in (
        ("ally_remaining", "ally_score_quality"),
        ("opponent_remaining", "opponent_score_quality"),
    ):
        floor: int | None = None
        for i in range(len(out) - 1, -1, -1):
            if match_phases[i] != "in_match":
                floor = None
                continue
            value = getattr(out[i], value_key)
            quality = getattr(out[i], quality_key)
            if value is None:
                continue
            if floor is not None and value < floor - slack:
                retracted = "rejected_implausible" if quality == "observed" else "unknown"
                out[i] = replace(out[i], **{value_key: None, quality_key: retracted})
            elif quality == "observed":
                floor = value if floor is None else max(floor, value)
    return out


def _penalty_ids(
    ally_q: PenaltyQuality,
    opponent_q: PenaltyQuality,
    evidence_id: str | None,
    ally_mem: _PenaltySideMemory,
    opponent_mem: _PenaltySideMemory,
) -> list[str]:
    """Evidence IDs for penalty values asserted this frame."""
    ids: list[str] = []
    if "observed" in (ally_q, opponent_q) and evidence_id:
        ids.append(evidence_id)
    if ally_q == "held":
        ids.extend(ally_mem.evidence_ids)
    if opponent_q == "held":
        ids.extend(opponent_mem.evidence_ids)
    return ids


def _best_score(frame: VisionFrameResult) -> DetectorResult | None:
    """Highest-confidence score result on a frame, if any."""
    results = [
        detection
        for detection in frame.detections
        if detection.detector_name == "score"
        and isinstance(detection.reading, ScoreReading)
    ]
    return max(results, key=lambda item: item.confidence) if results else None
