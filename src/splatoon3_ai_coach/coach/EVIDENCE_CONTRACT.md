# Coaching evidence contract

> **The LLM may choose wording, emphasis, and ordering among authorized claims;
> it may not create a new factual, causal, judgmental, or counterfactual claim.**

Scenario **ownership** (`scenarios.py`, `Scenario.event_ids`, types, intervals)
remains frozen. Do not change grouping or encode judgments / speculative combat
narratives into scenarios.

`ScenarioContext` may include **sparse factual secondary evidence** required for
coaching units:

- map ink observations (`map.ink` from `map_observations.json`)
- roster trajectory from fused `state_snapshots` (`players`)
- special gauge readings + presentation-only ready onset markers (`special`)
- Splat Zones remaining counts + penalties from fused `state_snapshots`
  (`score`, observed samples only)
- Splat Zones control state and confirmed team-level transitions from fused
  `state_snapshots` (`zone_control`, with explicit observed/held quality)

Do **not** encode coaching judgments, fight quality, “should have used special”,
interpolated continuous state, or invented causal relationships. Do **not** emit
player GameEvents or promote ready onsets to `SPECIAL_READY` / `SPECIAL_USED`.

Leave unavailable evidence unavailable.

## Claim levels and `claim_basis`

Flow: raw evidence → deterministic eligibility → `claim_basis` → LLM wording
→ deterministic validation. The model no longer decides whether something is
a coaching claim; it words claims that are already authorized
(`coach/claim_basis.py`).

| Level | Meaning | How it may be worded |
|-------|---------|----------------------|
| A | Observed fact | Directly, no hedge required |
| B | Supported interpretation (principle / process tied to an A fact) | Directly, naming its basis; never a cause of this death |
| C | Plausible hypothesis | Only via a whitelisted `ClaimDefinition` with `level=C` (none today), always qualified |
| D | Undetermined | Never stated — not hedged, not negated in the assessment |

- `ClaimDefinition` carries `level`, an eligibility predicate over CoachInput,
  `evidence_paths`, `contradicted_by`, `frame`, `coachable`, `public_ok`,
  `authorizes` (judgment subcategories it licenses) and `rec_template_ids`.
  The view's `claim_basis` entries also carry `rec_frames` (each template's
  wording) so the model can phrase the only recommendations R10 accepts.
- `coachable` separates "the fact exists" from "the fact is worth offering as
  coaching" (`timer_sample_exists` is A but not coachable).
- Hedging never creates C: "may have contributed to the death" is an invented
  causal claim, not a hypothesis.
- `CoachLlmView.undetermined` lists the closed D topics (cause of death,
  special usage, teammate state, opponent location, positioning, fight
  outcome, and roster while `coach.roster_evidence` is false), each with
  eligible A/B `alternative_claim_ids`.
- Evidence conservatism must not become evidence blindness: when a coachable
  A/B claim is eligible, "nothing notable" is a validation failure (R11).

### Roster capability boundary

`coach.roster_evidence` (default `false`) is one switch. While false there are
no roster ranking factors, no `roster` block or roster-derived `death.*`
fields in the view, no public roster lines, no roster prompt guidance, no
roster claim in `claim_basis` (`roster` is undetermined), and the validator
rejects roster assertions (R2). The roster code stays in place.

### Validation rules

| Rule | Checks | Effect |
|------|--------|--------|
| R1 | Causation / counterfactual causation tied to a death (hedged still counts) | Block |
| R2 | Roster claims while the capability is off | Block |
| R3 | Teammate state / opponent location | Block |
| R4 | Special usage (used, wasted, should have used) | Block |
| R6 | Judgment subcategories (positioning, pursuit, aggression, counterfactual action, outcome judgment) unless a `claim_basis` entry authorizes them | Block |
| R5 | State words match the view (special state, map status, clock windows; a map check is never "recent") | Badge |
| R7 | `claims[]` / `selected_claim_ids` ⊆ `claim_basis`, no level upgrade, cited values equal view values | Badge |
| R8 | No internal identifiers in player text | Badge |
| R9 | "Unknown / not available" only when the view lacks the value | Badge |
| R10 | Recommendations match a template grounded by an eligible claim | Badge |
| R11 | No "nothing notable" while a coachable A/B claim is eligible | Badge |
| R12 | No "video seconds" in player text (plain or game-clock seconds only) | Badge |
| R13 | Decimal-safe sentence splitting; negation scoped to its clause | Mechanics |
| R14 | `assessment` / `claims[]` state evidence only; a sentence repeating a recommendation or giving advice belongs in `recommendations` | Badge |

