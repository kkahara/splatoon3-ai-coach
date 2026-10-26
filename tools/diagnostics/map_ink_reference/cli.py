"""Command line for the Amendment 3 reference procedure (Amendment 4 conformant).

Every real-run command requires committed (clean) tooling and records the
tool commit. Blind commands accept only recording ids, stage numbers, a
blind-input scope, a video path and a port; none accepts calibration,
observation, classifier, comparison or criterion-4a inputs. Stage identity
comes only from the sealed blind-input manifest; Stage 2 membership only
from the sealed trigger.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from loguru import logger

from map_ink_reference.constants import (
    AMENDMENT4_COMMIT,
    CORRECTNESS_APPLICABLE,
    PILOT_ALLOWED_CHANGES,
    RECORDS_DIR,
    SCOPE_CORPUS,
    SCOPE_PILOT,
    STAGE_1,
    WORK_DIR,
)
from map_ink_reference.seal import require_tool_clean, verify_preconditions
from map_ink_reference.unblinded_pins import A1_IMPLEMENTATION_PINS

TOOL_DIR = "tools/diagnostics/map_ink_reference"


def _repo() -> Path:
    return Path(__file__).resolve().parents[3]


def _tool(repo: Path) -> dict[str, Any]:
    return {"tool_commit": require_tool_clean(repo, repo / TOOL_DIR)}


def _paths(repo: Path) -> tuple[Path, Path]:
    return repo / RECORDS_DIR, repo / WORK_DIR


def cmd_preconditions(args: argparse.Namespace) -> None:
    """Sealing step 1 (A1 committed and matching; Amendment 4 committed)."""
    verify_preconditions(_repo(), A1_IMPLEMENTATION_PINS)
    logger.info("preconditions hold")


def cmd_blind_inputs(args: argparse.Namespace) -> None:
    """Step 2a: pilot or corpus blind-input manifest (after the prior 2a record)."""
    from map_ink_reference.intro_latch import write_blind_input_manifest

    repo = _repo()
    records, _ = _paths(repo)
    verify_preconditions(repo, A1_IMPLEMENTATION_PINS)
    out = write_blind_input_manifest(
        repo,
        records,
        args.scope,
        tool_commit=_tool(repo)["tool_commit"],
        amendment4_commit=AMENDMENT4_COMMIT,
    )
    logger.info("wrote {}", out)


def _pilot_scope(repo: Path):
    from map_ink_reference.pilot_pipeline import PilotScope

    return PilotScope.from_repo(repo)


def cmd_pilot_select(args: argparse.Namespace) -> None:
    """G1: step 3 selection over the declared pilot sources (after step 2a)."""
    from map_ink_reference.pilot_pipeline import write_selection

    repo = _repo()
    output, digest = write_selection(_pilot_scope(repo), _tool(repo))
    logger.info("pilot selection: {} sha256={}", output, digest)


def cmd_pilot_plan(args: argparse.Namespace) -> None:
    """G2: both pilot frame plans (no decode)."""
    from map_ink_reference.pilot_pipeline import write_plans

    repo = _repo()
    logger.info("pilot plans: {}", write_plans(_pilot_scope(repo), _tool(repo)))


def cmd_pilot_extract(args: argparse.Namespace) -> None:
    """G2: extract both chosen pilot recordings (nothing is displayed)."""
    from map_ink_reference.pilot_pipeline import extract_plans

    repo = _repo()
    extract_plans(_pilot_scope(repo), _tool(repo))
    logger.info("pilot extraction complete")


def cmd_pilot_points(args: argparse.Namespace) -> None:
    """G2: pilot points and duplicates with the frozen point procedure."""
    from map_ink_reference.pilot_pipeline import write_points

    repo = _repo()
    logger.info("pilot points: {}", write_points(_pilot_scope(repo), _tool(repo)))


def cmd_pilot_annotate(args: argparse.Namespace) -> None:
    """Serve the blind pilot annotation UI (after G2)."""
    from map_ink_reference.pilot_pipeline import session_dir, session_inputs
    from map_ink_reference.server import serve
    from map_ink_reference.session import AnnotationSession

    scope = _pilot_scope(_repo())
    serve(AnnotationSession(session_inputs(scope), session_dir(scope)), args.port)


def cmd_pilot_export(args: argparse.Namespace) -> None:
    """G3/G4: export the completed pilot session (file names only)."""
    from map_ink_reference.pilot_pipeline import export

    repo = _repo()
    logger.info("pilot records: {}", sorted(export(_pilot_scope(repo), _tool(repo))))


def cmd_pilot_gate(args: argparse.Namespace) -> None:
    """G5: the frozen Stage 1 duplicate gate over the pilot records."""
    from map_ink_reference.pilot_pipeline import gate

    repo = _repo()
    result = gate(_pilot_scope(repo), _tool(repo))
    logger.info(
        "pilot gate: numerator={} denominator={} upper_bound={} passed={}",
        result["numerator"],
        result["denominator"],
        result["upper_bound"],
        result["passed"],
    )


def cmd_pilot_addendum(args: argparse.Namespace) -> None:
    """Prospective G6: the closed pilot addendum after a sealed passing G5."""
    from map_ink_reference.pilot_addendum import write_addendum

    output = write_addendum(
        _pilot_scope(_repo()),
        changes=args.item or [],
        interface_commit=args.interface_commit,
    )
    logger.info("pilot addendum: {}", output)


def _pilot2_scope(repo: Path):
    from map_ink_reference.pilot2_pipeline import Pilot2Scope

    return Pilot2Scope.from_repo(repo)


def _repair9(repo: Path) -> str:
    from map_ink_reference.execution import verify_repairs

    return verify_repairs(repo)[2]


def cmd_pilot1_close_gate(args: argparse.Namespace) -> None:
    """Write pilot-1 G5 once without exposing any outcome field."""
    from map_ink_reference.pilot_pipeline import GATE, gate

    repo = _repo()
    scope = _pilot_scope(repo)
    gate(scope, _tool(repo))
    logger.info("pilot gate record written {}", scope.records / GATE)


def cmd_pilot1_gate_report(args: argparse.Namespace) -> None:
    """After H5, expose only the four authorized pilot-1 summary fields."""
    from map_ink_reference.pilot_pipeline import GATE, read_pilot_record

    scope = _pilot_scope(_repo())
    result = read_pilot_record(scope.records / GATE)
    logger.info(
        "pilot gate: numerator={} denominator={} upper_bound={} passed={}",
        result["numerator"],
        result["denominator"],
        result["upper_bound"],
        result["passed"],
    )


def cmd_pilot2_select(args: argparse.Namespace) -> None:
    """H1: deterministic replacement-pilot source selection."""
    from map_ink_reference.pilot2_pipeline import write_selection

    repo = _repo()
    output, digest = write_selection(_pilot2_scope(repo), _tool(repo))
    logger.info("pilot2 selection: {} sha256={}", output, digest)


def cmd_pilot2_plan(args: argparse.Namespace) -> None:
    """H2: build both pilot-2 frame plans."""
    from map_ink_reference.pilot2_pipeline import write_plans

    repo = _repo()
    logger.info(
        "pilot2 plans: {}",
        write_plans(_pilot2_scope(repo), _tool(repo), predecessor=_repair9(repo)),
    )


def cmd_pilot2_extract(args: argparse.Namespace) -> None:
    """H2: extract both selected pilot-2 recordings."""
    from map_ink_reference.pilot2_pipeline import extract_plans

    repo = _repo()
    extract_plans(_pilot2_scope(repo), _tool(repo), predecessor=_repair9(repo))
    logger.info("pilot2 extraction complete")


def cmd_pilot2_points(args: argparse.Namespace) -> None:
    """H2: sample pilot-2 points and frozen duplicates."""
    from map_ink_reference.pilot2_pipeline import write_points

    repo = _repo()
    outputs = write_points(_pilot2_scope(repo), _tool(repo), predecessor=_repair9(repo))
    logger.info("pilot2 points: {}", outputs)


def cmd_pilot2_annotate(args: argparse.Namespace) -> None:
    """Serve the required human pilot-2 annotation session."""
    from map_ink_reference.pilot2_pipeline import session_dir, session_inputs
    from map_ink_reference.server import serve
    from map_ink_reference.session import AnnotationSession

    repo = _repo()
    scope = _pilot2_scope(repo)
    inputs = session_inputs(scope, predecessor=_repair9(repo))
    serve(AnnotationSession(inputs, session_dir(scope)), args.port)


def cmd_pilot2_export(args: argparse.Namespace) -> None:
    """Export completed W2 human annotation to H3 and pending H4."""
    from map_ink_reference.pilot2_pipeline import export

    repo = _repo()
    outputs = export(_pilot2_scope(repo), _tool(repo), predecessor=_repair9(repo))
    logger.info("pilot2 records: {}", sorted(outputs))


def cmd_pilot2_gate(args: argparse.Namespace) -> None:
    """H5: run the unchanged Stage 1 gate once."""
    from map_ink_reference.pilot2_pipeline import gate

    repo = _repo()
    result = gate(_pilot2_scope(repo), _tool(repo), predecessor=_repair9(repo))
    logger.info(
        "pilot2 gate: numerator={} denominator={} upper_bound={} passed={}",
        result["numerator"],
        result["denominator"],
        result["upper_bound"],
        result["passed"],
    )


def cmd_pilot2_addendum(args: argparse.Namespace) -> None:
    """H6: write the frozen empty/null addendum directly after passing H5."""
    from map_ink_reference.pilot2_addendum import write_addendum

    repo = _repo()
    output = write_addendum(_pilot2_scope(repo), predecessor=_repair9(repo))
    logger.info("pilot2 addendum: {}", output)


def cmd_plan(args: argparse.Namespace) -> None:
    """Frame plan from the pinned v3-replay manifest (metadata only)."""
    from map_ink_reference.pipeline_blind import write_plan

    repo = _repo()
    records, _ = _paths(repo)
    logger.info("wrote {}", write_plan(repo, records, args.recording, _tool(repo)))


def cmd_extract(args: argparse.Namespace) -> None:
    """Decode the pinned video and write the planned frames."""
    from map_ink_reference.pipeline_blind import run_extraction

    repo = _repo()
    records, work = _paths(repo)
    out = run_extraction(records, work, args.recording, Path(args.video), _tool(repo))
    logger.info("wrote {}", out)


def cmd_points(args: argparse.Namespace) -> None:
    """Point manifest for a stage (Stage 2 ids must equal the sealed trigger)."""
    from map_ink_reference.pipeline_blind import write_point_manifest

    repo = _repo()
    records, work = _paths(repo)
    recordings = (
        list(CORRECTNESS_APPLICABLE) if args.stage == STAGE_1 else args.recordings
    )
    write_point_manifest(records, work, args.stage, recordings, _tool(repo))


def cmd_annotate(args: argparse.Namespace) -> None:
    """Serve the blind annotation UI for a stage."""
    from map_ink_reference.pipeline_blind import session_inputs
    from map_ink_reference.server import serve
    from map_ink_reference.session import AnnotationSession

    repo = _repo()
    records, work = _paths(repo)
    _tool(repo)
    session = AnnotationSession(
        session_inputs(records, work, args.stage), work / f"session_stage{args.stage}"
    )
    serve(session, args.port)


def cmd_export(args: argparse.Namespace) -> None:
    """Sealing step: resolve the session's answer record and write its records."""
    from map_ink_reference.records import export_session_records

    repo = _repo()
    records, work = _paths(repo)
    state_dir = work / f"session_stage{args.stage}"
    written = export_session_records(state_dir, records, tool=_tool(repo))
    logger.info("wrote {}", sorted(written))


