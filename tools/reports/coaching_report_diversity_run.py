"""Coaching diversity experiment: exactly-once calls, assembly, blinding, analysis.

``run`` calls the provider once per planned unit. A unit selected by both arms
is called once and its output is shown in both reports, so report differences
come from selection alone. A reply, even an unparseable one, is a result and
is never re-called; only provider errors are retried (archived first, at most
``MAX_ATTEMPTS``). ``assemble`` builds each arm's ``coach_prototype/`` exactly
as ``s3-coach coach-prototype`` lays it out, renders the public report with
``public_site.results.load_result`` in the review page's order, checks that
every moment's evidence is identical across arms, and writes the blinded
packets (``blind/``) and the key (``key/``). ``analyze`` joins a filled
rating sheet with the key.
"""

from __future__ import annotations

import csv
import json
import random
import shutil
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import coaching_report_diversity as xd
from loguru import logger
from public_site.models import CoachingMoment, PublicSubmissionResult
from public_site.results import load_result

from splatoon3_ai_coach.coach.artifacts import (
    COACH_INPUTS_META_FILENAME,
    PrototypeIndex,
    PrototypeIndexEntry,
    PrototypeOutput,
    RunProvenance,
    load_unit_artifacts,
    output_file_names,
    read_coaching_index,
    read_prototype_index,
    unit_file_names,
    write_assessment_artifacts,
    write_json,
    write_prototype_index,
)
from splatoon3_ai_coach.coach.claim_basis import RECOMMENDATION_TEMPLATES
from splatoon3_ai_coach.coach.coach import (
    annotate_claim_flags,
    parse_coaching_assessment,
    serialize_llm_view_user_prompt,
)
from splatoon3_ai_coach.coach.coaching_candidates import active_factor_ids
from splatoon3_ai_coach.coach.llm_runs import (
    LlmRun,
    build_llm_runs,
    empty_coaching_assessment,
    run_provenance,
    utc_timestamp,
)
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.config.settings import CoachSettings

MAX_ATTEMPTS = 3
RunFactory = Callable[[AppConfig], LlmRun]
MEASURES = (
    ("usefulness", "1to5"),
    ("actionability", "1to5"),
    ("specificity", "1to5"),
    ("redundancy", "1to5"),
    ("evidence_supported", "yn"),
    ("opportunity_lost", "yn"),
)
_WITHHELD = ("contains_unverified_wording", "blocked_unsupported_claim")
_ATTEMPTED = ("recommendation", "no_recommendation", "blocked")


# --- run ----------------------------------------------------------------------


def default_run(base: AppConfig) -> LlmRun:
    """The production provider and model, built at the settings boundary."""
    return build_llm_runs(
        provider_name=xd.PROVIDER,
        coach_cfg=base.coach,
        settings=CoachSettings(),
        ollama_model=None,
        nvidia_model_override=None,
        also_baseline=False,
        baseline_model_override=None,
        cursor_model_override=xd.MODEL,
    )[0]


def input_problems(out: Path) -> list[str]:
    """Every planned input exists and matches ``inputs.sha256`` and the plan."""
    plan = xd.load_json(out / "calls.json")
    listed = dict(
        reversed(line.split("  ", 1))
        for line in (out / "inputs.sha256").read_text(encoding="utf-8").splitlines()
    )
    problems = []
    for rel, digest in listed.items():
        path = out / rel
        if not path.is_file() or xd.sha256_bytes(path.read_bytes()) != digest:
            problems.append(f"{rel}: missing or changed")
    expected = {plan["system_prompt"]: plan["system_prompt_sha256"]}
    expected |= {c["user_prompt"]: c["user_prompt_sha256"] for c in plan["calls"]}
    for rel, digest in expected.items():
        if listed.get(rel) != digest:
            problems.append(f"{rel}: differs from calls.json")
    return problems


def record_path(out: Path, call_id: str) -> Path:
    """Output record of one call."""
    return out / "outputs" / f"{call_id}.json"


