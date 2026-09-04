# Agent and contributor guide

## Pipeline stages

Dependency direction is one-way:

`media` → `extraction` → `vision` → `analysis` → `coach`

| Stage | Package | Responsibility |
|-------|---------|----------------|
| Config | `config/` | Typed YAML schema, path resolution, secrets via env |
| Media | `media/` | Video decode, manifest read/write |
| Extraction | `extraction/` | Change triggers, frame sampling, evidence selection |
| Vision | `vision/` | Pluggable detectors → readings → fused state → `GameEvent` |
| Analysis | `analysis/` | `GameSession`, metrics, good-vs-bad scoring |
| Coach | `coach/` | Evidence-constrained LLM coaching |

## Terminology (do not mix these)

- **Trigger** (`TriggerType`, `ChangeTrigger`): pixel-change evidence for keeping a frame
- **Reading** (`TimerReading`, `DeathReading`, …): per-frame detector observation
- **Detector result** (`DetectorResult`): a reading plus confidence and provenance IDs
- **Game state** (`GameStateSnapshot`): fused domain state (`match_time_remaining`, `player_alive`, …)
- **Game event** (`GameEvent`): semantic gameplay fact inferred from state transitions

`TriggerType.DEATH_UI_CHANGE` ≠ `GameEventType.DEATH`.  
`DeathReading.detected` ≠ `GameEventType.DEATH`.

## Code quality rules

- Type hints and docstrings on all public functions
- Functions under ~50 lines; split when they grow
- `loguru` for logging, never `print` in library code
- Pydantic at module boundaries and for anything persisted
- Dataclasses OK for hot-path in-process structs (`VideoFrame`, `ChangeTrigger`)
- No hardcoded paths; resolve via `config.paths` or `config.paths.default_config_path()`
- CLI commands stay thin; orchestration lives in `extraction/pipeline.py` and `vision/pipeline.py`
- Prefer concrete submodule imports (`media.video`, `extraction.pipeline`) over package-root re-exports

## Vision language

`VisionConfig.language` (`en` / `ja`) and `VisionConfig.ocr` configure text
templates and OCR. Prefer **language-neutral** cues (icons, colors, layout) for
primary detection. Localized text assets live under
`template_dir/<language>/` when present; helpers are in `vision/language.py`.
Do not gate kill/death events on OCR.

## Adding a vision detector

1. Add a `*Reading` model to `vision/models.py` and include it in the `Reading` union
2. Implement a detector with `name`, `run_on_evidence`, `cadence_fps`, and
   `detect(image: np.ndarray, timestamp: float | None = None) -> tuple[Reading | None, float]`
3. Register it in `vision/registry.py` when listed in `vision.enabled_detectors`
4. Fuse readings into `GameStateSnapshot` in `vision/state.py` (do not emit events here)
5. Add transition rules in `vision/events.py` that consume snapshots only
6. Keep primary cues language-neutral; use `VisionConfig.language` only for
   optional text/OCR enrichment

## Tests

- Mirror source layout under `tests/` as the suite grows
- `tests/test_video_loader.py` is the community example for video I/O
- Prefer testing pipeline/service functions over CLI wiring when possible

## Training assets

YOLO datasets and training scripts live in `training/` at the repo root, **not** inside the installable package.
