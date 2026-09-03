#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

source "$PROJECT_ROOT/.venv/bin/activate"

VIDEO_NAME="2026-07-04 15-27-20.mp4"
VIDEO_PATH="/Users/kenjikahara/Movies/$VIDEO_NAME"
OUTPUT_PATH="$PROJECT_ROOT/frames/$VIDEO_NAME"
CONFIG_PATH="$PROJECT_ROOT/configs/default.yaml"

echo "--------------------------------"
echo "Running command:"
echo "s3-coach inspect $VIDEO_PATH"
echo "--------------------------------"
echo ""
s3-coach --verbose inspect "$VIDEO_PATH"
