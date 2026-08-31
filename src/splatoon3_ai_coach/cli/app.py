"""Assembly of the `s3-coach` command tree."""

import typer

from splatoon3_ai_coach.cli.extract import extract
from splatoon3_ai_coach.cli.inspect import inspect

app = typer.Typer(no_args_is_help=True, help="Splatoon 3 gameplay analysis.")
app.command("inspect")(inspect)
app.command("extract")(extract)
