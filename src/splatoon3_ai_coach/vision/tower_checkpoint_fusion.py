"""Fuse attributed checkpoint-passed observations without using Tower score."""

from dataclasses import dataclass

from splatoon3_ai_coach.config.models import TowerCheckpointLocation
from splatoon3_ai_coach.vision.match_intro import MatchIdentityTracker
from splatoon3_ai_coach.vision.models import (
    MatchIntroReading,
    TowerControlReading,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.tower_checkpoint_state import (
    CheckpointObservation,
    TowerCheckpointState,
    _apply_observation,
)


@dataclass(frozen=True)
class _LocationEvidence:
    """One directly observed, calibrated location and team association."""

    location: TowerCheckpointLocation
    video_time: float
    frame_id: str
    evidence_id: str
    cue: bool
    at_cue: bool


def resolve_checkpoint_stage(frames: list[VisionFrameResult]) -> str | None:
    """Resolve stage from stored intro observations using the standard latch."""
    tracker = MatchIdentityTracker(intro_deadline_seconds=float("inf"))
    for frame in sorted(frames, key=lambda item: item.timestamp):
        for result in frame.detections:
            if isinstance(result.reading, MatchIntroReading):
                tracker.update(result.reading, video_time=frame.timestamp)
    return tracker.identity.stage_id


class TowerCheckpointFuser:
    """Track clears from same-frame cue, calibrated position, and team evidence."""

    def __init__(
        self, locations: list[TowerCheckpointLocation], stage_id: str | None
    ) -> None:
        self.locations = [
            location for location in locations if location.stage_id == stage_id
        ]
        self.state = TowerCheckpointState()
        self._cue_consumed = False
        self._at_consumed = False
        self._previous: _LocationEvidence | None = None

    def step(
        self,
        frame: VisionFrameResult,
        battle_mode_id: str | None,
        match_phase: str,
        map_overlay_present: bool | None,
    ) -> tuple[int | None, TowerCheckpointState | None]:
        """Return same-frame checkpoint identity and a copy of persistent state."""
        if battle_mode_id != "tower_control" or match_phase != "in_match":
            self._cue_consumed = False
            self._at_consumed = False
            self._previous = None
            return None, None
        if map_overlay_present:
            self._cue_consumed = False
            self._at_consumed = False
            self._previous = None
            return None, self.state.model_copy(deep=True)
        candidate = self._attributed_candidate(frame)
        if candidate is None:
            self._cue_consumed = False
            self._at_consumed = False
            self._previous = None
            return None, self.state.model_copy(deep=True)
        previous = self._previous
        self._previous = candidate
        if not candidate.cue:
            self._cue_consumed = False
            if candidate.at_cue and not self._at_consumed:
                observation = CheckpointObservation(
                    video_time=candidate.video_time,
                    frame_id=candidate.frame_id,
                    marker_evidence_id=candidate.evidence_id,
                    team=candidate.location.team,
                    checkpoint=candidate.location.checkpoint,
                    identity_evidence_ids=[candidate.evidence_id],
                    uncleared_evidence_id=candidate.evidence_id,
                )
                entry = getattr(self.state, observation.team)[observation.checkpoint - 1]
                _apply_observation(entry, observation)
                self._at_consumed = True
                return observation.checkpoint, self.state.model_copy(deep=True)
            if not candidate.at_cue:
                self._at_consumed = False
            return None, self.state.model_copy(deep=True)
        self._at_consumed = False
        if self._cue_consumed or previous is None or previous.cue:
            return None, self.state.model_copy(deep=True)
        if (
            candidate.location != previous.location
            or not 0 < candidate.video_time - previous.video_time <= 2.0
        ):
            return None, self.state.model_copy(deep=True)
        observation = CheckpointObservation(
            video_time=candidate.video_time,
            frame_id=candidate.frame_id,
            marker_evidence_id=candidate.evidence_id,
            team=candidate.location.team,
            checkpoint=candidate.location.checkpoint,
            identity_evidence_ids=[previous.evidence_id],
            identity_frame_id=previous.frame_id,
            identity_video_time=previous.video_time,
            passed_evidence_id=candidate.evidence_id,
        )
        entry = getattr(self.state, observation.team)[observation.checkpoint - 1]
        if entry.status != "cleared":
            _apply_observation(entry, observation)
        self._cue_consumed = True
        return observation.checkpoint, self.state.model_copy(deep=True)

    def _attributed_candidate(self, frame: VisionFrameResult) -> _LocationEvidence | None:
        """Accept only a unique calibrated location and same-result team."""
        association = frame.tower_team
        if association is None or association.control_presentation == "unknown":
            return None
        candidates = [
            result
            for result in frame.detections
            if result.id == association.result_id
            and result.detector_name == "tower_control"
            and isinstance(result.reading, TowerControlReading)
        ]
        if len(candidates) != 1:
            return None
        result = candidates[0]
        reading = result.reading
        assert isinstance(reading, TowerControlReading)
        marker = reading.marker
        if reading.timestamp != frame.timestamp or marker is None or not marker.usable:
            return None
        locations = [
            location
            for location in self.locations
            if location.team == association.control_presentation
            and abs(location.lane_position - marker.lane_position) <= location.tolerance
        ]
        if len(locations) != 1:
            return None
        location = locations[0]
        return _LocationEvidence(
            location=location,
            video_time=frame.timestamp,
            frame_id=frame.frame_id,
            evidence_id=result.id,
            cue=reading.checkpoint_passed_cue_score is not None,
            at_cue=reading.checkpoint_at_cue_score is not None,
        )