def cmd_gate(args: argparse.Namespace) -> None:
    """Duplicate gate over sealed labels (Stage 2 pools D_1 ∪ D_2)."""
    from map_ink_reference.pipeline_blind import run_gate

    repo = _repo()
    records, _ = _paths(repo)
    logger.info("gate: {}", run_gate(records, args.stage, _tool(repo)))


def cmd_criterion_4a(args: argparse.Namespace) -> None:
    """Step 5a: criterion 4a from the pinned A1 artifacts (frozen rule)."""
    from map_ink_reference.criterion_4a import write_criterion_4a

    repo = _repo()
    records, _ = _paths(repo)
    payload = write_criterion_4a(records, _tool(repo))
    logger.info("criterion 4a: {}", payload["verdict_4a"])


def cmd_compare(args: argparse.Namespace) -> None:
    """Unblinded comparison (sealed labels, keys, gate and criterion 4a)."""
    from map_ink_reference.pipeline_unblinded import run_comparison

    repo = _repo()
    records, work = _paths(repo)
    run_comparison(records, work, args.stage, _tool(repo))
    logger.info("comparison written")


def cmd_report(args: argparse.Namespace) -> None:
    """Final report: 4a from the sealed record, 4b and criterion 4."""
    from map_ink_reference.report import build_report

    repo = _repo()
    records, _ = _paths(repo)
    payload = build_report(records, _tool(repo))
    logger.info("4b={} criterion4={}", payload["verdict_4b"], payload["criterion_4"])


