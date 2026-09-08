# splatoon3-ai-coach

An extensible Python application for analyzing Splatoon 3 gameplay video and
producing evidence-based coaching.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

For GUI/debug windows that need OpenCV's display backend:

```bash
.venv/bin/pip install -e ".[dev,display]"
```

## Commands

```bash
s3-coach inspect path/to/gameplay.mp4
s3-coach analyze path/to/gameplay.mp4 --out ./analysis/
s3-coach extract path/to/gameplay.mp4 --out ./frames/   # optional debug dumps
s3-coach extract path/to/gameplay.mp4 -v   # verbose logging
```

Config defaults resolve from the repository's `configs/default.yaml` regardless
of cwd. Relative paths in YAML resolve against the config file's directory.

## Project layout

```text
src/splatoon3_ai_coach/
├── config/       schema, YAML loader, path resolution, CoachSettings (env)
├── media/        video decode, manifest read/write
├── extraction/   triggers, sampler, extractor, pipeline
├── vision/       detectors, readings, state fusion, events, pipeline
├── analysis/     GameSession, metrics (Phase 4)
├── coach/        LLM provider, prompts, coaching output (Phase 5)
└── cli/          thin commands wired to pipeline modules

training/         YOLO training + datasets (outside the wheel)
configs/          default.yaml
AGENTS.md         contributor and agent conventions
```

## Development

```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format .
```

See `tests/test_video_loader.py` for a contributor-friendly video I/O example,
and `AGENTS.md` for terminology and extension points.
