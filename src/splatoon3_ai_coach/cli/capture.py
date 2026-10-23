"""Commands for capture packages and the frozen timeline-capture-v1 gate."""

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from splatoon3_ai_coach.capture.gate_runner import confirm_check, run_timeline_gate
from splatoon3_ai_coach.capture.models import (
    AcquisitionMethod,
    ArtifactKind,
    CheckStatus,
    GateSummary,
    QualityGateResult,
    SourceType,
)
from splatoon3_ai_coach.capture.package_io import add_artifact, create_package
from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import VisionLanguage
from splatoon3_ai_coach.config.paths import resolve_config_path
from splatoon3_ai_coach.exceptions import ConfigError, S3CoachError

console = Console()
capture_app = typer.Typer(
    no_args_is_help=True, help="Capture packages and quality gates."
)
_ERRORS = (ConfigError, S3CoachError, OSError, ValueError)


def _fail(exc: Exception) -> typer.Exit:
    console.print(f"[red]Error:[/red] {exc}")
    return typer.Exit(code=1)


@capture_app.command("new")
def new(
    package_dir: Path = typer.Argument(..., file_okay=False),
    source: SourceType = typer.Option(..., "--source"),
    language: VisionLanguage = typer.Option(..., "--language"),
    review_code: str | None = typer.Option(None, "--code"),
    method: AcquisitionMethod | None = typer.Option(None, "--method"),
    note: str | None = typer.Option(None, "--note"),
) -> None:
    """Create an empty capture package."""
    default = AcquisitionMethod.MANUAL
    if source is SourceType.UPLOADED_VIDEO:
        default = AcquisitionMethod.UPLOAD
    try:
        package = create_package(
            package_dir,
            source_type=source,
            acquisition_method=method or default,
            language=language,
            review_code=review_code,
            note=note,
        )
    except _ERRORS as exc:
        raise _fail(exc) from exc
    console.print(f"Created package {package.package_id} ({package.source_type.value})")


@capture_app.command("add")
def add(
    package_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    video: Path = typer.Argument(..., exists=True, dir_okay=False),
    kind: ArtifactKind = typer.Option(..., "--kind"),
    playback_rate: str | None = typer.Option(None, "--playback-rate"),
    note: str | None = typer.Option(None, "--note"),
) -> None:
    """Store a recording byte-identical (copy + hash verify); the source is untouched."""
    try:
        artifact = add_artifact(
            package_dir, video, kind, playback_rate=playback_rate, note=note
        )
    except _ERRORS as exc:
        raise _fail(exc) from exc
    props = artifact.properties
    console.print(
        f"Stored {artifact.artifact_id} -> {artifact.path} "
        f"({props.width}x{props.height}, avg {props.avg_frame_rate}, "
        f"r {props.r_frame_rate}, {props.codec}, {props.duration_seconds}s)"
    )


@capture_app.command("check")
def check(
    package_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    artifact_id: str = typer.Option("timeline", "--artifact"),
    config_path: Path | None = typer.Option(None, "--config"),
    replace: bool = typer.Option(False, "--replace"),
) -> None:
    """Run the frozen timeline-capture-v1 gate on a stored timeline artifact."""
    try:
        config = load_config(resolve_config_path(config_path))
        result, summary = run_timeline_gate(
            package_dir, config, artifact_id=artifact_id, replace=replace
        )
    except _ERRORS as exc:
        raise _fail(exc) from exc
    _print_result(result, summary)


@capture_app.command("confirm")
def confirm(
    package_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    check_id: str = typer.Argument(...),
    passed: bool = typer.Option(..., "--pass/--fail"),
    note: str = typer.Option(..., "--note"),
    artifact_id: str = typer.Option("timeline", "--artifact"),
) -> None:
    """Record an operator decision for an unknown or manual check."""
    status = CheckStatus.PASS if passed else CheckStatus.FAIL
    try:
        _, summary = confirm_check(
            package_dir,
            check_id=check_id,
            status=status,
            note=note,
            artifact_id=artifact_id,
        )
    except _ERRORS as exc:
        raise _fail(exc) from exc
    console.print(f"{check_id}: {status.value}; verdict {summary.verdict.value}")


def _print_result(result: QualityGateResult, summary: GateSummary) -> None:
    table = Table(title=f"{result.contract} ({result.contract_status.value})")
    for column in ("check", "method", "status", "detail"):
        table.add_column(column)
    for item in result.checks:
        table.add_row(item.check_id, item.method.value, item.status.value, item.detail)
    console.print(table)
    console.print(
        f"automatic verdict {result.automatic_verdict.value}; "
        f"verdict with confirmations {summary.verdict.value}"
    )
