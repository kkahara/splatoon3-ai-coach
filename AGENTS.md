# Agent and contributor guide

## Pipeline stages

Dependency direction is one-way:

`media` → `vision` → `analysis` → `coach`

`extraction` is a standalone optional tool (change-triggered JPEG dumps). It is
**not** a prerequisite for vision analysis.

| Stage | Package | Responsibility |
|-------|---------|----------------|
| Config | `config/` | Typed YAML schema, path resolution, secrets via env |
| Media | `media/` | Video decode, manifest read/write |
| Extraction | `extraction/` | Optional change-triggered frame dumps (not used by analyze) |
| Vision | `vision/` | Cadence frames → detectors → readings → fused state → `GameEvent` |
| Analysis | `analysis/` | `GameSession`, metrics, good-vs-bad scoring |
| Coach | `coach/` | Evidence-constrained LLM coaching |

## Terminology (do not mix these)

- **Trigger** (`TriggerType`, `ChangeTrigger`): pixel-change evidence for keeping a frame
- **Reading** (`TimerReading`, `DeathReading`, …): per-frame detector observation
- **Detector result** (`DetectorResult`): a reading plus confidence and provenance IDs
- **Match phase** (`MatchPhase`): mutually exclusive session state
  (`out_of_match` → `intro` → `opening_countdown` → `in_match` → `post_match`)
- **Player lifecycle** (`PlayerLifecycle`): mutually exclusive in-match player state
  (`alive` / `dead` / respawn `countdown` / `respawned` / `awaiting_control`)
- **Game state** (`GameStateSnapshot`): fused domain state (`match_phase`,
  `player_lifecycle`, `match_time_remaining`, …)
- **Game event** (`GameEvent`): one-shot fact inferred from state transitions
  (`DEATH`, `RESPAWN`, `SPLAT`, `ACTIVE_AGAIN`)

`TriggerType.DEATH_UI_CHANGE` ≠ `GameEventType.DEATH`.
`DeathReading.detected` ≠ `GameEventType.DEATH`.
`ActiveGameplayReading.detected` ≠ snapshot `active_gameplay` ≠ `MatchPhase.in_match`.

`player_lifecycle == "countdown"` is the respawn waiting plate.
`match_phase == "opening_countdown"` is frozen 5:00 / 3:00 before GO.

Detectors report evidence and may be true at the same time (e.g. SPLAT + HUD).
Lifecycle decides what that evidence means. DEATH is the first transition into
`dead`; later Ouch frames are continued death evidence, not more DEATH events.

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
2. Implement a detector with `name` and
   `detect(image: np.ndarray, timestamp: float | None = None) -> tuple[Reading | None, float]`
   Cadence scheduling belongs to `vision/pipeline.py`; detectors only observe the
   in-memory frame they are given.
3. Register it in `vision/registry.py` when listed in `vision.enabled_detectors`
4. Fuse readings into `GameStateSnapshot` in `vision/state.py` (do not emit events here)
5. Add transition rules in `vision/events.py` that consume snapshots only
6. Keep primary cues language-neutral; use `VisionConfig.language` only for
   optional text/OCR enrichment
7. Detectors report evidence only. Do not encode match phase, death, or
   respawn policy inside a detector — that belongs to `vision/lifecycle.py`
   and `vision/match_phase.py`. Readings may be true at the same time.

## Tests

- Mirror source layout under `tests/` as the suite grows
- `tests/test_video_loader.py` is the community example for video I/O
- Prefer testing pipeline/service functions over CLI wiring when possible

## Training assets

YOLO datasets and training scripts live in `training/` at the repo root, **not** inside the installable package.
