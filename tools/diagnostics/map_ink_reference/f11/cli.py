"""F11 command dispatcher. Every command passes the guard before doing anything."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from loguru import logger

from map_ink_reference.errors import ProcedureOrderError
from map_ink_reference.f11.commands import eligibility_record, sampling_record
from map_ink_reference.f11.guard import guard

_LATER = (
    "extract",
    "calibrate",
    "stratify",
    "annotate",
    "export",
    "gate",
    "compare",
    "replay",
    "report",
)


def repo_root() -> Path:
    """Repository root, four levels above this file."""
    return Path(__file__).resolve().parents[4]


def build_parser() -> argparse.ArgumentParser:
    """F11 commands. Only ``preconditions`` is authorized after the tooling commit."""
    parser = argparse.ArgumentParser(prog="map_ink_reference.f11")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preconditions")
    commands.add_parser("eligibility")
    commands.add_parser("sample")
    for name in _LATER:
        commands.add_parser(name)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run one F11 command after the guard."""
    args = build_parser().parse_args(argv)
    repo = repo_root()
    tooling = guard(repo)
    if args.command == "preconditions":
        logger.info("F11 preconditions hold at {}", tooling)
        return
    if args.command == "eligibility":
        eligibility_record(repo)
        return
    if args.command == "sample":
        sampling_record(repo)
        return
    raise ProcedureOrderError(f"F11 {args.command} is not authorized at this seal step")