Only the `limitations` field and clause-scoped negations exempt R1–R6 hits;
for the state families (R2, R3) a sentence declaring the state unknown is an
abstention and is exempt too. A blocking hit withholds the prose publicly;
nothing is regex-rewritten.

Japanese display: a translation (fresh or cached) that introduces a claim
family the validated English never mentioned (`claim_rules.ja_claim_hits` /
`translation_introduced_hits`) is discarded and the English is shown.
Template translations are trusted.

### Saved-output corpus

`tests/fixtures/claim_corpus/` holds frozen CoachInputs with the model output
they produced; `tests/test_claim_corpus.py` rebuilds each view with current
code and checks deliberate rule labels and coachable claims per entry
(`tools/diagnostics/claim_corpus.py report` prints per-category metrics).
Entries from before `claim_basis` are `prompt_contract: legacy` and skip R7.
Add outputs that expose a new failure mode with `claim_corpus.py freeze`;
change an expected label only with a `note` saying why.

Live model evaluation is manual: run `s3-coach coach-prototype --call-llm`
on the analyses behind the corpus, freeze the new outputs, and compare
`claim_corpus.py report` metrics before and after a prompt or model change.
CI never calls a provider.

`death_quick_redeath` was `death_redeath_le_10s` (the gap is configurable,
20 s by default). The old ID is still read from configs, persisted units and
saved outputs via `config.models.LEGACY_DEATH_FACTOR_IDS`.

### Public coaching states

| State | Public page shows |
|-------|-------------------|
| Sent, valid, with recommendation | Assessment and recommendations |
| Sent, valid, no recommendation | Assessment plus "No recommendation supported by the available evidence." |
| Sent, blocked (R1–R4, R6) | Deterministic statements only |
| Not sent to the LLM | "Not reviewed by the coach." |
| Stale artifact | The stale-result notice (takes precedence) |

## Layer separation

```text
GameEvent  →  Scenario  →  ScenarioContext  →  Coaching interpretation
```

| Layer | Role |
|-------|------|
| `GameEvent` | Observed / fused fact |
| `Scenario` | Structural **ownership** (`event_ids`) — what belongs here |
| `ScenarioContext` | Observed + deterministic + relational **evidence** (incl. sparse secondary nests) |
| Coaching | **Interpretation** of that evidence |

Do **not** encode coaching judgments into `ScenarioContext`.

MAP_OVERLAY facts (`map.map_check_*`) stay distinct from map **ink**
(`map.ink`). Ink never sets overlay map-check fields.

### Sparse secondary nests (Stage 1) — association semantics

| Field | Association | Meaning |
|-------|-------------|---------|
| `map.ink.nearest_before_anchor` | at/before anchor only | Latest ink sample with `video_time ≤ anchor` within max gap |
| `special.nearest_before_anchor` | at/before anchor only | Latest special reading with `video_time ≤ anchor` within max gap |
| `special.state_at_anchor` / `SpecialReading.state` | derived from one reading | `ready` and `not_ready` require a visible gauge; a hidden gauge or no reading is `not_observed`, never `not_ready` |
| `special.last_visible_before_anchor` | at/before anchor, in window | Latest visible reading; names what was last observed when the nearest reading is hidden. Not a substitute state at the anchor |
| `SpecialReading.coaching_valid` / `coaching_invalid_reason` | per reading, full stream | `lifecycle`: dead after the first dead frame, countdown, respawned or awaiting control. `uncorroborated_prompt_ready`: ready from the prompt alone with no full ring yet in the current life (see Decisions). Invalid readings stay in `observations` but never feed state, onsets or last ready |
| `players.at_anchor` / `players.at_death` | nearest within max gap | Closest fused roster sample by `\|Δt\|` (may be slightly after the labeled time) |
| `score.at_anchor` / `score.pre_death` / `score.lookback` | at/before target only | Latest fused snapshot in `[target − max gap, target]` that observed any score field (prefers both main counters observed); `pre_death` target = `death_time − score_pre_death_offset_seconds`; `lookback` target = pre-death target − `score_lookback_seconds` |

