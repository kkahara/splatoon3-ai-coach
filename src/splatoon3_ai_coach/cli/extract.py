"""The `s3-coach extract` command."""

from pathlib import Path

import typer
from rich.console import Console

from splatoon3_ai_coach.analysis.extractor import MeaningfulFrameExtractor
from splatoon3_ai_coach.analysis.manifest import save_manifest
from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.io.video import VideoLoader

DEFAULT_CONFIG_PATH = Path("./configs/default.yaml")
console = Console()


def extract(
    video: Path,
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Output directory. Defaults to paths.frame_output from the config.",
    ),
    config_path: Path = typer.Option(
        DEFAULT_CONFIG_PATH,
        "--config",
        help="Path to a YAML configuration file.",
    ),
) -> None:
    """Extract meaningful gameplay frames and write a JSON manifest."""
    config = load_config(config_path)
    output_dir = out or config.paths.frame_output

    loader = VideoLoader(
        video,
        max_width=config.video.max_width,
        max_height=config.video.max_height,
    )
    with loader:
        extractor = MeaningfulFrameExtractor(config.extraction)
        result = extractor.extract(loader)

    manifest = save_manifest(
        video,
        result,
        output_dir,
        config.extraction.save_jpeg_quality,
    )
    console.print(
        f"Extracted [bold]{len(manifest.frames)}[/bold] frames "
        f"from [bold]{len(manifest.events)}[/bold] events into {output_dir}."
    )
