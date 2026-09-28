"""Read coaching artifacts into the public result. Prompts stay on disk."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from public_site.frames import has_frame, ranked_entries
from public_site.models import CoachingMoment, LifecycleMark, PublicSubmissionResult
from splatoon3_ai_coach.analysis.player_count_series import format_avb
from splatoon3_ai_coach.cli.coach_common import COACH_INPUTS_DIRNAME
from splatoon3_ai_coach.coach.claim_catalog import NO_RECOMMENDATION_MESSAGE
from splatoon3_ai_coach.coach.llm_runs import empty_coaching_assessment

_SKIP = empty_coaching_assessment().assessment
_REPLACED = frozenset(
    {"death_special_ready", "death_map_overlay_before_false"}
)
_CONTEXT = frozenset({"splat", "map_overlay"})
_TITLES = {"splat": "Splat", "map_overlay": "Map check", "death": "Death"}
_BEFORE = 3
_AFTER = 2
_MAP_CLUSTER_GAP = 10.0


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
    inputs = analysis_dir / COACH_INPUTS_DIRNAME
    prototype = analysis_dir / "coach_prototype"
    events = _game_events(analysis_dir)
    moments = [
        _moment(analysis_dir, inputs, prototype, entry, events)
        for entry in ranked_entries(analysis_dir)
    ]
    return PublicSubmissionResult(moments=moments)


def _moment(
    analysis: Path,
    inputs: Path,
    prototype: Path,
    entry: dict,
    events: list[tuple[str, float]],
) -> CoachingMoment:
    coaching_name = str(entry.get("coaching_json") or "")
    coaching = _read_json(inputs / coaching_name) if coaching_name else {}
    factors = coaching.get("factors") or []
    statements = _statements(factors)
    projected = _projected(entry, inputs, factors, events)
    assessment = _assessment(prototype, str(entry.get("safe_id") or ""))
    if projected is not None and assessment is None:
        assessment = NO_RECOMMENDATION_MESSAGE
    safe_id = str(entry.get("safe_id") or "")
    return CoachingMoment(
        scenario_type=str(entry.get("candidate_type") or ""),
        video_time=float(entry.get("video_time") or 0),
        statements=statements,
        assessment=assessment,
        frame=has_frame(analysis, safe_id),
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
    entry: dict,
    inputs: Path,
    factors: object,
    events: list[tuple[str, float]],
) -> _Projected | None:
    if str(entry.get("candidate_type") or "") != "death_episode":
        return None
    name = str(entry.get("coach_input_json") or "")
    coach_input = _read_json(inputs / name) if name else {}
    death = _death(coach_input)
    if death is None:
        return None
    marks, remaining = _context_marks(
        death, events, coach_input.get("game_clock_samples")
    )
    return _Projected(
        heading=_heading(remaining),
        marks=marks,
        gaps=[],
        until_active_again=_recovery(death),
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
    death: dict, events: list[tuple[str, float]], samples: object
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
    marks = [_event_mark(*item) for item in before]
    marks.append(_anchor(death_time))
    marks.extend(_event_mark(*item) for item in after)
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
        if kind in _CONTEXT and _inside(when, death_time, earlier, later, after=after)
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


def _event_mark(kind: str, when: float) -> LifecycleMark:
    return LifecycleMark(
        label=kind,
        title=_TITLES.get(kind, kind),
        video_time=when,
        clock=_video_clock(when),
    )


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


def _recovery(death: dict) -> str | None:
    active = _seconds(death.get("death_to_active_again"))
    if active is not None:
        return f"{active:.1f}s out of play"
    respawn = _seconds(death.get("death_to_respawn"))
    if respawn is not None:
        return f"{respawn:.1f}s until respawn"
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
    special = _special_line(coach_input)
    mapped = _map_line(coach_input)
    if special:
        lines.append(special)
    if mapped:
        lines.append(mapped)
    lines.extend(_factor_lines(factors))
    lines.append(_roster_line(coach_input))
    return lines


def _special_line(coach_input: dict) -> str | None:
    special = _nest(coach_input, "special")
    reading = special.get("nearest_before_anchor") if special else None
    if not isinstance(reading, dict) or "ready" not in reading:
        return None
    if reading.get("ready") is True:
        return "Special was ready."
    if reading.get("ready") is False:
        return "Special was not observed as ready near the death."
    return None


def _map_line(coach_input: dict) -> str | None:
    nest = _nest(coach_input, "map")
    if nest is None or "map_check_before_death" not in nest:
        return None
    flag = nest.get("map_check_before_death")
    if flag is None:
        return None
    if flag is False:
        return "No map check observed before death"
    if flag is not True:
        return None
    gap = nest.get("seconds_since_map_check_before_death")
    if isinstance(gap, bool) or not isinstance(gap, (int, float)):
        return None
    return f"Last observed map check: {float(gap):.1f}s before death"


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
    at_death = players.get("at_death") if players else None
    if not isinstance(at_death, dict):
        return "No roster information was available."
    ally = at_death.get("ally_alive_count")
    opponent = at_death.get("opponent_alive_count")
    if _count(ally) is None or _count(opponent) is None:
        return "No roster information was available."
    return f"Roster at death: {format_avb(ally, opponent)}."


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


def _assessment(prototype: Path, safe_id: str) -> str | None:
    if not safe_id or not prototype.is_dir():
        return None
    prefix = f"{safe_id}."
    suffix = ".output.json"
    names = sorted(
        path.name
        for path in prototype.iterdir()
        if path.name.startswith(prefix) and path.name.endswith(suffix)
    )
    prose = [_prose(prototype / name) for name in names]
    kept = [text for text in prose if text]
    return kept[-1] if kept else None


def _prose(path: Path) -> str | None:
    text = str(_read_json(path).get("assessment") or "").strip()
    if not text or text == _SKIP:
        return None
    return text


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}
