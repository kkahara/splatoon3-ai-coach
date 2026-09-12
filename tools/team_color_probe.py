#!/usr/bin/env python3
"""Thin wrapper: print TEAM COLOR PROBE for a vision_manifest.json.

Usage::

    python tools/team_color_probe.py path/to/vision_manifest.json \\
        --config configs/default.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from vision_manifest_viewer.team_colors_probe import main

if __name__ == "__main__":
    raise SystemExit(main())
