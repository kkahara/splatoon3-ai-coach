"""Compatibility notice for the removed standalone manifest viewer."""

import typer


def vision_view() -> None:
    """Direct callers of the removed command to the React VMV site."""
    raise typer.BadParameter(
        "The standalone 'vision-view' command was removed. "
        "Start the React Analysis Tool with 's3-coach vmv-site'."
    )
