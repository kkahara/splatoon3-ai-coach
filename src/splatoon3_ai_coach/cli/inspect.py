"""The `s3-coach inspect` command."""

from pathlib import Path

from rich.console import Console
from rich.table import Table

from splatoon3_ai_coach.io.video import VideoLoader

console = Console()


def inspect(video: Path) -> None:
    """Inspect a video and print its media metadata."""
    with VideoLoader(video) as loader:
        metadata = loader.open()

    table = Table(title="Video")
    table.add_column("Property")
    table.add_column("Value")
    table.add_row("Path", str(metadata.path))
    table.add_row("Format", metadata.format_name)
    table.add_row("FPS", f"{metadata.fps:.3f}")
    table.add_row("Duration", f"{metadata.duration_seconds:.3f} s")
    table.add_row("Resolution", f"{metadata.width} x {metadata.height}")
    table.add_row("Video stream", str(metadata.video_stream_index))
    table.add_row("Metadata streams", str(metadata.metadata_stream_indices))
    console.print(table)