def _answered(out: Path, call: Mapping[str, Any]) -> bool:
    path = record_path(out, call["call_id"])
    return path.is_file() and xd.load_json(path).get("raw_output") is not None


def run(out: Path, *, live: bool, factory: RunFactory = default_run) -> int:
    """Make every pending call once; without ``live`` only report what is pending."""
    plan = xd.load_json(out / "calls.json")
    problems = input_problems(out)
    if problems:
        logger.error("inputs do not verify; no call made: {}", problems)
        return 2
    pending = [c for c in plan["calls"] if not _answered(out, c)]
    if not live:
        logger.warning(
            "{} of {} calls pending; pass --live to call",
            len(pending),
            len(plan["calls"]),
        )
        return 0
    llm_run = factory(xd.base_config())
    system = (out / plan["system_prompt"]).read_text(encoding="utf-8")
    for call in pending:
        _call_once(out, call, llm_run, system)
    unanswered = [c["call_id"] for c in plan["calls"] if not _answered(out, c)]
    if unanswered:
        logger.error("{} calls without a reply: {}", len(unanswered), unanswered)
        return 1
    return 0


def _archive_failure(out: Path, call_id: str) -> int:
    """Move a provider-error record aside; return the number of failed attempts."""
    failed = out / "outputs" / "failed_attempts"
    path = record_path(out, call_id)
    if path.is_file():
        failed.mkdir(parents=True, exist_ok=True)
        n = len(list(failed.glob(f"{call_id}.*.json"))) + 1
        path.rename(failed / f"{call_id}.{n}.json")
    return len(list(failed.glob(f"{call_id}.*.json"))) if failed.is_dir() else 0