Roster nearest-in-gap is intentional: alive counts are held state sampled on a
cadence, not one-shot UI flashes. Ink/special “nearest before” stay
pre-anchor-only so coaching does not pull a sample that appears only after the
scenario anchor. Do not “unify” these without an explicit contract change.

Canonical sparse roster compression is
`analysis.player_count_series.compress_player_count_observations` (shared by
ScenarioContext and CoachInput). Ready onsets use
`analysis.special_ready_markers.derive_special_ready_onsets` (shared with VMV);
derive from the full ordered stream, then restrict markers to the evidence
window.

## Dual clocks

| Clock | Layer | Role |
|-------|-------|------|
| Video time | `GameEvent` / Scenario | Canonical identity, ordering, grouping, relations |
| Game clock | `coach.game_clock.GameClock` | Observed match countdown at a video time |

`GameClock` is built from raw usable timer detections on `VisionFrameResult`
(`TimerReading` + `DetectorResult.confidence`), not from held/smoothed
`GameStateSnapshot.match_time_remaining`. Missing lookups stay missing.
Do not invent remaining time from elapsed video time.

## Match end boundary

**No coaching evidence may originate after `MATCH_END`.** The `MATCH_END`
GameEvent (`vision/match_end.py`) is the first trustworthy game clock 0:00
(`reason=clock_zero`). Overtime and knockouts have no trustworthy 0:00 and get
no `MATCH_END` by default (the opt-in `reason=hud_loss` fallback would cut
overtime play). Scenario intervals are clamped to it, and
ScenarioContext / CoachInput samples at or after it are dropped
(`analysis/match_bounds.py`). Without a `MATCH_END` the domain is the whole
video.

A death still open at the match end is a terminal end, not a respawn:
`death_episode.end_reason="match_end"`, `match_end_time`,
`death_to_match_end`. The permitted statement is "The match ended X video
seconds after this death, before a respawn was observed." `CoachLlmView`
adds `ended_by_match_end: true` only in that case.

## Player-count samples (roster state)

`player_count_samples` are secondary coaching evidence built from fused
`GameStateSnapshot.ally_alive_count` / `opponent_alive_count` (persisted on
`vision_manifest.state_snapshots`). Detector evidence is HUD **roster X markers**
in known player-slot ROIs (`PlayerCountReading`); coaching vocabulary is the
alive roster counts only. Roster X is not local-player `DEATH`.

**`ally_alive_count` and `opponent_alive_count` are supported coaching evidence
when `player_count_samples` or `player_count_window` are present. These fields
describe roster state, not the number of players participating in a particular
engagement.**

`player_count_window` is a compact event-relative timeline around the unit
anchor (death time / first splat). `player_count_context` may add derived
fields such as `numbers_state`, `state_present_by`, and
`duration_since_present_by`.

Prefer this phrasing:

> Disadvantage was observed by 43.0s, 5.0s before the anchor.

Do **not** phrase it as:

> The player was disadvantaged for 5.0 seconds.

`duration_since_present_by` is only `anchor - state_present_by` for a
contiguous same-`numbers_state` observation run (holes larger than the
configured max lookup gap end the run). It is a sampled presence bound, not
proof of continuous disadvantaged time or an exact transition instant. A fused
roster change is not an `ALLY_DEATH` / trade / fight-quality fact.

Never infer alive counts from splat counts, death events, or scenario
membership. Generic “enemy count” / fight participation remains unsupported
without engagement-specific evidence.

Sparse roster trajectory may also appear on `ScenarioContext.players` as
factual secondary evidence. Do **not** emit player GameEvents. Player count
describes tactical context; it does not define scenario ownership.

**Stage 1:** `CoachInput.player_count_*` APIs remain unchanged (still derived
via `PlayerCountClock` in the coaching layer).

