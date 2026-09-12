"""Print TEAM COLOR PROBE for a vision_manifest.json (viewer-only).

Usage::

    python -m vision_manifest_viewer.team_colors_probe path/to/vision_manifest.json \\
        --config configs/default.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vision_manifest_viewer.loader import load_manifest_view
from vision_manifest_viewer.team_colors import format_team_color_probe


def build_parser() -> argparse.ArgumentParser:
    """CLI for offline multi-match team-color probe dumps."""
    parser = argparse.ArgumentParser(
        prog="team-colors-probe",
        description=(
            "Print per-slot HUD team color probe from early debug_snapshots "
            "(viewer-only; not TeamColorCalibration)."
        ),
    )
    parser.add_argument(
        "manifest",
        type=Path,
        help="Path to vision_manifest.json",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional configs/default.yaml for roster slot ROIs",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Load manifest view and print the probe report."""
    args = build_parser().parse_args(argv)
    manifest_path = args.manifest.expanduser().resolve()
    if not manifest_path.exists():
        print(f"manifest not found: {manifest_path}", file=sys.stderr)
        return 1
    config_path = args.config.expanduser().resolve() if args.config else None
    view = load_manifest_view(manifest_path, config_path=config_path)
    print(format_team_color_probe(view.team_colors))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
