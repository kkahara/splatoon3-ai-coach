"""Persist Tower control from explicit acquisition and loss messages."""

from splatoon3_ai_coach.vision.models import (
    SourceFrameReference,
    TowerControlCue,
    TowerControlEvidence,
    TowerControlReading,
    TowerOpponentLostObservation,
    TowerTookObservation,
    VisionFrameResult,
)


class TowerControlMessageFuser:
    """Fuse direct message observations without marker or score evidence."""

    def __init__(self) -> None:
        self.state = "unknown"
        self.evidence: TowerControlEvidence | None = None

    def step(
        self,
        frame: VisionFrameResult,
        battle_mode_id: str | None,
        match_phase: str,
        *,
        opponent_lost_observation: TowerOpponentLostObservation | None = None,
        took_observation: TowerTookObservation | None = None,
    ) -> tuple[str, TowerControlEvidence | None]:
        """Update persistent state from a uniquely attributed same-frame cue."""
        if battle_mode_id != "tower_control" or match_phase != "in_match":
            self.state = "unknown"
            self.evidence = None
            return self.state, None
        cues = [
            result
            for result in frame.detections
            if result.detector_name == "tower_control"
            and isinstance(result.reading, TowerControlReading)
            and result.reading.timestamp == frame.timestamp
            and result.reading.control_cue is not None
        ]
        if not cues:
            return self.state, self.evidence
        if len(cues) != 1:
            self.state = "unknown"
            self.evidence = None
            return self.state, None
        result = cues[0]
        reading = result.reading
        assert (
            isinstance(reading, TowerControlReading) and reading.control_cue is not None
        )
        cue = reading.control_cue
        if cue.cue_type == "opponent_lost":
            return self._opponent_lost(frame, result.id, cue, opponent_lost_observation)
        if cue.cue_type in ("ally_took", "opponent_took"):
            return self._took(frame, result.id, cue, took_observation)
        expected_team = "ally" if cue.cue_type.startswith("ally_") else "opponent"
        if cue.team != expected_team:
            self.state = "unknown"
            self.evidence = None
            return self.state, None
        state = (
            "ally_control"
            if cue.cue_type == "ally_took"
            else "opponent_control"
            if cue.cue_type == "opponent_took"
            else "returning"
        )
        if state != self.state:
            self.state = state
            self.evidence = TowerControlEvidence(
                video_time=frame.timestamp,
                source_frame=SourceFrameReference(
                    frame_id=frame.frame_id,
                    timestamp=frame.timestamp,
                    source_frame_index=frame.source_frame_index,
                    source_pts=frame.source_pts,
                    source_time_base_num=frame.source_time_base_num,
                    source_time_base_den=frame.source_time_base_den,
                    frame_path=frame.frame_path,
                ),
                detector_evidence_id=result.id,
                cue=cue,
            )
        return self.state, self.evidence

    def _took(
        self,
        frame: VisionFrameResult,
        evidence_id: str,
        cue: TowerControlCue,
        observation: TowerTookObservation | None,
    ) -> tuple[str, TowerControlEvidence | None]:
        """Apply a grouped direct acquisition cue, retaining support observed so far."""
        expected_team = "ally" if cue.cue_type == "ally_took" else "opponent"
        expected_observation = f"{cue.cue_type}_tower"
        if cue.team != expected_team:
            return self.state, self.evidence
        state = "ally_control" if cue.cue_type == "ally_took" else "opponent_control"
        if observation is None:
            if self.state != state:
                self.state = state
                self.evidence = TowerControlEvidence(
                    video_time=frame.timestamp,
                    source_frame=SourceFrameReference(
                        frame_id=frame.frame_id,
                        timestamp=frame.timestamp,
                        source_frame_index=frame.source_frame_index,
                        source_pts=frame.source_pts,
                        source_time_base_num=frame.source_time_base_num,
                        source_time_base_den=frame.source_time_base_den,
                        frame_path=frame.frame_path,
                    ),
                    detector_evidence_id=evidence_id,
                    cue=cue,
                )
            return self.state, self.evidence
        if observation.cue != expected_observation:
            return self.state, self.evidence
        first, current = observation.samples[0], observation.samples[-1]
        if (
            observation.first_video_time != first.video_time
            or current.video_time != frame.timestamp
            or current.source_frame.frame_id != frame.frame_id
            or current.evidence_id != evidence_id
            or current.cue != cue
        ):
            return self.state, self.evidence
        if len(observation.samples) == 1 or self.state != state:
            self.state = state
            self.evidence = TowerControlEvidence(
                video_time=first.video_time,
                source_frame=first.source_frame,
                detector_evidence_id=first.evidence_id,
                cue=first.cue,
                took_observation=observation.model_copy(deep=True),
            )
        elif (
            self.evidence is not None
            and self.evidence.took_observation is not None
            and self.evidence.detector_evidence_id == first.evidence_id
        ):
            self.evidence = self.evidence.model_copy(
                update={"took_observation": observation.model_copy(deep=True)}
            )
        return self.state, self.evidence

    def _opponent_lost(
        self,
        frame: VisionFrameResult,
        evidence_id: str,
        cue: TowerControlCue,
        observation: TowerOpponentLostObservation | None,
    ) -> tuple[str, TowerControlEvidence | None]:
        """Apply only the grouped direct cue, retaining observed support so far."""
        if observation is None or cue.team != "opponent":
            return self.state, self.evidence
        samples = observation.samples
        first, current = samples[0], samples[-1]
        if (
            observation.first_video_time != first.video_time
            or current.video_time != frame.timestamp
            or current.source_frame.frame_id != frame.frame_id
            or current.evidence_id != evidence_id
        ):
            return self.state, self.evidence
        if len(samples) == 1:
            self.state = "returning"
            self.evidence = TowerControlEvidence(
                video_time=first.video_time,
                source_frame=first.source_frame,
                detector_evidence_id=first.evidence_id,
                cue=cue,
                opponent_lost_observation=observation.model_copy(deep=True),
            )
        elif (
            self.state == "returning"
            and self.evidence is not None
            and self.evidence.opponent_lost_observation is not None
            and self.evidence.detector_evidence_id == first.evidence_id
        ):
            self.evidence = self.evidence.model_copy(
                update={"opponent_lost_observation": observation.model_copy(deep=True)}
            )
        return self.state, self.evidence
