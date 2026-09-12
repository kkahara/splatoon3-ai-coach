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
| Analysis | `analysis/` | `GameSession`, metrics, scenarios, ScenarioContext |
| Coach | `coach/` | Evidence-constrained LLM coaching |

## Scenario / evidence freeze

The Scenario **ownership** layer (`analysis/scenarios.py`, `Scenario.event_ids`,
types, intervals) remains frozen. Do not change grouping or invent speculative
convenience fields (fight quality, overextended, won/lost fight, etc.).

`ScenarioContext` may be extended **only** with sparse factual secondary
evidence required for coaching units:

- map ink observations (`map.ink` from `map_observations.json`)
- roster trajectory from fused `state_snapshots` (`players`)
- special gauge readings + presentation-only ready onset markers (`special`)

Do **not** encode judgments, fight-quality conclusions, “should have used
special”, interpolated continuous state, or invented causal relationships.
Do **not** emit player GameEvents or promote ready onsets to
`SPECIAL_READY` / `SPECIAL_USED` GameEvents.

Derive simple arithmetic in the coaching layer; leave unavailable evidence
unavailable.

### Three-layer terminology

| Layer | Role |
|-------|------|
| **Scenario** | Structural **ownership** via `event_ids` — what events belong here |
| **ScenarioContext** | Deterministic **evidence** (timeline, map overlay + ink, splat, death lifecycle, roster, special, relations) |
| **Coaching** | **Interpretation** / judgment of that evidence |

Ownership rules:

- `DEATH_EPISODE` owns its DEATH, lifecycle events, and in-episode map overlays.
- `ENGAGEMENT` owns **SPLAT events only**. A following DEATH belongs to its
  `DEATH_EPISODE`; it may be referenced via `following_death_id` (compat) and
  ScenarioContext relations, never as an ENGAGEMENT member.

Relations (`leads_to_death_episode_id`, `preceded_by_engagement_id`, …) and
`trade_candidate` are **temporal associations / window flags**, never causal
proof or fight quality. Scenario outcome (`fragged` / `died`) is closed
vocabulary for linkage, not win/lose.

`map_check_before_death` means **any** map overlay before the death (unbounded
lookback). It does **not** mean a short pre-death check window. Map **ink**
samples (`map.ink`) are separate from MAP_OVERLAY facts and never set overlay
`map_check_*` fields.

### Dual clocks (video vs game)

- **Video time** (`GameEvent.start_time`, scenario intervals) is the canonical
  clock for identity, ordering, grouping, and relationships.
- **Game clock** (`coach.game_clock.GameClock`) is secondary coaching evidence:
  raw usable `TimerReading` detections mapped from `VisionFrameResult`.
- Do **not** use game time for scenario construction or fusion.
- Do **not** present fused/held/smoothed `GameStateSnapshot.match_time_remaining`
  as observed coaching clock evidence.
- Do **not** invent remaining time as `300 - video_time`.

### Player-count samples (roster state)

- Secondary evidence from fused `GameStateSnapshot` alive counts persisted on
  `vision_manifest.state_snapshots` (not re-fused in coach).
- Detector cue: HUD **roster X markers** (dark X on teammate/opponent icons)
  in configured player-slot ROIs (`PlayerCountReading`). Field names may still
  say `*_dead_slots` — that means “X present on that slot,” **not** local-player
  `DEATH`. Coaching vocabulary: `ally_alive_count` / `opponent_alive_count` only.
- Describes **roster state at a video time**, not who participated in an
  engagement. Never infer counts from splat/death/scenario membership.
- Sparse roster trajectory may appear on `ScenarioContext.players` as factual
  secondary evidence. Do **not** emit player GameEvents.
- **Stage 1:** `CoachInput.player_count_*` remains unchanged (still built from
  `PlayerCountClock`); ScenarioContext players do not replace CoachInput APIs.

### Match intro + 2D map ink

- `MatchIntroDetector` (templates) captures `stage_id` + `battle_mode_id`.
- `MAP_OVERLAY` remains the visibility event/interval (`MapOverlayDetector`).
- `MapObservation` is a sparse ink sample while the map is open — **not** a
  `GameEvent`, not interpolated continuous state. See `vision/MAP_INK.md`.
- Sparse ink samples may attach to `ScenarioContext.map.ink` (facts only).
- Map ink is disabled for the match if **stage** identity is unresolved.
- Map ink is **not** an `enabled_detectors` entry; it is gated by
  `vision.map_ink.enabled` (+ stage identity). Missing battle mode falls back to
  `configs/stage_maps/<stage_id>/default.yaml`.

### CoachInput

Coaching consumes **one unit** at a time via `coach.coach_input.CoachInput`:

- primary `Scenario` + `ScenarioContext`
- related scenarios resolved only from existing `relations`
- `GameClock` samples at labeled video times (provenance preserved)
- `player_count_samples` at the same labeled times when fused counts exist
- `player_count_window` / `player_count_context` around the unit anchor when
  defined (present_by / duration derived in coaching; Stage 1 unchanged)
- `EvidenceLimit` statements for what the unit cannot establish

`CoachInput` is evidence only — not good/bad play, advice, or fight quality.

Prototype path (does not reopen evidence builders):

```text
CoachInput → Ollama (gpt-oss:20b / llama3.1:8b) → CoachingAssessment
```

Claim-pattern flags are **experiment annotations only**; they never rewrite
model output. CLI: `s3-coach coach-prototype`.

## Coaching evidence contract

`ScenarioContext` must not encode judgments (fight quality, map advice, gear blame).

Contract source of truth:

- `src/splatoon3_ai_coach/coach/EVIDENCE_CONTRACT.md`
- `src/splatoon3_ai_coach/coach/evidence_contract.py`
- system prompt: `coach/prompts/coach_system.txt`

Do not modify scenario grouping to satisfy coaching wording. Tests live in
`tests/test_coach_evidence_contract.py`.

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

### Local-player death vs roster X (do not conflate)

| Term | Layer | Meaning |
|------|--------|---------|
| `TriggerType.DEATH_UI_CHANGE` | extraction | Pixel change in death-UI ROI (frame keep cue) |
| `DeathReading` / detector `death` | vision reading | Local Ouch/banner UI observation |
| `player_lifecycle == "dead"` | fused state | Continuous local-player state after accepted death UI |
| `GameEventType.DEATH` | event | One-shot first transition into local `dead` |
| `ScenarioType.DEATH_EPISODE` | analysis | Ownership group around that local death lifecycle |
| Roster X / `*_dead_slots` | player_count | X on teammate/opponent icons → alive counts only |

Prefer saying **roster X marker** for player-count evidence. Do not call it a
`DEATH` event or local-player death.
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
