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
| Review | `review/` | Local Review timeline screenshots → elapsed-clock evidence |
| Alignment | `alignment/` | Declared Review import + gameplay analysis pair → Review remaining clock to gameplay video interval |
| Capture | `capture/` | Acquisition boundary: capture packages (byte-identical artifacts + provenance) and versioned capture-contract gates |

### Future vision layers

Extension path without rewriting the pipeline contract:

- **Detector** = per-frame `Reading` only (`BaseDetector.detect`); no phase/death
  policy; no track IDs as identity of GameEvents.
- **Tracker / associator** (`BaseTracker`) = sibling to splat-style fusers
  (temporal identity across frames); lives under `vision/` but **not** inside
  `detect()` and **not** registered as `enabled_detectors`.
- **Secondary spatial evidence** (YOLO boxes, depth, segmentation masks) =
  readings or sparse artifacts → ScenarioContext / CoachInput later; do **not**
  invent Scenario types for them until the evidence contract allows.
- Match side-effects (Ready gate, team color, map ink) stay co-located; map ink
  is not an `enabled_detectors` entry.
- Training assets stay under `training/` at the repo root.

## Scenario / evidence freeze

The Scenario **ownership** layer (`analysis/scenarios.py`, `Scenario.event_ids`,
types, intervals) remains frozen. Do not change grouping or invent speculative
convenience fields (fight quality, overextended, won/lost fight, etc.).

`ScenarioContext` may be extended **only** with sparse factual secondary
evidence required for coaching units:

- map ink observations (`map.ink` from `map_observations.json`)
- roster trajectory from fused `state_snapshots` (`players`)
- special gauge readings + presentation-only ready onset markers (`special`)
- `LOW_INK` intervals overlapping the scenario interval (`low_ink`)
- Splat Zones remaining counts + penalties from fused `state_snapshots`
 (`score`: observed samples at the anchor and pre-death; mode-gated; never
 zone holder / objective control)
- Splat Zones control state and confirmed team-level transitions from fused
  `state_snapshots` (`zone_control`: sparse observed/held samples and
  transitions; mode-gated; never player attribution or causal proof)

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
- `LOW_INK` is owned by **no** scenario. It reaches `ScenarioContext.low_ink`
  only when its interval overlaps the scenario interval (closed intervals,
  no proximity window) — see `analysis/low_ink_context.py`.
- `MATCH_END` is owned by **no** scenario. It bounds every scenario interval
  (documented exception to the interval freeze; grouping and `event_ids` are
  unchanged). See **Match end** below.

### Match end (`MATCH_END`)

- Deterministic `GameEvent` decided offline in `vision/match_end.py` from raw
  usable `TimerReading`s (look-ahead over the whole scan), wired in
  `vision/pipeline.py` for analyze and `s3-coach refuse`.
- **The game clock reaching 0:00 is authoritative** (`reason=clock_zero`):
  the first usable 0:00 whose previous reading was ≤
  `vision.match_end.zero_max_previous_seconds`, with no usable timer reading
  more than `zero_tail_seconds` later.
- Ranked **overtime** replaces the digits with an OVERTIME banner (clock reads
  1 → no reading, never 0:00) while play continues for tens of seconds; a
  **knockout** ends with time left. Neither has a trustworthy 0:00, so by
  default there is **no** MATCH_END (domain = whole video). The opt-in
  `vision.match_end.hud_loss_fallback` (`reason=hud_loss`: last timer reading
  before the timer is gone longer than `lifecycle.match_context_hold_seconds`)
  cuts real overtime play on stored matches — keep it off until a FINISH
  detector can confirm. Never present `hud_loss` as a clock reading.
- The yellow FINISH screen is only visual confirmation
  (`GameEvent.confirmation="finish_screen"`); there is no detector yet, so the
  field stays `None`.
- The 0:00 frame is the boundary, not gameplay. Snapshots at or after
  MATCH_END are `match_phase="post_match"`; no other event starts there; open
  `MAP_OVERLAY` / `LOW_INK` intervals close at it.
- A `DEATH_EPISODE` still open at MATCH_END ends there with
  `end_reason="match_end"` (outcome stays `incomplete`). Never invent a
  respawn.
- **Invariant: no coaching evidence may originate after `MATCH_END`.**
  ScenarioContext evidence (`load_scenario_evidence_pack`) and CoachInput
  clocks (`coach/load_analysis.py`) drop samples at or after it via
  `analysis/match_bounds.py`. Without a MATCH_END the domain is the whole
  video.

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
- A frame where any slot ROI lacks icon edges (map open, respawn map) is read
 as `roster_visible=False` with zero confidence: no roster evidence, not
 “everyone alive.”
