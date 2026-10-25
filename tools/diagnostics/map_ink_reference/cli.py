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


def cmd_pilot_select(args: argparse.Namespace) -> None:
    """Step 3 selection over the declared pilot sources (after step 2a)."""
    from map_ink_reference.io_utils import write_json
    from map_ink_reference.pilot import select_pilot

    repo = _repo()
    records, work = _paths(repo)
    selection = {**select_pilot(repo, records), "tool": _tool(repo)}
    write_json(work / "pilot_selection.json", selection)
    logger.info("pilot sources: {}", selection["chosen"])


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
    args.func(args)