## Splat Zones score samples (`ScenarioContext.score`)

Sparse facts from fused `GameStateSnapshot` score/penalty fields
(`vision/score_fusion.py`, Stage 3.2). Fusion is mode-gated: any match whose
battle mode is not `splat_zones` (or is unresolved) has no score samples.

- `ally_remaining` / `opponent_remaining`: the counts each team still needs
  (lower is closer to winning). A value is present only when that side's
  quality is `observed`; `held`, `rejected_implausible` and `unknown` stay
  `None` with the quality recorded.
- `ally_penalty` / `opponent_penalty`: the `+N` penalty under a team's count.
  Present only when `observed`. `not_shown` means the penalty region was read
  and no `+N` was visible — it is **not** zero in evidence; only the coaching
  layer may treat it as zero, under a stated `EvidenceLimit`. Penalty never
  counts toward the result.
- Samples: `at_anchor` for every implemented unit; `pre_death` and
  `lookback` (`score_lookback_seconds` before the pre-death target) for
  `DEATH_EPISODE` only. Two samples let coaching state that a counter changed
  between them — nothing about why.

Permitted: “ally/opponent remaining count and penalty at time T (Splat Zones
only)”. Coaching-layer arithmetic (remaining difference, counts before
progress, which counter changed shortly before the death) is derived in the
coaching layer, never stored here: `coach/score_facts.py` →
`CoachInput.score_facts` → `CoachLlmView.score`, with `EvidenceLimit`s
`score_not_splat_zones`, `score_count_unavailable`, `score_not_zone_holder`
and `penalty_not_shown_as_zero`.

Prohibited: which player caused a control change, “pushing / losing the
objective” as player attribution, and any causal link between control/count
and the death. Team-level control at a sampled time and confirmed team-level
transitions are permitted. Do not emit score GameEvents.

## Splat Zones control samples (`ScenarioContext.zone_control`)

Control samples are sparse factual state from the fused scoreboard highlight.
The state vocabulary is `neutral`, `ally_control`, `opponent_control`, and
`unknown`; quality distinguishes `observed`, `held`, and `unknown`.

- `at_anchor`, `pre_death`, and `lookback` use the same at/before target
  convention as score samples.
- `trajectory` and `transitions` preserve only persisted state evidence.
- Held state is not a fresh observation and must not be used as proof that a
  transition occurred at that timestamp.
- Unknown gaps are not bridged into a transition.

Permitted: “At time T, the zone was controlled by the ally/opponent team,” and
“The zone became neutral and was subsequently regained by the opponent/ally
team,” when the corresponding observed samples and transition evidence exist.

Prohibited: identifying which player caused a capture/loss, claiming that a
death caused a control change, or calling the factual sequence a missed/seized
opportunity inside ScenarioContext. Those are coaching interpretations.

## CoachInput unit

`CoachInput` is **one coaching unit**, not a whole-match dump:

```text
primary Scenario + ScenarioContext
  + related Scenarios via ScenarioContext.relations only
  + GameClock samples at labeled video times
  + PlayerCount samples at the same labeled times
  + PlayerCount window + derived present_by context around the unit anchor
  + EvidenceLimit non-claims
```

`CoachInput` describes **evidence**. It does not define good/bad play, advice,
or fight quality. Assessments and recommendations are a later LLM output layer.

Prototype: `s3-coach coach-inputs` then `s3-coach coach-prototype` writes the
system prompt and each selected unit's user prompt. `s3-coach coach-prototype
--call-llm` sends those same bytes to one or more Ollama models and writes
`CoachingAssessment` JSON.
Empty `recommendations` is a valid success. Claim flags are separate annotation
files and must not rewrite assessments.

See `splatoon3_ai_coach.coach.evidence_contract` and
`splatoon3_ai_coach.coach.coach_input` for the enforceable API.

## CoachInput vs CoachLlmView

| Object | Role |
|--------|------|
| `CoachInput` | Internal evidence unit (full ScenarioContext + related + samples) |
| `CoachLlmView` | Deterministic **projection** for `complete()` — omit/reshape only |

```text
CoachInput (+ CoachingUnitResult importance)
        ↓
