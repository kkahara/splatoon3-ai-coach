"""Read coaching artifacts into the public result. Prompts stay on disk."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from public_site.frames import has_frame, ranked_entries
from public_site.models import CoachingMoment, LifecycleMark, PublicSubmissionResult
from splatoon3_ai_coach.analysis.player_count_series import format_avb
from splatoon3_ai_coach.coach.artifacts import (
    CoachingIndexEntry,
    coach_inputs_dir,
    coach_inputs_staleness,
    coach_prototype_dir,
    primary_assessment,
    primary_stored_validation,
    read_document,
    unit_file_names,
)
from splatoon3_ai_coach.coach.claim_validation import ValidationStatus
from splatoon3_ai_coach.coach.coach import annotate_claim_flags
from splatoon3_ai_coach.coach.llm_client import CoachingAssessment
from splatoon3_ai_coach.coach.llm_runs import empty_coaching_assessment
from splatoon3_ai_coach.coach.playback_clock import (
    ClockedDuration,
    PlaybackClockInfo,
    clock_video_span,
)
from splatoon3_ai_coach.config.models import canonical_factor_id

_SKIP = empty_coaching_assessment().assessment
_REPLACED = frozenset(
    {"death_special_ready", "death_map_overlay_before_false"}
)
_CONTEXT = frozenset({"splat", "map_overlay"})
_TITLES = {"splat": "Splat", "map_overlay": "Map check", "death": "Death"}
_BEFORE = 3
_AFTER = 2
_MAP_CLUSTER_GAP = 10.0
# Video seconds (the timeline's own clock); marks farther away are not context.
_CONTEXT_WINDOW_SECONDS = 15.0


@dataclass(frozen=True)
class _Projected:
    """Public wording for one death episode. Facts come from the coach input."""

    heading: str
    marks: list[LifecycleMark]
    gaps: list[str | None]
    until_active_again: str | None
    recovery_context: str | None
    context: list[str]
    recording_times: list[str]


def load_result(analysis_dir: Path) -> PublicSubmissionResult:
    """Ranked moments with active player statements and optional assessment prose."""
    inputs = coach_inputs_dir(analysis_dir)
    prototype = coach_prototype_dir(analysis_dir)
    events = _game_events(analysis_dir)
    moments = [
        _moment(analysis_dir, inputs, prototype, entry, events)
        for entry in ranked_entries(analysis_dir)
    ]
    staleness = coach_inputs_staleness(inputs, analysis_dir)
    stale = staleness is not None and staleness.stale
    return PublicSubmissionResult(
        moments=moments,
        stale=stale,
        stale_reason=staleness.reason.value if stale else None,
    )


def _moment(
    analysis: Path,
    inputs: Path,
    prototype: Path,
    entry: CoachingIndexEntry,
    events: list[tuple[str, float]],
) -> CoachingMoment:
    coaching = read_document(inputs, entry.coaching_json) or {}
    factors = coaching.get("factors") or []
    statements = _statements(factors)
    projected = _projected(entry, inputs, factors, events)
    review = _review(prototype, inputs, entry)
    return CoachingMoment(
        scenario_type=entry.candidate_type,
        video_time=entry.video_time,
        statements=statements,
        assessment=review.assessment,
        assessment_status=review.status,
        current_assessment_status=review.current_status,
        coaching_state=review.state,
        recommendations=review.recommendations,
        frame=has_frame(analysis, entry.safe_id),
        heading=None if projected is None else projected.heading,
        marks=[] if projected is None else projected.marks,
        gaps=[] if projected is None else projected.gaps,
        until_active_again=None if projected is None else projected.until_active_again,
        recovery_context=None if projected is None else projected.recovery_context,
        context=[] if projected is None else projected.context,
        recording_times=[] if projected is None else projected.recording_times,
    )


def _statements(factors: object) -> list[str]:
    if not isinstance(factors, list):
        return []
    lines: list[str] = []
    for factor in factors:
        if not isinstance(factor, dict) or not factor.get("active"):
            continue
        if factor.get("statement_player"):
            lines.append(str(factor["statement_player"]))
    return lines


def _projected(
    entry: CoachingIndexEntry,
    inputs: Path,
    factors: object,
    events: list[tuple[str, float]],
) -> _Projected | None:
    if entry.candidate_type != "death_episode":
        return None
    coach_input = read_document(inputs, entry.coach_input_json) or {}
    death = _death(coach_input)
    if death is None:
        return None
    marks, remaining = _context_marks(
        death,
        events,
        coach_input.get("game_clock_samples"),
        _playback_info(coach_input),
    )
    return _Projected(
        heading=_heading(remaining),
        marks=marks,
        gaps=[],
        until_active_again=_recovery(coach_input),
        recovery_context=_recovery_map_line(coach_input),
        context=_context(coach_input, factors),
        recording_times=[],
    )


def _death(coach_input: dict) -> dict | None:
    primary = coach_input.get("primary_context")
    if not isinstance(primary, dict):
        return None
    death = primary.get("death_episode")
    return death if isinstance(death, dict) else None


def _game_events(analysis: Path) -> list[tuple[str, float]]:
    rows = _read_json(analysis / "vision_manifest.json").get("game_events")
    if not isinstance(rows, list):
        return []
    timed: list[tuple[str, float]] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("event_type"), str):
            continue
        when = _video_time(row.get("start_time"))
        if when is not None:
            timed.append((row["event_type"], when))
    return timed


def _context_marks(
    death: dict,
    events: list[tuple[str, float]],
    samples: object,
    playback: PlaybackClockInfo | None = None,
) -> tuple[list[LifecycleMark], int | None]:
    death_time = _video_time(death.get("death_time"))
    rows = samples if isinstance(samples, list) else []
    remaining = None if death_time is None else _remaining(rows, "death")
    if death_time is None:
        return [], remaining
    earlier, later = _death_bounds(events, death_time)
    before = _pick(events, death_time, earlier, later, after=False)
    after = _pick(
        events,
        death_time,
        earlier,
        later,
        after=True,
        recovery_end=_recovery_end(death, death_time),
    )
    marks = [_event_mark(*item, death_time, playback) for item in before]
    marks.append(_anchor(death_time))
    marks.extend(_event_mark(*item, death_time, playback) for item in after)
    return marks, remaining


def _death_bounds(
    events: list[tuple[str, float]], death_time: float
) -> tuple[float | None, float | None]:
    earlier = [when for kind, when in events if kind == "death" and when < death_time]
    later = [when for kind, when in events if kind == "death" and when > death_time]
    previous = max(earlier) if earlier else None
    following = min(later) if later else None
    return previous, following


def _pick(
    events: list[tuple[str, float]],
    death_time: float,
    earlier: float | None,
    later: float | None,
    *,
    after: bool,
    recovery_end: float | None = None,
) -> list[tuple[str, float]]:
    pool = [
        (kind, when)
        for kind, when in events
        if kind in _CONTEXT
        and _inside(when, death_time, earlier, later, after=after)
        and abs(when - death_time) <= _CONTEXT_WINDOW_SECONDS
        and not _recovery_overlay(kind, when, after, recovery_end)
    ]
    pool = _cluster_maps(pool)
    pool.sort(key=lambda item: abs(item[1] - death_time))
    chosen = pool[: _AFTER if after else _BEFORE]
    chosen.sort(key=lambda item: item[1])
    return chosen


def _cluster_maps(pool: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """One map-check mark per run of overlays; splats pass through."""
    kept: list[tuple[str, float]] = []
    previous: float | None = None
    for kind, when in sorted(pool, key=lambda item: item[1]):
        if kind != "map_overlay":
            kept.append((kind, when))
            continue
        if previous is None or when - previous > _MAP_CLUSTER_GAP:
            kept.append((kind, when))
        previous = when
    return kept


def _recovery_end(death: dict, death_time: float) -> float | None:
    active = _video_time(death.get("active_again_time"))
    if active is not None:
        return active
    duration = _seconds(death.get("death_to_active_again"))
    if duration is not None:
        return death_time + duration
    return None


def _recovery_overlay(
    kind: str, when: float, after: bool, recovery_end: float | None
) -> bool:
    if not after or kind != "map_overlay":
        return False
    return recovery_end is None or when <= recovery_end


def _inside(
    when: float,
    death_time: float,
    earlier: float | None,
    later: float | None,
    *,
    after: bool,
) -> bool:
    if after:
        return when > death_time and (later is None or when < later)
    return when < death_time and (earlier is None or when > earlier)


def _event_mark(
    kind: str,
    when: float,
    death_time: float,
    playback: PlaybackClockInfo | None,
) -> LifecycleMark:
    offset = clock_video_span(abs(when - death_time), playback)
    direction = "before" if when < death_time else "after"
    return LifecycleMark(
        label=kind,
        title=_TITLES.get(kind, kind),
        video_time=when,
        clock=_video_clock(when),
        **_offset_fields(offset, direction),
    )


def _offset_fields(offset: ClockedDuration | None, direction: str) -> dict:
    """Temporal-only offset from the death, on the same clock as durations."""
    if offset is None:
        return {}
    if offset.value is None:
        return {
            "offset_seconds": offset.video_seconds,
            "offset_clock": "video",
            "offset_label": f"{offset.video_seconds:.1f}s of video {direction}",
        }
    if offset.clock == "game":
        return {
            "offset_seconds": offset.value,
            "offset_clock": "game",
            "offset_label": f"{offset.value:.0f}s {direction} (game clock)",
        }
    return {
        "offset_seconds": offset.value,
        "offset_clock": "video",
        "offset_label": f"{offset.value:.1f}s {direction}",
    }


def _playback_info(coach_input: dict) -> PlaybackClockInfo | None:
    raw = coach_input.get("playback_clock")
    if not isinstance(raw, dict):
        return None
    try:
        return PlaybackClockInfo.model_validate(raw)
    except ValidationError:
        return None


def _anchor(death_time: float) -> LifecycleMark:
    return LifecycleMark(
        label="death",
        title="Death",
        video_time=death_time,
        clock=_video_clock(death_time),
        anchor=True,
    )


def _video_time(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _remaining(samples: list, label: str) -> int | None:
    for sample in samples:
        if not isinstance(sample, dict) or sample.get("label") != label:
            continue
        observation = sample.get("observation")
        if not isinstance(observation, dict):
            return None
        seconds = observation.get("seconds_remaining")
        if isinstance(seconds, bool) or not isinstance(seconds, int):
            return None
        return seconds
    return None


def _clock(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}:{secs:02d}"


def _heading(remaining: int | None) -> str:
    if remaining is None:
        return "Death"
    return f"Death — {_clock(remaining)} remaining"


def _recovery(coach_input: dict) -> str | None:
    """Play-time recovery line; omitted when only unverified video seconds exist."""
    durations = coach_input.get("durations")
    if not isinstance(durations, dict):
        return None
    for key, suffix in (
        ("death_to_active_again", "out of play"),
        ("death_to_respawn", "until respawn"),
    ):
        text = _clocked_text(durations.get(key), suffix)
        if text is not None:
            return text
    return None


def _clocked_text(duration: object, suffix: str) -> str | None:
    if not isinstance(duration, dict):
        return None
    value = _seconds(duration.get("value"))
    if value is None:
        return None
    if duration.get("clock") == "game":
        return f"{value:.0f}s {suffix} (game clock)"
    if duration.get("clock") == "video":
        return f"{value:.1f}s {suffix}"
    return None


def _recovery_map_line(coach_input: dict) -> str | None:
    nest = _nest(coach_input, "map")
    if nest is not None and nest.get("map_checked_while_dead") is True:
        return "The player checked the map during recovery."
    return None


def _seconds(raw: object) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _context(coach_input: dict, factors: object) -> list[str]:
    lines: list[str] = []
    special = _special_line(coach_input, _active_ids(factors))
    mapped = _map_line(coach_input)
    if special:
        lines.append(special)
    if mapped:
        lines.append(mapped)
    lines.extend(_factor_lines(factors))
    if coach_input.get("roster_evidence") is not False:
        lines.append(_roster_line(coach_input))
    return lines


def _active_ids(factors: object) -> set[str]:
    if not isinstance(factors, list):
        return set()
    return {
        canonical_factor_id(str(f.get("factor_id")))
        for f in factors
        if isinstance(f, dict) and f.get("active")
    }


_SPECIAL_LINES = {
    "ready": "Special was ready.",
    "unconfirmed": "Special readiness just before the death was not confirmed.",
    "not_ready": "Special was not ready just before the death.",
    "not_observed": "The special gauge was not visible just before the death.",
}


def _special_line(coach_input: dict, active: set[str]) -> str | None:
    """ "Ready" needs the confirmed special-ready factor (consecutive readings);
    a reading the vision layer marked not coaching-valid is not observed."""
    special = _nest(coach_input, "special")
    reading = special.get("nearest_before_anchor") if special else None
    if not isinstance(reading, dict):
        return None
    state = special.get("state_at_anchor") if special else None
    if reading.get("coaching_valid") is False or reading.get("visible") is not True:
        state = "not_observed"
    elif state not in _SPECIAL_LINES:
        state = "ready" if reading.get("ready") is True else "not_ready"
    if state == "ready" and "death_special_ready" not in active:
        state = "unconfirmed"
    return _SPECIAL_LINES[state]


_EPISODE_MAP_REASONS = frozenset(
    {"episode_pre_death_coverage_gap", "episode_respawn_map_missing"}
)


def _map_line(coach_input: dict) -> str | None:
    nest = _nest(coach_input, "map")
    if nest is None:
        return None
    status = nest.get("map_check_status")
    if status is None:
        if "map_check_before_death" not in nest:
            return None
        # Pre-coverage artifacts: only a positive overlay is assertable.
        observed = nest.get("map_check_before_death") is True
        status = "observed" if observed else "unknown"
    if status == "unknown":
        if nest.get("map_check_unknown_reason") in _EPISODE_MAP_REASONS:
            return "Map-check evidence was unavailable around this death."
        return "Map-check evidence was unavailable for this video."
    if status == "not_observed":
        return "No map check observed before death"
    if status != "observed":
        return None
    gap = nest.get("seconds_since_map_check_before_death")
    if isinstance(gap, bool) or not isinstance(gap, (int, float)):
        return "A map check was observed before this death."
    if not _playback_normal(coach_input):
        return "A map check was observed before this death."
    return f"Last observed map check: {float(gap):.1f}s before death"


def _playback_normal(coach_input: dict) -> bool:
    """Video seconds equal game seconds only on verified normal playback."""
    playback = coach_input.get("playback_clock")
    return isinstance(playback, dict) and playback.get("status") == "normal"


def _factor_lines(factors: object) -> list[str]:
    if not isinstance(factors, list):
        return []
    lines: list[str] = []
    for factor in factors:
        if not isinstance(factor, dict) or not factor.get("active"):
            continue
        if factor.get("factor_id") in _REPLACED or not factor.get("statement_player"):
            continue
        lines.append(str(factor["statement_player"]))
    return lines


def _roster_line(coach_input: dict) -> str:
    players = _nest(coach_input, "players")
    if players and _death_in_unreliable_roster(players, _death(coach_input)):
        return "Roster information around this death was unreliable."
    at_death = players.get("at_death") if players else None
    if not isinstance(at_death, dict):
        return "No roster information was available."
    ally = at_death.get("ally_alive_count")
    opponent = at_death.get("opponent_alive_count")
    if _count(ally) is None or _count(opponent) is None:
        return "No roster information was available."
    return f"Roster at death: {format_avb(ally, opponent)}."


def _death_in_unreliable_roster(players: dict, death: dict | None) -> bool:
    when = _seconds((death or {}).get("death_time"))
    intervals = players.get("unreliable_intervals") or []
    if when is None or not isinstance(intervals, list):
        return False
    for item in intervals:
        start = _seconds(item.get("start")) if isinstance(item, dict) else None
        end = _seconds(item.get("end")) if isinstance(item, dict) else None
        if start is not None and end is not None and start <= when <= end:
            return True
    return False


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _nest(coach_input: dict, name: str) -> dict | None:
    primary = coach_input.get("primary_context")
    if not isinstance(primary, dict):
        return None
    value = primary.get(name)
    return value if isinstance(value, dict) else None


def _video_clock(seconds: float) -> str:
    whole = int(seconds) if seconds >= 0 else 0
    minutes, secs = divmod(whole, 60)
    tenths = int((seconds - whole) * 10)
    return f"{minutes}:{secs:02d}.{tenths}"


@dataclass(frozen=True)
class _Review:
    """Public coaching state for one unit; prose only when it may be shown."""

    state: str
    assessment: str | None = None
    status: str | None = None
    current_status: str | None = None
    recommendations: list[str] = field(default_factory=list)


def _review(prototype: Path, inputs: Path, entry: CoachingIndexEntry) -> _Review:
    """State, prose, the stored original status and a current revalidation.

    No model prose (never selected, skipped or failed) is "not reviewed", never
    "no recommendation supported". ``status`` is the one stored when the output
    was generated (``None`` if none was stored) and is never recomputed;
    ``current_status`` is today's validator. If either blocks, the prose is
    withheld and the deterministic evidence lines are shown instead.
    """
    assessment = primary_assessment(prototype, entry.safe_id)
    text = "" if assessment is None else assessment.assessment.strip()
    if assessment is None or not text or text == _SKIP:
        return _Review(state="not_reviewed")
    stored = primary_stored_validation(prototype, entry.safe_id)
    original = None if stored is None else stored.status.value
    current = _current_status(prototype, inputs, entry, assessment)
    if ValidationStatus.BLOCKED_UNSUPPORTED_CLAIM.value in (original, current):
        return _Review(state="blocked", status=original, current_status=current)
    recommendations = [r.strip() for r in assessment.recommendations if r.strip()]
    return _Review(
        state="recommendation" if recommendations else "no_recommendation",
        assessment=text,
        status=original,
        current_status=current,
        recommendations=recommendations,
    )


def _current_status(
    prototype: Path,
    inputs: Path,
    entry: CoachingIndexEntry,
    assessment: CoachingAssessment,
) -> str:
    view = read_document(prototype, unit_file_names(entry.safe_id).llm_view)
    if view is None:
        view = read_document(inputs, entry.llm_view_json)
    return annotate_claim_flags(assessment, view)["validation_status"]


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}