- Fusion debounces each side: a changed count needs
 `vision.player_count.confirm_readings` consecutive agreeing readings; one-frame
 misreads never become the fused count. `s3-coach refuse <analysis_dir>` re-runs
 fusion from stored `frame_results` (no decode) after fusion settings change.
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
analyze → coach-inputs → CoachInput + claims
coach-prototype → system prompt + selected user prompts
coach-prototype --call-llm → Ollama / NVIDIA → CoachingAssessment
```

Claim-pattern flags are **experiment annotations only**; they never rewrite
model output. CLI: `s3-coach coach-inputs`, then `s3-coach coach-prototype`.
Add `--call-llm` to call the provider for `selected_for_llm` units.

### Review SPECIAL_USE facts

Contract: `src/splatoon3_ai_coach/review/SPECIAL_USE_FUSION.md`.
`s3-coach review special-facts <import_dir>` writes `special_use_facts.json`
beside `review_timeline.json` (never rewritten): one closed-vocabulary
`SpecialUseFact` per detected glyph.

- **Review clock only.** No mapping to gameplay video time, no GameEvents
 (never `SPECIAL_USED` / `SPECIAL_READY`), not consumed by `analysis/` or
 `coach/` until a separate integration assignment.
- The SPECIAL_USE timing lane is frozen (tag `special-use-timing-frozen`).
 `slot_state_at_use` uses the parity-locked X = 0 rule in
 `review/slot_state.py`; do not change its semantics or add a
 disconnected-slot classifier in fusion.
- Slots, never "user": Review has no local-player slot.
- `integration_valid` / `integration_blockers` (`slot_dead_at_use`,
 `slot_state_unknown`, `unknown_type`) only gate later integration; the
 observation itself always stands. No `fact_valid`.
- `remaining_estimate` is the fact's name for the dataset's unchanged
 `SpecialUseEvent.elapsed_estimate` (a remaining clock).

### Review to gameplay clock alignment

Contract: `src/splatoon3_ai_coach/alignment/ALIGNMENT_CONTRACT.md`.
`s3-coach align review-clock <review_import> <gameplay_analysis> --out <dir>`
writes `review_gameplay_alignment.json` for one operator-declared pair; neither
input is rewritten.

- Two layers. `GameplayClockCalibration` estimates `c` in
 `video_t = c - remaining` (an interval) from gameplay HUD timer edges only.
 `ReviewGameplayAlignment` composes it with the Review axis:
 `video_interval(r) = [c_lo - (r + delta) - e_axis, c_hi - (r + delta) + e_axis]`.
- `delta` is pre-registered 0; it changes only under the contract's
 replication rule, never to a numerical optimum. Deaths only test `delta`;
 they never estimate `c`.
- Three separate statuses: `pair_status` (structural only; not proof of match
 identity), `fit_status`, per-value `alignment_status`. Overtime values are
 `unknown` / `overtime_no_gameplay_timer`. No extrapolation across cuts.
- Intervals end to end; `AlignedReviewTime` has no `game_remaining`.
- No SPECIAL_USE input anywhere in alignment code, tools or gates; no local
 slot; no GameEvents. `review/`, `coach/`, `analysis/` and `vision/` do not
 import `alignment/`; ScenarioContext / CoachInput do not consume it until a
 separate bridge assignment.

### Capture packages

Contract: `src/splatoon3_ai_coach/capture/CAPTURE_CONTRACT.md`; operator
procedure: `docs/capture/OPERATOR_CHECKLIST.md`. CLI:
`s3-coach capture new|add|check|confirm|review-import`.

- `capture/` may reuse `media/`, `vision/` and `review/`; no other stage
  imports `capture/`. `analyze` and the Review timeline importer are
  unchanged and consume stored artifacts only.
- Source types are `review_code` and `uploaded_video` (public-site
  `nintendo_review_code` is the same thing; not renamed). Manual and SCLC
  acquisition satisfy the same contract.
- Artifacts are byte-identical, hash-verified, read-only. Never trim,
  re-encode or remux in capture code.
- `timeline-capture-v1` is **FROZEN** (tag `timeline-capture-v1-frozen`):
  its checks, values and pinned reader settings never change; any change in
  meaning is `timeline-capture-v2`. Evidence is C1–C4 plus the sanity set
  (`docs/capture/CHARACTERIZATION_LOG.md`); the Switch 2 observation R1 is
  research only. Detector evidence is positive-only (not observed →
  `unknown`). Continuity and overtime are observations, never gates.
  Procedure: timeline opened before GO.
- `top_down` is experimental collection only (declared playback rate, no CV,
  no contract).
- `capture review-import` is orchestration and provenance verification only
  (gate verdict `pass`, artifact identity); Review owns the import and the
  evidence pack, written under `derived/review_timeline_import/`.

### Review evidence pack

Contract: `src/splatoon3_ai_coach/review/REVIEW_EVIDENCE_PACK.md`.
`review_evidence.json` collects one import's evidence on the Review remaining
clock with per-block `validation_status`, machine-readable `limitations`, and
fact → sample → artifact → capture-package provenance.

- Inclusion never authorizes consumption: ScenarioContext / CoachInput need an
  explicit consumer allowlist (none exists yet).
- Slot-state `alive` means no X marker observed; a disconnected slot reads
  alive. Only X-marker detection is validated.
- Missing death glyphs are never evidence of no death.
- No GameEvents, no "user" slot, no interpolation.

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
   Cadence scheduling belongs to `vision/cadence_scan.py`; detectors only observe
   the in-memory frame they are given.
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

## Developer tools

Repo-root `tools/` is outside the package; `src/` must not import it (except
the CLI launchers for `vision_manifest_viewer`, `vmv_site`, `public_site`).

- `tools/calibration/`: ROI, stage-mask, and color calibration helpers
- `tools/diagnostics/`: validators and diagnostics over analysis artifacts
- `tools/reports/`: death-importance ranking reports
- `tools/studies/`: one-off research scripts; unmaintained, never imported by
  new code. Promote reusable logic into `src/` with tests instead.

See `tools/README.md` for the index and conventions for adding a tool.

## Production telemetry

Public-site operational telemetry is a separate domain under
`tools/public_site/`. Its dependency direction is one-way:

`coaching artifacts` → `telemetry readers` → `admin API/UI`

Coaching and evidence code must never import or consume telemetry. A submission
is the user's request; a job run is one execution. The first release creates one
run per submission, while the schema supports later attempts.

Telemetry JSON is built only from allowlisted Pydantic models. Never store an
email, display name, IP or IP hash, uploaded filename, path or object key,
prompt/coaching text, command line, stderr, or exception text. Expiry clears the
user link from every run while retaining operational measurements. PostgreSQL
time records when lifecycle events happened; `perf_counter()` measures stage
duration. `last_seen_at` means throttled authenticated API activity, not a page
view.