build_coach_llm_view()   # project; never invent facts
        ↓
CoachLlmView → user prompt → LLM
```

Rules:

- Every value in `CoachLlmView` must be copyable from existing `CoachInput` /
  ScenarioContext fields (or from `CoachingUnitResult` already derived from
  that CoachInput).
- Compression = **omit or reshape**, never enrich. Absent stays absent.
- Each compact fact should carry a `source_path` **audit reference** into
  CoachInput / ScenarioContext vocabulary (stable dotted labels for
  `evidence_used`). It is not required to be an executable JSONPath.
- Locked statement / interpretation / recommendation triad text stays on
  `CoachingUnitResult` / VMV — **not** in `CoachLlmView` (pre-interpreted;
  the model words from evidence + factor ids).
- Do not send raw `CoachInput.model_dump()` as the production LLM payload.
- `build_coach_llm_view` dispatches on **`candidate_type` only** (authoritative;
  no scenario_type fallback).

## Coaching candidates (coaching layer)

Selection policy ≠ definition of what is coachable.

```text
Game evidence / ScenarioContext
        ↓
Candidate generation   (domain-specific; today: DEATH_EPISODE only)
        ↓
CoachingCandidate      (common shape: id, type, factors, importance_score)
        ↓
Importance scoring     (per candidate_type factor model)
        ↓
Top-N selection        (type-agnostic; coach.max_llm_units)
        ↓
CoachLlmView builder   (compact projection of selected CoachInput)
        ↓
