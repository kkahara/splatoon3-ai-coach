"""Assembly of the `s3-coach` command tree."""

import typer

from splatoon3_ai_coach.cli.analyze import analyze
from splatoon3_ai_coach.cli.calibrate import calibrate_timer_command
from splatoon3_ai_coach.cli.extract import extract
from splatoon3_ai_coach.cli.inspect import inspect
from splatoon3_ai_coach.logging_config import configure_logging

app = typer.Typer(no_args_is_help=True, help="Splatoon 3 gameplay analysis.")


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging."),
) -> None:
    """Configure shared runtime options."""
    configure_logging(verbose)


app.command("inspect")(inspect)
app.command("extract")(extract)
app.command("analyze")(analyze)
app.command("calibrate-timer")(calibrate_timer_command)
