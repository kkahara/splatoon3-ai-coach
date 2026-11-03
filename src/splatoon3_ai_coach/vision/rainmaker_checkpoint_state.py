"""Minimal persistent Rainmaker cleared facts from direct milestone evidence."""

from splatoon3_ai_coach.vision.models import (
    RainmakerCheckpointObservation,
    RainmakerCheckpointState,
    VisionFrameResult,
)


class RainmakerCheckpointFuser:
    """Latch each team's first observed breakthrough for the current match."""

    def __init__(self) -> None:
        self.state = RainmakerCheckpointState()

    def step(
        self,
        frame: VisionFrameResult,
        battle_mode_id: str | None,
        match_phase: str,
        observation: RainmakerCheckpointObservation | None,
    ) -> RainmakerCheckpointState | None:
        """Hold cleared evidence; absent cues leave unestablished teams unknown."""
        if battle_mode_id != "rainmaker" or match_phase != "in_match":
            self.state = RainmakerCheckpointState()
            return None
        if observation is None or not _supports_frame(frame, observation):
            return self.state.model_copy(deep=True)
        team = observation.team
        previous = getattr(self.state, f"{team}_evidence")
        if previous is None or (
            previous.samples[0].evidence_id == observation.samples[0].evidence_id
        ):
            self.state = self.state.model_copy(
                update={
                    team: "cleared",
                    f"{team}_evidence": observation.model_copy(deep=True),
                }
            )
        return self.state.model_copy(deep=True)


def _supports_frame(
    frame: VisionFrameResult, observation: RainmakerCheckpointObservation
) -> bool:
    """Require current direct detector support and consistent group identity."""
    first, current = observation.samples[0], observation.samples[-1]
    return (
        observation.first_video_time == first.video_time
        and observation.last_video_time == current.video_time == frame.timestamp
        and current.source_frame.frame_id == frame.frame_id
        and all(sample.reading.team == observation.team for sample in observation.samples)
        and all(
            sample.reading.timestamp == sample.video_time
            and sample.source_frame.timestamp == sample.video_time
            for sample in observation.samples
        )
        and any(
            result.id == current.evidence_id
            and result.detector_name == "rainmaker_checkpoint"
            and result.reading == current.reading
            for result in frame.detections
        )
    )
