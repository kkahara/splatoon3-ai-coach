"""Rainmaker possession state from explicit messages and observed match start."""

from splatoon3_ai_coach.vision.models import (
    RainmakerPossessionEvidence,
    RainmakerPossessionObservation,
    RainmakerPossessionReading,
    SourceFrameReference,
    VisionFrameResult,
)

_TARGET_STATES = {
    "ally_acquired_rainmaker": "ally_possession",
    "opponent_acquired_rainmaker": "opponent_possession",
    "ally_lost_rainmaker": "neutral",
    "opponent_lost_rainmaker": "neutral",
    "rainmaker_reset": "neutral",
}


class RainmakerPossessionFuser:
    """Hold direct Rainmaker state and its establishing evidence."""

    def __init__(self) -> None:
        self.state = "unknown"
        self.evidence: RainmakerPossessionEvidence | None = None
        self.previous_phase: str | None = None

    def step(
        self,
        frame: VisionFrameResult,
        battle_mode_id: str | None,
        match_phase: str,
        observation: RainmakerPossessionObservation | None = None,
    ) -> tuple[str, RainmakerPossessionEvidence | None]:
        """Apply a direct cue or trusted opening transition; otherwise hold."""
        if battle_mode_id != "rainmaker" or match_phase in ("out_of_match", "post_match"):
            self.state, self.evidence = "unknown", None
            self.previous_phase = match_phase
            return self.state, self.evidence
        if self._observed_normal_start(frame, match_phase):
            self.state = "neutral"
            self.evidence = RainmakerPossessionEvidence(
                state="neutral",
                video_time=frame.timestamp,
                source_frame=_source_frame(frame),
                initialization="observed_match_start",
            )
        self.previous_phase = match_phase
        reading = _reading(frame)
        if reading is None:
            return self.state, self.evidence
        self._apply_cue(frame, reading, observation)
        return self.state, self.evidence

    def _observed_normal_start(self, frame: VisionFrameResult, phase: str) -> bool:
        """Require an observed opening-countdown → in-match boundary."""
        if self.previous_phase != "opening_countdown" or phase != "in_match":
            return False
        return not any(
            isinstance(result.reading, RainmakerPossessionReading)
            and result.reading.timestamp == frame.timestamp
            for result in frame.detections
        )

    def _apply_cue(
        self,
        frame: VisionFrameResult,
        reading: RainmakerPossessionReading,
        observation: RainmakerPossessionObservation | None,
    ) -> None:
        """Apply a cue only when its grouped observation supports this frame."""
        cue = reading.cue.cue
        target = _TARGET_STATES[cue]
        if observation is None or observation.cue != cue:
            return
        samples = observation.samples
        first, current = samples[0], samples[-1]
        if (
            observation.first_video_time != first.video_time
            or current.video_time != frame.timestamp
            or current.source_frame.frame_id != frame.frame_id
            or current.cue != reading.cue
        ):
            return
        if len(samples) == 1 or target != self.state:
            self.state = target
            self.evidence = RainmakerPossessionEvidence(
                state=target,
                video_time=first.video_time,
                source_frame=first.source_frame,
                detector_evidence_ids=[sample.evidence_id for sample in samples],
                cue_observation=observation.model_copy(deep=True),
            )
        elif (
            self.evidence is not None
            and self.evidence.cue_observation is not None
            and self.evidence.detector_evidence_ids[0] == first.evidence_id
        ):
            self.evidence = self.evidence.model_copy(update={
                "detector_evidence_ids": [sample.evidence_id for sample in samples],
                "cue_observation": observation.model_copy(deep=True),
            })


def _reading(frame: VisionFrameResult) -> RainmakerPossessionReading | None:
    """Return exactly one direct cue reading matching this frame timestamp."""
    matches = [
        result.reading for result in frame.detections
        if result.detector_name == "rainmaker_possession"
        and isinstance(result.reading, RainmakerPossessionReading)
        and result.reading.timestamp == frame.timestamp
    ]
    return matches[0] if len(matches) == 1 else None


def _source_frame(frame: VisionFrameResult) -> SourceFrameReference:
    """Copy canonical decoder provenance for a non-detector initialization."""
    return SourceFrameReference(
        frame_id=frame.frame_id,
        timestamp=frame.timestamp,
        source_frame_index=frame.source_frame_index,
        source_pts=frame.source_pts,
        source_time_base_num=frame.source_time_base_num,
        source_time_base_den=frame.source_time_base_den,
        frame_path=frame.frame_path,
    )