LLM
```

- **Every generated candidate is eligible** for ranking. Importance factors are
  **not** eligibility gates.
- Death is the **first** candidate domain, not the boundary of coaching.
- Death-scoped factors (`death_while_outnumbered`, `death_quick_redeath`, …)
  contribute to `importance_score` only for death candidates.
- Top-N ranking ignores `candidate_type` (future domains share the same ranker).
- `importance_score` means coaching **attention** worthiness — not “how bad”
  the death was, and not “only death is coachable.”
- LLM receives **CoachLlmView** (projected evidence + active factors), not the
  full serialized CoachInput dump.
- Supporting evidence is separate from factor annotations (VMV “why” vs attention
  context).

See `coach.coaching_candidates`, `coach.death_importance`, `coach.llm_view`,
`coach.claim_catalog`.

Locked factor annotations (VMV only): `death_while_outnumbered` /
`death_while_ahead_in_numbers` (statement only, quoting both pre-death counts;
active only at a gap of at least `death_factor_thresholds.roster_min_gap`);
`death_special_ready` (statement only — never “should have used special”).

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
| `map_check_before_death` | At least one map overlay occurred **before** the death (any prior time). `null` = not assertable (see `map_check_status` / reason) | A map overlay in a short pre-death window; “recent check”; good/bad map use; inventing “did not check” when `null` |
| `seconds_since_map_check_before_death` | Unbounded gap from last prior overlay to death | A configured short lookback. Values like **44.5s** are valid |

Do not change the implementation to window this field unless a concrete coaching
requirement needs a bounded pre-death map-check fact.

### Map coverage gate (`respawn_map_coverage`)

`map_check_status` is authoritative: `observed`, `not_observed`, or `unknown`
(with `map_check_unknown_reason`). Absence becomes `not_observed` (and
`map_check_before_death = false`) only when **all three** layers hold:

1. **Source** — `map_overlay_observability` is `observable`.
2. **Match** — overlays were detected during at least
   `scenarios.map_coverage.min_respawn_overlay_fraction` of completed respawns
   (death → active again). The live respawn screen shows the map, so a working
   detector sees it there; zero hits means the detector is blind on this video.
3. **Episode** — usable map_overlay readings cover the pre-death segment
   (previous active again → death) at ≥ `min_pre_death_frame_fraction` with no
   gap > `max_pre_death_gap_seconds`, and a complete episode has an overlay on
   its own respawn screen.

Reason codes: `source_unobservable`, `match_coverage_insufficient`,
`map_readings_unavailable`, `episode_pre_death_coverage_gap`,
`episode_respawn_map_missing`. A prior overlay is `observed` regardless of
coverage. `death_map_overlay_before_false` activates only on `not_observed`;
artifacts written before this gate (bare `false`, no status) never activate it.
`map_checked_while_dead = false` follows the same rule.

### Playback clock

ScenarioContext durations are video seconds. When the replay plays faster or
slower than the match (`playback_clock.status = rate_mismatch`), coaching
durations carry `{video_seconds, value, clock, method}`; `value` is in game
seconds (from timer samples, or rate-converted with
`game_clock_endpoint_missing`). With `insufficient_clock_evidence`, `value` is
null and video seconds must not be described as time out of play.

### Artifact versions and run provenance

`coach_inputs/coaching_index.json` records `evidence_version`
(`COACH_EVIDENCE_VERSION`) and the manifest's `analysis_id`. A missing or older
version, or an `analysis_id` that differs from the current manifest, makes the
saved coaching **stale** (`artifact_staleness`). Stale artifacts are marked and
warned about everywhere they are read (public result notice, analysis
platform, viewer, reports); `coach-prototype` refuses them unless
`--allow-stale` is passed. Nothing is rebuilt automatically.

Each LLM run records `provenance`: requested vs sent temperature/seed with a
support status, sha256 of the exact prompt strings (or Cursor's joined
message) and of the written `llm_view.json`, the importance config hash and
`executed_at`. Replay is `best_effort`; what a remote server actually received
cannot be proven.

## Decisions and residual risks

| Decision | Rule | Why |
|----------|------|-----|
| Trace C roster 4v3 is kept | The roster plausibility rule (`assess_roster_plausibility`) rates the counts plausible, so they stay reliable. No per-trace special case | A count the rule accepts is not overridden by hand. A future refinement may split roster state into observed / plausibly reconstructed / unreliable |
| Special ready needs ring corroboration | See below | Prompt-only ready readings after a respawn were detector misreads, not stale frames |
| Replay detection deferred | No replay classifier; the map coverage gate (`respawn_map_coverage`) is the current protection against replay-only map evidence | A classifier needs labelled replay footage first |

### Special ready corroboration

The detector reports `ready` when the ring is full (`fill ≥
vision.special_gauge.ready_fill_threshold` with continuity) **or** when the
ready prompt scores high, because real ready frames pulse. The prompt alone
misfires on partly filled dials. Coaching therefore reads it as:

`fill ≥ ready_fill_threshold` → ring corroborated for this life → later
prompt-only ready readings in the same life may be coaching-valid.

not "prompt detected → special definitely ready". The threshold is the
detector's own; there is no second threshold.

Per-life state (`contextualize_special_readings`), with the invariant
**`ring_seen` is true only if a qualifying full-ring observation has occurred
since the start of the player's current life**:

- Lives start at the first snapshot and at each return to `alive` after
  dead / countdown / respawned / awaiting control (from snapshots, so a death
  with no readings still resets). `unknown` neither starts nor ends a life.
- Entering a new life is the only reset.
- Lifecycle-invalid readings never set `ring_seen`, even at full fill.
- Without snapshots nothing is contextualized (fields stay null).

Evidence: in Trace B (9d47, death 72.5 s) the readings at 82.0 / 82.5 s report
ready from prompt 0.83 / 0.65 at fill 0.38, and the frames show a dial about a
third lit. Across the full 9d47 and enprev matches, 85/180 and 36/116 ready
readings become `uncorroborated_prompt_ready`; sampled frames (fill 0.05 to
0.86) all show a dial that is not full. A time window after `ACTIVE_AGAIN`
was rejected: misreads also occur mid-life, far from any respawn.

### Residual risks

- Stale coaching pages keep their historical text under the stale notice;
  they are not rewritten.
- NVIDIA `seed` support is unverified; provenance records what was
  requested and sent, not whether the server honoured it.
- A roster series that starts mid-match (no opening 4v4) is handled
  conservatively: early dips may be marked unreliable.
- A genuine ready that only ever shows the prompt (ring never read at
  threshold, e.g. a gauge partly occluded all life) is dropped as
  uncorroborated. Unknown stays unknown rather than becoming ready.

## Critical wording

| Field | Must not say | May say |
|-------|--------------|---------|
| `leads_to_death_episode_id` | "splat caused death" | associated under configured temporal rule |
| `following_death_id` | ownership or causation | compat reference to a following DEATH event |
| `trade_candidate` | "you traded" | gap within configured window; not proof of a trade |
| `ENGAGEMENT` / outcome | "you won/lost a fight" | splat observation cluster; `fragged`/`died` = linkage only |
| `map_check_before_death` | "should have checked"; "recent map check"; treating `null` as “did not check” | any prior overlay before death (unbounded); `null` = unavailable for this source |

## Permitted vs prohibited

**Permitted:** lifecycle timings; respawn path; map overlay presence/counts;
death spacing; ENGAGEMENT splat observations; temporal associations;
cross-episode comparisons of the same fields; when `player_count_samples`
are present, `ally_alive_count` / `opponent_alive_count` roster state at a
time (including roster difference wording — not fight participation); in
Splat Zones, ally/opponent remaining count and penalty at a time from
`score` samples; team-level control state and transitions from
`zone_control` samples.

**Prohibited as facts:** fight win/lose/quality; overextension; outnumbered
**in the fight**; should-have advice, including "should have pushed" or
"should have retreated" because of roster counts (a roster count is a
circumstance, never a verdict); gear attributions; rush/hesitate/camp
intent; causal "splat caused death"; "same fight" without richer evidence;
inferring alive counts from splat/death/scenario membership; player causality
for zone control, pushing or losing the objective as player attribution, or
the count/control state causing the death.

**Requires new evidence:** fight boundaries, damage, weapons, positions,
generic enemy_count / fight participants, loadout, objective state beyond
Splat Zones counts, special state, map content. (Match clock uses GameClock samples when present.)

**Requires interpretation (not a ScenarioContext field):** good/bad map or
engagement, avoidability, what the player should have done.

## Convenience fields

Do not add speculative fields such as `active_again_to_next_engagement`,
`fight_quality`, `overextended`, `won_fight`, or `lost_fight`. Derive simple
arithmetic in the coaching layer when needed.

## DEATH_EPISODE enrichment levels

Stop enriching `DEATH_EPISODE` beyond Level 2 unless validation finds a genuine
missing factual dependency. Special usage stays Level 3 / unresolved.

### Level 1 — already strong (do not duplicate)

Lifecycle times/durations, awaiting-control span, `players.at_death` /
trajectory, map-check overlay fields, `timeline` neighbor deaths, and
relations to engagements. Leave these as-is.

### Level 2 — factual convenience on `death_episode`

| Field | Means | Absence |
|-------|-------|---------|
| `is_first_death` | `timeline.time_since_previous_death is None` | always bool when nest present |
| `time_since_previous_splat` | gap from last SPLAT strictly before death | `None` if no prior splat |
| `match_phase_at_death` | nearest snapshot `match_phase` within `context_max_gap_seconds` | `None` if none in gap |
| `numbers_state_at_death` | pure AvB from `players.at_death` (`even` / `advantage` / `disadvantage`) | `None` if `at_death` missing |
| `roster_changed_before_death` | compressed trajectory AvB changed in `(death − lookback, death]` | `None` if no players evidence; else bool |
| `seconds_since_roster_change` | `death_time − last change` in that lookback | `None` if no change |
| `preceded_by_trade_candidate` | mirrors preceding engagement `combat.trade_candidate` via relation | `False` if no preceding engagement; never invents a trade |

Do **not** add flat `ally_alive_at_death` / `opponent_alive_at_death` mirrors —
use `players.at_death`. Do **not** mirror `time_since_previous_death` onto
`death_episode` — it stays on `timeline`.

`numbers_state_*` is roster arithmetic only, not fight quality. Roster-change
fields record *what* changed, not *why* the death happened.

### Level 3 — Special frozen

Sparse `special.*` observations / ready onsets remain factual secondary
evidence. Do **not** encode special fire, use, influence, or
“should have used special” in ScenarioContext. Do not emit `SPECIAL_READY` /
`SPECIAL_USED` GameEvents from this layer.
