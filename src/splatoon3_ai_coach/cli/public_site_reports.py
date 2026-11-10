"""The `s3-coach public-site-reports` commands (report publishing operations).

Output never includes credentials, URLs, signatures, object contents or
personal fields.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from splatoon3_ai_coach.cli.repo_tools import require_repo_tools

public_site_reports_app = typer.Typer(
    no_args_is_help=True, help="Inspect and maintain published report bundles."
)


def _settings():
    require_repo_tools("public-site-reports")
    from public_site.settings import PublicSettings

    settings = PublicSettings.from_env()
    problem = settings.report_problem()
    if problem is not None:
        raise typer.BadParameter(problem)
    return settings


def _echo_json(payload: object) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True, default=sorted))


@public_site_reports_app.command("status")
def status(
    remote: bool = typer.Option(False, "--remote", help="Also LIST reports/ in R2."),
    details: bool = typer.Option(False, "--details", help="One row per submission."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Record counts, settings, staging and (with --remote) per-run storage."""
    settings = _settings()
    from public_site.report_ops import status as report_status

    try:
        payload = report_status(settings, remote=remote, details=details)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if as_json:
        _echo_json(payload)
        return
    _print_status(payload)


def _print_status(payload: dict) -> None:
    local = payload["local"]
    typer.echo("records: " + " ".join(f"{k}={v}" for k, v in local["records"].items()))
    for rule, count in sorted(local["blocked_rules"].items()):
        typer.echo(f"  blocked {rule}: {count}")
    typer.echo(f"delete_pending overdue: {local['delete_pending_overdue']}")
    typer.echo(
        f"superseded runs: {local['superseded_runs']} "
        f"(records: {local['records_with_superseded_runs']})"
    )
    typer.echo("settings: " + " ".join(f"{k}={v}" for k, v in local["settings"].items()))
    typer.echo("staging: " + " ".join(f"{k}={v}" for k, v in local["staging"].items()))
    remote = payload.get("remote")
    if remote is None:
        return
    typer.echo(f"bucket: {remote['bucket']}")
    typer.echo(f"{'ROW':<16}{'RUNS':>8}{'OBJECTS':>10}{'BYTES':>16}")
    for name, row in remote["table"].items():
        typer.echo(f"{name:<16}{row['runs']:>8}{row['objects']:>10}{row['bytes']:>16}")
    for item in remote.get("submissions", []):
        typer.echo(" ".join(f"{k}={v}" for k, v in item.items()))


@public_site_reports_app.command("scan")
def scan(
    analysis_root: Path | None = typer.Option(
        None, "--analysis-root", help="Folder of analyses (default PUBLIC_ROOT/analysis)."
    ),
    inventory: Path | None = typer.Option(
        None, "--inventory", help="Key-path inventory JSON to diff against."
    ),
    write_inventory: Path | None = typer.Option(
        None, "--write-inventory", help="Write the scanned key-path inventory here."
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Read-only content-policy scan of local analyses. Counts by rule only."""
    settings = _settings()
    from public_site.report_ops import inventory_diff, inventory_payload
    from public_site.report_ops import scan as run_scan

    report = run_scan(settings, analysis_root)
    payload: dict = {
        "analyses": report.analyses,
        "analyses_clean": report.analyses_clean,
        "files": report.files,
        "findings": dict(sorted(report.findings.items())),
        "clips_with_metadata": report.clips_with_metadata,
    }
    if inventory is not None:
        expected = json.loads(inventory.read_text(encoding="utf-8"))
        payload["inventory_diff"] = inventory_diff(report, expected)
    if write_inventory is not None:
        write_inventory.write_text(
            json.dumps(inventory_payload(report), indent=2) + "\n", encoding="utf-8"
        )
    _echo_json(payload) if as_json else _print_mapping(payload)


def _print_mapping(payload: dict, indent: str = "") -> None:
    for key, value in payload.items():
        if isinstance(value, dict):
            typer.echo(f"{indent}{key}:")
            _print_mapping(value, indent + "  ")
        elif isinstance(value, list):
            typer.echo(f"{indent}{key}: {len(value)}")
            for item in value:
                typer.echo(f"{indent}  {item}")
        else:
            typer.echo(f"{indent}{key}: {value}")


@public_site_reports_app.command("sweep")
def sweep(
    apply: bool = typer.Option(False, "--apply", help="Delete the planned prefixes."),
    confirm: str = typer.Option("", "--confirm", help="Plan digest from the dry run."),
    allow_large: bool = typer.Option(
        False, "--allow-large", help="Lift only the size threshold."
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Dry run by default. Apply needs --apply --confirm <digest>."""
    settings = _settings()
    from public_site.report_ops import sweeper
    from public_site.report_sweep import SweepAborted

    try:
        worker = sweeper(settings)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not apply:
        plan = worker.plan()
        _echo_json(plan.as_dict()) if as_json else _print_plan(plan.as_dict())
        return
    if not confirm:
        raise typer.BadParameter("--apply requires --confirm <plan digest>")
    try:
        result = worker.apply(confirm, allow_large=allow_large)
    except SweepAborted as exc:
        typer.echo(f"sweep aborted: {exc}", err=True)
        raise typer.Exit(2) from exc
    _echo_json(result.__dict__)


def _print_plan(plan: dict) -> None:
    typer.echo(
        f"mode={plan['mode']} bucket={plan['bucket']} at={plan['generated_at']} "
        f"threshold=max({plan['threshold_min']}, {plan['threshold_fraction']:.0%}) "
        f"limit={plan['limit']} prefixes={plan['total_prefixes']}"
    )
    for row in plan["rows"]:
        typer.echo(
            f"DELETE {row['prefix']} reason={row['reason']} "
            f"objects={row['object_count']} bytes={row['bytes']} "
            f"projection={row['projection_status']} "
            f"record={row['record_state']} age_s={row['newest_object_age_seconds']}"
        )
    for skip in plan["skips"]:
        typer.echo(f"skip   {skip['prefix']} reason={skip['reason']}")
    totals = plan["totals"]
    typer.echo(
        f"rows={totals['rows']} objects={totals['objects']} bytes={totals['bytes']} "
        f"skips={totals['skips']} exceeds_threshold={plan['exceeds_threshold']}"
    )
    typer.echo(f"plan digest: {plan['digest']}")


@public_site_reports_app.command("publish")
def publish(
    retry_blocked: str = typer.Option(
        ..., "--retry-blocked", help="Submission ID of a blocked record to retry."
    ),
) -> None:
    """Move one blocked record back to pending (local record only)."""
    settings = _settings()
    from public_site.report_ops import retry_blocked as retry

    try:
        changed = retry(settings, retry_blocked)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not changed:
        typer.echo("no blocked record for that submission", err=True)
        raise typer.Exit(1)
    typer.echo("record moved to pending")
