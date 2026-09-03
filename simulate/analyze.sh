#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$PROJECT_ROOT/.venv/bin/activate"

VIDEO_PATH="/Users/kenjikahara/Movies/2026-07-07 23-49-06.mov"
OUTPUT_PATH="$PROJECT_ROOT/analysis/2026-07-07 23-49-06"
CONFIG_PATH="$PROJECT_ROOT/configs/default.yaml"


s3-coach -v extract "$VIDEO_PATH" --out "$OUTPUT_PATH/frames"
s3-coach --verbose analyze "$OUTPUT_PATH/frames" --out "$OUTPUT_PATH/analysis" --config "$CONFIG_PATH"