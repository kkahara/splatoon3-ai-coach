# Final-30 Splat Zones relevance gate: design and feasibility

Status: design only. Nothing here changes production code, prompts, config,
detectors or stored analyses.

## Decisions recorded (2026-10-14)

1. **After-splat claim retired.** Three wordings (E, F, G) did not make it
   useful; its frame duplicates the level-A `death_own_splat_before` fact.
   History stays in the Experiment E/F/G folders; no further experiment.
2. **G's exact final-30 wording is frozen** (not promoted to production):
   - `process_final30_back_with_time`: "You died with {display} left and were
     back in play with about {back_with}s on the clock."
   - `process_final30_not_back_by_zero`: "You died with {display} left and
     were not back in play before the match clock reached 0:00."
   - `process_final30_no_return_observed`: "You died with {display} left; no
     return to play was observed after this death."
   - recommendation `rec_final30_respawn_time` unchanged.
3. **Score decides eligibility only.** Displayed evidence states the score and
   the clock/return facts; no causal or "close game, so..." wording.
4. **Roster (players-left) evidence stays disabled.**
5. **Assessment layout issue logged separately:** in 3 of 52 Experiment G
   outputs (both arms) the model put recommendation sentences inside
   `assessment`. Prompt/format-contract repair, not part of this experiment.

## Proposed gate (Splat Zones only)

Inputs, all observed and already in the coaching layer: `ScoreFacts` at the
`pre_death` sample (`ally_remaining`, `opponent_remaining`, penalties), the
death game-clock sample `seconds_remaining` (T), and G's final-30 case.

**Precondition A: mode.** `battle_mode_id == "splat_zones"`. Decided
2026-10-14: outside Splat Zones the final-30 claim is **dropped** in the
clock+score arm (score required, no clock-only fallback).

**Precondition B: stable counts.** Splat Zones counters never increase. Use
the `lookback`, `pre_death` and `at_anchor` samples already in
`ScenarioContext.score`: if either team's observed count increases between
any two of them, the score is treated as unavailable. Coaching-layer check
only; no detector or fusion change.

**Relevance rule (revised 2026-10-14: plain categories, no count-rate
assumption).** gap = opponent_remaining - ally_remaining (positive = the
player's team is ahead; `*_work_remaining` when both penalties are
observed):

| category | gap | eligible |
| --- | --- | --- |
| contested | abs(gap) <= 10 | yes |
| behind | -30 <= gap < -10 | yes |
| ahead | 10 < gap <= 30 | yes |
| decided | abs(gap) > 30 | no |

The category is used for eligibility only; it is not shown as a judgment and
the frozen G frames are unchanged. A first draft (`gap < seconds left`,
assuming about one count per second of zone hold) was rejected in favour of
categories that need no game-rate assumption.

## Feasibility audit (read-only)

Tool: `tools/diagnostics/final30_score_survey.py` (rebuilds contexts in
memory; nothing written).

1. **No score reached any experiment input so far.** The research corpus
   `analysis/_rescore_v2` stored `scenario_contexts.json` on 2026-09-28; score
   and zone-control context were added on 2026-09-29 (c3f6df0). The coach
   loader reads stored contexts, so `score` / `zone_control` were null for all
   13 deaths in Experiments D-G (consistent with "no score in E", but it means
   a score experiment needs rebuilt contexts).
2. **Few Splat Zones final-30 deaths.** Corpus-wide (15 unique matches) there
   are 15 final-30 deaths: 5 Turf War, 1 unknown mode, 1 Clam Blitz,
   3 Rainmaker, 5 Splat Zones. Of the 9 final-30 deaths in the 13-death
   experiment set, only 2 are Splat Zones.
3. **Score readings are unstable in 3 of the 5.** Observed fused counts in
   the 20 s before each death:

   | match | death | T | ally | opponent | gate |
   | --- | --- | --- | --- | --- | --- |
   | en-2026-09-21 | 327.5 | ~30 | 16 (21) / 1 (15) | 8 (36) / 81 (1) | unavailable (unstable) |
   | en-2026-09-21 | 343.0 | ~14 | 16 (20) / 1 (9) | 8 (28) | unavailable (unstable) |
   | brinewater triple_inkstrike | 288.5 | ~30 | 36 (40) | 32 (28) | contested (-4): eligible |
   | mahi reef_slider | 302.5 | ~13 | 36 (37) | 7 (16) / 1 (3) | behind (-29): eligible if 7 is right |
   | mahi splattercolor_screen | 316.0 | ~5 | 23 (40) | 39 (6) / 1 (9) / 5 / 52 | unavailable (unstable) |

   The flips (16/1, 81/8, 39/1) look like a digit read problem that survives
   fusion debouncing. Vision is frozen; this note only records it.
4. **Penalties are `unknown` at all 5; zone control at death is null at all
   5** (zone-control readings are absent from these manifests).

## Consequence

A controlled "clock-only vs clock+score" experiment cannot be run
meaningfully on the current corpus: the 13-death set has 2 Splat Zones
final-30 deaths (both unstable), and the whole corpus has 2 Splat Zones
final-30 deaths with usable counts (both eligible under the revised bands).

## Next step (decided 2026-10-14): broaden the corpus first

The eligibility experiment needs Splat Zones final-30 deaths with stable
counts, including some in the decided band, so both arms differ on enough
units. That means analyzing more Splat Zones recordings with the current
pipeline (decode required) before the experiment.

Score reading reliability (digit flips) is a vision issue; it stays frozen
unless reopened as its own task. Precondition B keeps flipped readings out of
the gate in the meantime.
