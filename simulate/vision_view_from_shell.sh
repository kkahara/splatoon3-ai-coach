#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

source "$PROJECT_ROOT/.venv/bin/activate"

#VIDEO_NAME="2026-07-07 23-14-50"
#VIDEO_NAME="2026-07-07 23-49-06"
#VIDEO_NAME="2026-07-06 21-45-19"
VIDEO_NAME="2026-09-05 09-34-24"

# Regenerate the diagnostic HTML next to the manifest (open manually or drop --no-open).
s3-coach vision-view "analysis/${VIDEO_NAME}/vision_manifest.json" --config configs/default.yaml --no-open
