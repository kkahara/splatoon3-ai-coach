# Coaching evidence contract

Freeze line: Scenario + ScenarioContext architecture is **frozen**. ScenarioContext
is sufficient for a first **timeline / evidence** coaching layer. Do not expand
the scenario schema to encode judgments or speculative combat narratives.

Do not modify `scenarios.py`, `scenario_context.py`, detectors, fusion, or
Scenario / ScenarioContext schemas unless a concrete coaching requirement shows
an existing evidence field is insufficient.

## Layer separation

```text
GameEvent  →  Scenario  →  ScenarioContext  →  Coaching interpretation
```

| Layer | Role |
|-------|------|
| `GameEvent` | Observed / fused fact |
| `Scenario` | Structural **ownership** (`event_ids`) — what belongs here |
| `ScenarioContext` | Observed + deterministic + relational **evidence** |
| Coaching | **Interpretation** of that evidence |

Do **not** encode coaching judgments into `ScenarioContext`.

## Dual clocks

| Clock | Layer | Role |
|-------|-------|------|
| Video time | `GameEvent` / Scenario | Canonical identity, ordering, grouping, relations |
| Game clock | `coach.game_clock.GameClock` | Observed match countdown at a video time |

`GameClock` is built from raw usable timer detections on `VisionFrameResult`
(`TimerReading` + `DetectorResult.confidence`), not from held/smoothed
`GameStateSnapshot.match_time_remaining`. Missing lookups stay missing.
Do not invent remaining time from elapsed video time.

## Player-count samples (roster state)

`player_count_samples` are secondary coaching evidence built from fused
`GameStateSnapshot.ally_alive_count` / `opponent_alive_count` (persisted on
`vision_manifest.state_snapshots`). Detector evidence is HUD death-X markers
in known player-slot ROIs (`PlayerCountReading`); coaching vocabulary is the
alive roster counts only.

**`ally_alive_count` and `opponent_alive_count` are supported coaching evidence
when `player_count_samples` are present. These fields describe roster state,
not the number of players participating in a particular engagement.**

Never infer alive counts from splat counts, death events, or scenario
membership. Generic “enemy count” / fight participation remains unsupported
without engagement-specific evidence.

Do **not** put player counts on `ScenarioContext` or emit player GameEvents.

## CoachInput unit

`CoachInput` is **one coaching unit**, not a whole-match dump:

```text
primary Scenario + ScenarioContext
  + related Scenarios via ScenarioContext.relations only
  + GameClock samples at labeled video times
  + PlayerCount samples at the same labeled times
  + EvidenceLimit non-claims
```

`CoachInput` describes **evidence**. It does not define good/bad play, advice,
or fight quality. Assessments and recommendations are a later LLM output layer.

Prototype: `s3-coach coach-prototype` sends identical CoachInput JSON + system
prompt to one or more Ollama models and writes `CoachingAssessment` JSON.
Empty `recommendations` is a valid success. Claim flags are separate annotation
files and must not rewrite assessments.

See `splatoon3_ai_coach.coach.evidence_contract` and
`splatoon3_ai_coach.coach.coach_input` for the enforceable API.

## Scenario membership / ownership

Event ownership must be unambiguous.

- `DEATH_EPISODE` owns its DEATH, respawn / active-again lifecycle events, and
  map overlays during the episode.
- `ENGAGEMENT` owns **SPLAT events only**.
- A DEATH associated with an ENGAGEMENT is **not** an ENGAGEMENT member.

### Compatibility vs relation vs ownership

| Field | Layer | Meaning |
|-------|-------|---------|
| `event_ids` | Scenario | Owned members |
| `following_death_id` | Scenario `context` | Compat GameEvent id for a following death; **not** ownership |
| `leads_to_death_episode_id` | ScenarioContext relations | Temporal association to a death episode |
| `preceded_by_engagement_id` | ScenarioContext relations | Reverse temporal association |
| `follows_death_episode_id` / `next_engagement_id` | ScenarioContext relations | Post-return temporal links |

Association is **not** causation. Never claim a splat caused a death from these fields.

Scenario outcome values (`fragged`, `died`) are closed linkage vocabulary.
They are **not** fight-quality evidence (not won/lost/good/bad fight).

## Evidence classes (permissible as facts)

1. **Observed** — timestamps, `respawn_reason`, splat times, map event ids
2. **Deterministic** — durations, counts, `complete`, map presence flags
3. **Relational** — death gaps, `leads_to_*` / `follows_*` / `next_engagement_id`,
   `splat_death_gap`, `trade_candidate` (window flag only)

## Map check semantics

| Field | Means | Does **not** mean |
|-------|-------|-------------------|
| `map_check_before_death` | At least one map overlay occurred **before** the death (any prior time) | A map overlay in a short pre-death window; “recent check”; good/bad map use |
| `seconds_since_map_check_before_death` | Unbounded gap from last prior overlay to death | A configured short lookback. Values like **44.5s** are valid |

Do not change the implementation to window this field unless a concrete coaching
requirement needs a bounded pre-death map-check fact.

## Critical wording

| Field | Must not say | May say |
|-------|--------------|---------|
| `leads_to_death_episode_id` | "splat caused death" | associated under configured temporal rule |
| `following_death_id` | ownership or causation | compat reference to a following DEATH event |
| `trade_candidate` | "you traded" | gap within configured window; not proof of a trade |
| `ENGAGEMENT` / outcome | "you won/lost a fight" | splat observation cluster; `fragged`/`died` = linkage only |
| `map_check_before_death` | "should have checked"; "recent map check" | any prior overlay before death (unbounded) |

## Permitted vs prohibited

**Permitted:** lifecycle timings; respawn path; map overlay presence/counts;
death spacing; ENGAGEMENT splat observations; temporal associations;
cross-episode comparisons of the same fields; when `player_count_samples`
are present, `ally_alive_count` / `opponent_alive_count` roster state at a
time (including roster difference wording — not fight participation).

**Prohibited as facts:** fight win/lose/quality; overextension; outnumbered
**in the fight**; should-have advice; gear attributions; rush/hesitate/camp
intent; causal "splat caused death"; "same fight" without richer evidence;
inferring alive counts from splat/death/scenario membership.

**Requires new evidence:** fight boundaries, damage, weapons, positions,
generic enemy_count / fight participants, loadout, objective/score/special,
map content. (Match clock uses GameClock samples when present.)

**Requires interpretation (not a ScenarioContext field):** good/bad map or
engagement, avoidability, what the player should have done.

## Convenience fields

Do not add speculative fields such as `active_again_to_next_engagement`,
`fight_quality`, `overextended`, `won_fight`, or `lost_fight`. Derive simple
arithmetic in the coaching layer when needed.