def build_parser() -> argparse.ArgumentParser:
    """Argument parser (exposed for the parameter isolation test)."""
    parser = argparse.ArgumentParser(prog="map_ink_reference")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preconditions").set_defaults(func=cmd_preconditions)
    p = sub.add_parser("blind-inputs")
    p.add_argument("--scope", required=True, choices=(SCOPE_PILOT, SCOPE_CORPUS))
    p.set_defaults(func=cmd_blind_inputs)
    sub.add_parser("pilot-select").set_defaults(func=cmd_pilot_select)
    sub.add_parser("pilot-plan").set_defaults(func=cmd_pilot_plan)
    sub.add_parser("pilot-extract").set_defaults(func=cmd_pilot_extract)
    sub.add_parser("pilot-points").set_defaults(func=cmd_pilot_points)
    p = sub.add_parser("pilot-annotate")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_pilot_annotate)
    sub.add_parser("pilot-export").set_defaults(func=cmd_pilot_export)
    sub.add_parser("pilot-gate").set_defaults(func=cmd_pilot_gate)
    p = sub.add_parser("pilot-addendum")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--none", action="store_true")
    group.add_argument("--item", action="append", choices=PILOT_ALLOWED_CHANGES)
    p.add_argument("--interface-commit")
    p.set_defaults(func=cmd_pilot_addendum)
    sub.add_parser("pilot1-close-gate").set_defaults(func=cmd_pilot1_close_gate)
    sub.add_parser("pilot1-gate-report").set_defaults(func=cmd_pilot1_gate_report)
    sub.add_parser("pilot2-select").set_defaults(func=cmd_pilot2_select)
    sub.add_parser("pilot2-plan").set_defaults(func=cmd_pilot2_plan)
    sub.add_parser("pilot2-extract").set_defaults(func=cmd_pilot2_extract)
    sub.add_parser("pilot2-points").set_defaults(func=cmd_pilot2_points)
    p = sub.add_parser("pilot2-annotate")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_pilot2_annotate)
    sub.add_parser("pilot2-export").set_defaults(func=cmd_pilot2_export)
    sub.add_parser("pilot2-gate").set_defaults(func=cmd_pilot2_gate)
    sub.add_parser("pilot2-addendum").set_defaults(func=cmd_pilot2_addendum)
    for name, func in (("plan", cmd_plan), ("extract", cmd_extract)):
        p = sub.add_parser(name)
        p.add_argument("--recording", required=True, choices=CORRECTNESS_APPLICABLE)
        if name == "extract":
            p.add_argument("--video", required=True)
        p.set_defaults(func=func)
    p = sub.add_parser("points")
    p.add_argument("--stage", type=int, choices=(1, 2), required=True)
    p.add_argument("--recordings", nargs="*", default=[], choices=CORRECTNESS_APPLICABLE)
    p.set_defaults(func=cmd_points)
    p = sub.add_parser("annotate")
    p.add_argument("--stage", type=int, choices=(1, 2), required=True)
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_annotate)
    for name, func in (
        ("export", cmd_export),
        ("gate", cmd_gate),
        ("compare", cmd_compare),
    ):
        p = sub.add_parser(name)
        p.add_argument("--stage", type=int, choices=(1, 2), required=True)
        p.set_defaults(func=func)
    sub.add_parser("criterion-4a").set_defaults(func=cmd_criterion_4a)
    sub.add_parser("report").set_defaults(func=cmd_report)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point."""
    args = build_parser().parse_args(argv)
    from map_ink_reference.execution import guard

    guard(_repo(), args.command)
    args.func(args)
