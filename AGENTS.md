# Agent and contributor guide

## Pipeline stages

Dependency direction is one-way:

`media` → `extraction` → `vision` → `analysis` → `coach`

| Stage | Package | Responsibility |
|-------|---------|----------------|
| Config | `config/` | Typed YAML schema, path resolution, secrets via env |
| Media | `media/` | Video decode, manifest read/write |
| Extraction | `extraction/` | Change triggers, frame sampling, evidence selection |
| Vision | `vision/` | Semantic detectors (HUD, YOLO) → `GameEvent` |
| Analysis | `analysis/` | `GameSession`, metrics, good-vs-bad scoring |
| Coach | `coach/` | Evidence-constrained LLM coaching |

## Terminology (do not mix these)

- **Trigger** (`TriggerType`, `ChangeTrigger`): pixel-change evidence for keeping a frame
- **Vision detection** (`VisionDetection`): semantic read from a detector on one frame
- **Game event** (`GameEvent`): fused semantic gameplay fact (splat, special used, etc.)

`TriggerType.DEATH_UI_CHANGE` ≠ `GameEventType.DEATH`.

## Code quality rules

- Type hints and docstrings on all public functions
- Functions under ~50 lines; split when they grow
- `loguru` for logging, never `print` in library code
- Pydantic at module boundaries and for anything persisted
- Dataclasses OK for hot-path in-process structs (`VideoFrame`, `ChangeTrigger`)
- No hardcoded paths; resolve via `config.paths` or `config.paths.default_config_path()`
- CLI commands stay thin; orchestration lives in `extraction/pipeline.py` and future `*/pipeline.py` modules

## Adding a vision detector

1. Implement `detect(frame: VideoFrame) -> list[VisionDetection]` with a unique `name`
2. Register in `vision/registry.py`
3. Enable via `vision.enabled_detectors` in YAML

## Tests

- Mirror source layout under `tests/` as the suite grows
- `tests/test_video_loader.py` is the community example for video I/O
- Prefer testing pipeline/service functions over CLI wiring when possible

## Training assets

YOLO datasets and training scripts live in `training/` at the repo root, **not** inside the installable package.
