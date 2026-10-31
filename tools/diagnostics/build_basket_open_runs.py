#!/usr/bin/env python3
"""Build an explicit derived basket-run artifact from a vision manifest."""

import argparse
from pathlib import Path

from splatoon3_ai_coach.analysis.basket_open_runs import write_basket_open_runs
from splatoon3_ai_coach.media.vision_manifest import load_vision_manifest


def main() -> None:
    """Generate basket_open_runs.json without changing the source manifest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis_dir", type=Path)
    parser.add_argument(
        "--out", type=Path, required=True, help="Derived output directory"
    )
    args = parser.parse_args()
    path = write_basket_open_runs(load_vision_manifest(args.analysis_dir), args.out)
    print(path)


if __name__ == "__main__":
    main()