def _call_once(out: Path, call: Mapping[str, Any], llm_run: LlmRun, system: str) -> None:
    attempts = _archive_failure(out, call["call_id"])
    if attempts >= MAX_ATTEMPTS:
        logger.error("{}: {} failed attempts; not retried", call["call_id"], attempts)
        return
    user = (out / call["user_prompt"]).read_text(encoding="utf-8")
    if xd.sha256_bytes(user.encode("utf-8")) != call["user_prompt_sha256"]:
        raise SystemExit(f"{call['call_id']}: prompt changed; no call made")
    executed_at = utc_timestamp()
    started = time.perf_counter()
    try:
        raw, error = llm_run.provider.complete(system, user), None
    except Exception as exc:  # noqa: BLE001 — a provider error is recorded, then retried
        raw, error = None, f"{type(exc).__name__}: {exc}"
    provenance = run_provenance(
        llm_run,
        provider_name=xd.PROVIDER,
        system_prompt=system,
        user_prompt=user,
        llm_view_bytes=None,
        importance_config_hash=None,
        executed_at=executed_at,
    ).model_copy(update={"llm_view_sha256": call["llm_view_sha256"]})
    record = {
        "experiment": xd.EXPERIMENT,
        "call_id": call["call_id"],
        "model_label": llm_run.label,
        "raw_output": raw,
        "call_error": error,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "provenance": provenance.model_dump(mode="json"),
    }
    xd.dump_json(record_path(out, call["call_id"]), record)
    entry = {"call_id": call["call_id"], "ok": error is None, "at": executed_at}
    with (out / "run_log.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(entry, sort_keys=True) + "\n")


# --- coach_prototype per arm --------------------------------------------------


def _records(out: Path, plan: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    records = {}
    for call in plan["calls"]:
        path = record_path(out, call["call_id"])
        record = xd.load_json(path) if path.is_file() else {}
        if record.get("raw_output") is None:
            raise SystemExit(f"{call['call_id']}: no reply; run first")
        records[(call["report_id"], call["candidate_id"])] = record
    return records


def build_prototype(
    shadow: Path, report_id: str, records: Mapping[tuple[str, str], Mapping[str, Any]]
) -> Path:
    """Write ``coach_prototype/`` for one arm from the shared call records."""
    inputs, proto = shadow / "coach_inputs", shadow / "coach_prototype"
    if proto.exists():
        shutil.rmtree(proto)
    proto.mkdir()
    config_hash = xd.load_json(inputs / COACH_INPUTS_META_FILENAME)[
        "importance_config_hash"
    ]
    labels = {r["model_label"] for r in records.values()}
    if len(labels) != 1:
        raise SystemExit(f"model labels differ across calls: {sorted(labels)}")
    label = labels.pop()
    entries = []
    for entry in read_coaching_index(inputs).entries:
        record = (
            records.get((report_id, entry.candidate_id))
            if entry.selected_for_llm
            else None
        )
        entries.append(_prototype_entry(inputs, proto, entry, record, label, config_hash))
    write_prototype_index(
        proto, PrototypeIndex(provider=xd.PROVIDER, primary_model=label, entries=entries)
    )
    return proto


def _prototype_entry(
    inputs: Path,
    proto: Path,
    entry: Any,
    record: Mapping[str, Any] | None,
    label: str,
    config_hash: str,
) -> PrototypeIndexEntry:
    artifacts = load_unit_artifacts(inputs, entry)
    unit, names = artifacts.unit, unit_file_names(entry.safe_id)
    write_json(proto / names.coach_input, artifacts.coach_input.model_dump(mode="json"))
    write_json(proto / names.coaching, unit.model_dump(mode="json"))
    result = PrototypeIndexEntry(
        scenario_id=entry.scenario_id,
        candidate_id=unit.candidate_id,
        candidate_type=unit.candidate_type,
        safe_id=entry.safe_id,
        video_time=unit.video_time,
        importance_score=unit.importance_score,
        rank=unit.rank,
        selected_for_llm=bool(unit.selected_for_llm),
        active_factors=active_factor_ids(unit.to_candidate()),
        coach_input_json=names.coach_input,
        coaching_json=names.coaching,
    )
    if record is None:
        outputs = [_skip_output(proto, entry.safe_id, label)]
    else:
        view = artifacts.llm_view
        write_json(proto / names.llm_view, view.model_dump(mode="json"))
        (proto / names.user_prompt).write_bytes(
            serialize_llm_view_user_prompt(view).encode("utf-8")
        )
        result.llm_view_json, result.user_prompt_txt = names.llm_view, names.user_prompt
        outputs = _model_output(proto, entry.safe_id, label, view, record, config_hash)
    result.outputs = outputs
    result.primary_output_json = next(
        (o.output_json for o in outputs if o.model == label), None
    )
    return result


def _skip_output(proto: Path, safe_id: str, label: str) -> PrototypeOutput:
    assessment = empty_coaching_assessment()
    raw = json.dumps(
        assessment.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )
    return write_assessment_artifacts(
        out_dir=proto,
        safe_id=safe_id,
        model_label=label,
        assessment=assessment,
        raw_text=raw + "\n",
        flags=annotate_claim_flags(assessment),
        skipped_llm=True,
    )


def _model_output(
    proto: Path,
    safe_id: str,
    label: str,
    view: Any,
    record: Mapping[str, Any],
    config_hash: str,
) -> list[PrototypeOutput]:
    raw = record["raw_output"]
    (proto / output_file_names(safe_id, label).raw).write_text(raw, encoding="utf-8")
    try:
        assessment = parse_coaching_assessment(raw)
    except Exception:  # noqa: BLE001 — as the CLI: an unparseable reply has no output
        return []
    provenance = RunProvenance.model_validate(record["provenance"]).model_copy(
        update={"importance_config_hash": config_hash}
    )
    return [
        write_assessment_artifacts(
            out_dir=proto,
            safe_id=safe_id,
            model_label=label,
            assessment=assessment,
            raw_text=raw,
            flags=annotate_claim_flags(assessment, view),
            skipped_llm=False,
            provenance=provenance,
        )
    ]


# --- public report rendering (mirrors web/public/src/pages/reviewMoments.js) --


def passed_evidence_check(m: CoachingMoment) -> bool:
    """Neither the original nor the current validation withholds the prose."""
    return (
        m.assessment_status not in _WITHHELD
        and m.current_assessment_status not in _WITHHELD
    )


def is_key_moment(m: CoachingMoment) -> bool:
    """A supported recommendation the page presents as coaching."""
    return (
        m.coaching_state == "recommendation"
        and bool(m.assessment)
        and bool(m.recommendations)
        and passed_evidence_check(m)
    )


def evidence_lines(m: CoachingMoment) -> list[str]:
    """Observed facts in display order."""
    if not m.heading:
        return list(m.statements)
    return [x for x in (m.until_active_again, m.recovery_context, *m.context) if x]


def moment_title(m: CoachingMoment) -> str:
    """Heading, or scenario type and video time."""
    if m.heading:
        return m.heading
    whole = int(m.video_time)
    return f"{m.scenario_type} — video {whole // 60}:{whole % 60:02d}"


def next_game_tips(
    key: Sequence[CoachingMoment], limit: int = 3
) -> list[tuple[str, str]]:
    """First recommendation of each key moment; exact duplicates dropped."""
    seen, tips = set(), []
    for m in key:
        text = m.recommendations[0] if m.recommendations else ""
        if text and text not in seen:
            seen.add(text)
            tips.append((text, moment_title(m)))
        if len(tips) == limit:
            break
    return tips


def render_packet(result: PublicSubmissionResult, version: str) -> str:
    """One report as Markdown, in the review page's sections and English text."""
    key = [m for m in result.moments if is_key_moment(m)]
    other = [m for m in result.moments if not is_key_moment(m)]
    lines = [f"# Report version {version}", "", "## Your match", ""]
    lines.append(
        f"Coaching recommendations for {len(key)} of {len(result.moments)} "
        "detected moments."
        if key
        else "The coach has no recommendations for this match. The detected "
        "moments are listed below."
    )
    tips = next_game_tips(key)
    if tips:
        lines += ["", "### Things to take into your next game", ""]
        lines += [
            f"{n}. {text} (From: {title})" for n, (text, title) in enumerate(tips, 1)
        ]
    if key:
        lines += ["", "## Key coaching moments"]
        for m in key:
            lines += _key_lines(m)
    if other:
        lines += [
            "",
            "## Other detected moments",
            "",
            "Events from this match without a coaching recommendation, shown as part "
            "of your match timeline.",
        ]
        for m in other:
            lines += _other_lines(m)
    return "\n".join(lines) + "\n"


def _key_lines(m: CoachingMoment) -> list[str]:
    lines = ["", f"### {moment_title(m)}", "", "**What to work on**", ""]
    lines += [f"- {r}" for r in m.recommendations]
    lines += ["", "**Coach's note**", "", m.assessment or ""]
    return lines + _evidence_block(m)


def _other_lines(m: CoachingMoment) -> list[str]:
    lines = ["", f"### {moment_title(m)}"]
    if (
        m.coaching_state == "no_recommendation"
        and m.assessment
        and passed_evidence_check(m)
    ):
        lines += ["", "**What happened**", "", m.assessment]
    if m.coaching_state in _ATTEMPTED:
        lines += [
            "",
            "**No coaching recommendation for this moment** — There isn't enough "
            "evidence to confidently recommend a change here.",
        ]
    return lines + _evidence_block(m)


def _evidence_block(m: CoachingMoment) -> list[str]:
    lines = evidence_lines(m)
    return ["", "**Match evidence**", "", *[f"- {x}" for x in lines]] if lines else []


def evidence_signature(result: PublicSubmissionResult) -> list[dict[str, Any]]:
    """Every deterministic (non-LLM) field of every moment, for cross-arm checks."""
    keep = {
        "scenario_type",
        "video_time",
        "statements",
        "heading",
        "marks",
        "gaps",
        "until_active_again",
        "recovery_context",
        "context",
        "recording_times",
    }
    return [m.model_dump(mode="json", include=keep) for m in result.moments]


# --- assemble -----------------------------------------------------------------


def arm_checks(proto: Path, result: PublicSubmissionResult) -> dict[str, Any]:
    """Objective per-arm facts for the key (never shown to the rater)."""
    key = [m for m in result.moments if is_key_moment(m)]
    families = [_family(r) for m in key for r in m.recommendations]
    statuses, unsupported = Counter(), 0
    index = read_prototype_index(proto)
    for entry in [] if index is None else index.entries:
        for output in entry.outputs:
            if output.skipped_llm or output.flags_json is None:
                continue
            flags = xd.load_json(proto / output.flags_json)
            statuses[str(flags.get("validation_status"))] += 1
            unsupported += len(flags.get("unsupported_recommendation_hits") or [])
    return {
        "moments": len(result.moments),
        "key_moments": len(key),
        "key_moment_times": [round(m.video_time, 1) for m in key],
        "tips": len(next_game_tips(key)),
        "recommendation_families_shown": dict(Counter(families)),
        "repeated_family_recommendations": len(families) - len(set(families)),
        "validation_statuses": dict(statuses),
        "unsupported_recommendation_hits": unsupported,
        "stale": result.stale,
    }


def _family(text: str) -> str:
    hits = [
        t.template_id for t in RECOMMENDATION_TEMPLATES.values() if t.pattern.search(text)
    ]
    return "+".join(sorted(hits)) or "unmatched"


def assemble(out: Path, seed: int = xd.SEED) -> list[dict[str, Any]]:
    """Prototypes, renders, cross-arm evidence checks, blinded packets and key."""
    if (out / "blind").exists():
        raise SystemExit("blind/ exists; packets are written once")
    plan = xd.load_json(out / "calls.json")
    records = _records(out, plan)
    reports = {}
    for rid in plan["reports"]:
        reports[rid] = _assemble_report(out, rid, records)
    problems = [rid for rid, r in reports.items() if not r["evidence_identical"]]
    if problems:
        raise SystemExit(f"public evidence differs across arms: {problems}")
    key_rows = _write_blind(out, reports, seed)
    xd.dump_json(
        out / "key" / "automatic_checks.json",
        {
            "seed": seed,
            "reports": {
                rid: {k: v for k, v in r.items() if k != "packets"}
                for rid, r in reports.items()
            },
        },
    )
    return key_rows


def _assemble_report(
    out: Path, rid: str, records: Mapping[tuple[str, str], Mapping[str, Any]]
) -> dict[str, Any]:
    packets, signatures, checks = {}, {}, {}
    for arm in xd.ARMS:
        shadow = xd.report_dir(out, rid, arm)
        proto = build_prototype(shadow, rid, records)
        result = load_result(shadow)
        signatures[arm] = evidence_signature(result)
        packets[arm] = result
        checks[arm] = arm_checks(proto, result)
    return {
        "evidence_identical": signatures["current"] == signatures["capped"],
        "checks": checks,
        "packets": packets,
    }


def _write_blind(
    out: Path, reports: Mapping[str, Mapping[str, Any]], seed: int
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    order = sorted(reports)
    rng.shuffle(order)
    key_rows = []
    for n, rid in enumerate(order, start=1):
        blind_id = f"R{n}"
        arms = list(xd.ARMS)
        rng.shuffle(arms)
        for version, arm in zip("AB", arms, strict=True):
            path = out / "blind" / blind_id / f"{version}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                render_packet(reports[rid]["packets"][arm], version), encoding="utf-8"
            )
        key_rows.append(
            {"blind_id": blind_id, "report_id": rid, "A": arms[0], "B": arms[1]}
        )
    _write_csv(out / "key" / "rating_key.csv", key_rows)
    _write_csv(
        out / "blind" / "rating_sheet.csv", [_sheet_row(r["blind_id"]) for r in key_rows]
    )
    (out / "blind" / "INSTRUCTIONS.md").write_text(INSTRUCTIONS, encoding="utf-8")
    _write_sums(out, "blind")
    return key_rows


def sheet_columns() -> list[str]:
    """Rating sheet header."""
    cols = ["blind_id"]
    cols += [f"{v}_{name}_{scale}" for v in "AB" for name, scale in MEASURES]
    return cols + ["preferred_A_B_tie", "notes"]


def _sheet_row(blind_id: str) -> dict[str, str]:
    return {c: (blind_id if c == "blind_id" else "") for c in sheet_columns()}


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_sums(out: Path, folder: str) -> None:
    files = sorted(p for p in (out / folder).rglob("*") if p.is_file())
    text = "".join(
        f"{xd.sha256_bytes(p.read_bytes())}  {p.relative_to(out)}\n" for p in files
    )
    (out / f"{folder}.sha256").write_text(text, encoding="utf-8")


INSTRUCTIONS = """# Rating instructions

Each folder `R1` … `R9` holds two versions, `A.md` and `B.md`, of the
coaching report for one match. Read both, then fill one row of
`rating_sheet.csv` per folder. Rate each version on its own:

- usefulness (1–5): overall, how useful the report is for improving.
- actionability (1–5): how clearly it tells you what to do differently.
- specificity (1–5): how specific it is to the situations in this match.
- redundancy (1–5): how repetitive it is; 5 = very repetitive.
- evidence_supported (y/n): every recommendation is supported by the
  report's own match evidence.
- opportunity_lost (y/n): an important coaching opportunity visible in
  the evidence is left without coaching.

Then `preferred_A_B_tie`: `A`, `B` or `tie`. Use `notes` for anything else.
Do not open files outside `blind/` until the sheet is complete.
"""


# --- analyze (after ratings) --------------------------------------------------


def analyze(out: Path, sheet: Path) -> dict[str, Any]:
    """Unblind a completed sheet: per-policy means, paired differences, preferences."""
    key = {
        r["blind_id"]: r for r in csv.DictReader((out / "key" / "rating_key.csv").open())
    }
    rows = list(csv.DictReader(sheet.open(encoding="utf-8")))
    per_arm: dict[str, dict[str, list[float]]] = {a: {} for a in xd.ARMS}
    preferred = Counter()
    for row in rows:
        mapping = key[row["blind_id"]]
        for version in "AB":
            arm = mapping[version]
            for name, scale in MEASURES:
                value = _rating(row[f"{version}_{name}_{scale}"], scale)
                per_arm[arm].setdefault(name, []).append(value)
        choice = row["preferred_A_B_tie"].strip().upper()
        preferred[mapping[choice] if choice in ("A", "B") else "tie"] += 1
    summary = {
        "reports": len(rows),
        "preferred": dict(preferred),
        "means": {
            arm: {k: round(sum(v) / len(v), 2) for k, v in measures.items()}
            for arm, measures in per_arm.items()
        },
        "paired_difference_capped_minus_current": {
            name: [
                c - k
                for c, k in zip(
                    per_arm["capped"][name], per_arm["current"][name], strict=True
                )
            ]
            for name, _ in MEASURES
        },
    }
    xd.dump_json(out / "key" / "rating_results.json", summary)
    return summary


def _rating(text: str, scale: str) -> float:
    value = text.strip().lower()
    if scale == "yn":
        if value not in ("y", "n", "yes", "no"):
            raise SystemExit(f"expected y/n, got {text!r}")
        return 1.0 if value.startswith("y") else 0.0
    number = float(value)
    if not 1 <= number <= 5:
        raise SystemExit(f"expected 1-5, got {text!r}")
    return number
