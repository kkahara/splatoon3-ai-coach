# Experiment I: map-check coaching (pre-registration)

**Status: frozen 2026-10-19 after rater approval (stop point 1).** Nothing
has been built or called. Gates and thresholds are not adjusted after
outputs are seen, including results close to a boundary. Machine-readable:
`design.json`.

Research only. Production code, prompts, claim catalog and config are
unchanged. Origin: coaching gap study (`../coaching_gap_study/GAP_STUDY.md`,
rater decision 2026-10-19): the map-check runner-up.

## Why

The rater's ideal coaching names missing map checks at 6 of 10 deaths. The
model sees `map.seconds_since_map_check_before_death` but the system prompt
forbids treating it as "should have checked", and the claim
`map_check_observed_before_death` is not coachable.

The production value also counts the **respawn map** (shown while dead) as a
check. At 2026-10-11 22-57-30 @308.0 the view says 21.5 s since the last
check; the last check the player made while alive was 245.5 s earlier. So
the variant needs a derived fact, not only permission to use the existing
value. (The gap-study classification filed these facts as A-view; the
accurate fact is B, derived. The study's decision is unchanged.)

## Question

Does coaching on "no map check since you came back into play" improve
usefulness at deaths where it applies, without breaking the evidence
contract?

## Derived fact (variant arm only)

Computed in the experiment tool from existing artifacts (`scenarios.json`
death episodes, `vision_manifest.json` MAP_OVERLAY events, first `in_match`
snapshot). Simple arithmetic in the coaching layer; no vision, fusion,
Scenario or ScenarioContext change.

- `back_in_play_time`: end of the previous death episode before this death;
  if none, the first `in_match` snapshot (match start).
- `seconds_back_in_play` = death time - `back_in_play_time`.
- `map_checks_since_back_in_play`: MAP_OVERLAY events starting strictly
  after `back_in_play_time + 2.0 s` and before the death. The 2 s grace
  excludes the tail of the respawn map (22-20-42 @152.0 has an overlay
  0.5 s after the episode end).
- `seconds_since_alive_map_check`: death time - start of the last such
  overlay, or null.
- **Eligible:** `map_checks_since_back_in_play == 0` and
  `seconds_back_in_play >= 10.0`.
- **Policy threshold:** 10 seconds continuously back in active play
  without a voluntary map check. The threshold is fixed for Experiment I
  and is not being estimated or validated by this experiment. It was
  chosen with knowledge of the gap sheet (the rater called "no map check"
  at 11 s, 19 s and 74.5 s back in play), so a positive result is evidence
  that surfacing this coaching unit is valuable under a fixed 10 s policy,
  not evidence that 10 s is the right threshold.
- Unknown map observability (`map_check_status` not `observed` /
  `not_observed`, or episode map observability not `sufficient`): block is
  null; no map-check coaching.

View block added to the variant's LLM view only:

```json
"map_awareness": {
  "back_in_play_source": "previous_respawn | match_start",
  "seconds_back_in_play": 74.5,
  "map_checks_since_back_in_play": 0,
  "seconds_since_alive_map_check": null,
  "respawn_grace_seconds": 2.0,
  "threshold_seconds": 10.0,
  "eligible": true
}
```

## Arms

| arm | view | system prompt |
| --- | --- | --- |
| A | production (refreshed analysis, roster off) | production |
| M | production + `map_awareness` | production + map-check section below |

Same inputs as Experiment H arm A for A (byte-identical prompts are rebuilt,
not reused; outputs are new calls). Model: cursor `gpt-5.6-luna`, one death
per call, 2 repeats.

### Map-check prompt section (arm M; frozen wording after approval)

```text
MAP CHECK SINCE BACK IN PLAY (map_awareness block)
- map_awareness.seconds_back_in_play is the time from when the player was
  last back in play (previous respawn, or match start) to this death.
- map_awareness.map_checks_since_back_in_play counts map checks the player
  opened while alive in that time. The respawn map is not counted.
- When map_awareness.eligible is true, you may coach map awareness as a
  habit: state seconds_back_in_play and that no map check was observed
  since then, and suggest a quick map check before moving up or engaging.
- Do not say the death happened because of the missing map check.
- Do not describe what the map would have shown (opponent positions, ink,
  routes); it was not observed.
- When eligible is false or the block is null, give no map-check advice.
```

The production rule "never treat map_check_before_death as should have
checked" stays in the prompt; the section applies only to `map_awareness`.
If the claim validator flags the new statements as unsupported, an
experiment-only claim (`no_map_check_since_back_in_play`, coachable, frame
"No map check was observed in the {seconds_back_in_play}s since the player
was back in play.") is added to arm M's claim basis; this is settled and
reported at stop point 2, before any call.

## Deaths

The 22 Experiment H deaths (refreshed analysis), unchanged membership.
Eligible 15, not eligible 7 (values computed from inputs only).

| match | death | back in play from | seconds back | alive checks | eligible |
| --- | --- | --- | --- | --- | --- |
| 2026-10-11 18-18-00 | 197.5 | previous respawn | 74.5 | 0 | Y |
| 2026-10-11 18-25-16 | 175.5 | previous respawn | 81.5 | 1 (39.5 s before) | N |
| 2026-10-11 18-25-16 | 85.0 | match start | 66.0 | 1 (43.0 s before) | N |
| 2026-10-11 20-33-31 | 151.0 | previous respawn | 19.0 | 0 | Y |
| 2026-10-11 20-33-31 | 180.5 | previous respawn | 21.5 | 1 (7.0 s before) | N |
| 2026-10-11 20-33-31 | 199.5 | previous respawn | 11.0 | 0 | Y |
| 2026-10-11 20-33-31 | 37.5 | match start | 24.0 | 0 | Y |
| 2026-10-11 22-20-42 | 152.0 | previous respawn | 4.0 | 0 | N |
| 2026-10-11 22-31-53 | 208.5 | previous respawn | 18.0 | 0 | Y |
| 2026-10-11 22-37-47 | 210.5 | previous respawn | 143.5 | 1 (46.5 s before) | N |
| 2026-10-11 22-37-47 | 301.5 | previous respawn | 81.5 | 0 | Y |
| 2026-10-11 22-37-47 | 58.0 | match start | 16.0 | 0 | Y |
| 2026-10-11 22-57-30 | 106.5 | previous respawn | 33.0 | 0 | Y |
| 2026-10-11 22-57-30 | 176.5 | previous respawn | 25.0 | 0 | Y |
| 2026-10-11 22-57-30 | 244.0 | previous respawn | 56.5 | 0 | Y |
| 2026-10-11 22-57-30 | 278.5 | previous respawn | 23.5 | 0 | Y |
| 2026-10-11 22-57-30 | 308.0 | previous respawn | 20.5 | 0 | Y |
| 2026-10-11 22-57-30 | 49.5 | match start | 32.0 | 1 (16.0 s before) | N |
| 2026-10-12 20-17-32 | 179.5 | previous respawn | 19.5 | 0 | Y |
| 2026-10-12 20-17-32 | 221.0 | previous respawn | 32.5 | 0 | Y |
| 2026-10-12 20-17-32 | 257.5 | previous respawn | 28.0 | 0 | Y |
| 2026-10-12 20-17-32 | 65.5 | previous respawn | 7.0 | 0 | N |

Known issue shared by both arms: 22-57-30 @308.0 carries the missed
match end (out-of-play measured past the end of the match). It is kept;
both arms see the same input.

Calls: 22 deaths x 2 arms x 2 repeats = 88.

## Measures

Automatic, per output, frozen in the tool before any output:

- **Validator:** `claim_validation.validate_assessment` hits and blocked
  outputs, each arm under its own claim basis.
- **Map mention** (`mentions_map_check`): map check / checking the map /
  looking at the map / map awareness wording.
- **Value fidelity:** every number cited in a map-check sentence equals
  `seconds_back_in_play` or `seconds_since_alive_map_check` (rounded to
  0.5 s or whole seconds).
- **Map-rule violations:** map-check advice on a non-eligible death; a
  causal claim (died because / due to not checking the map); described map
  content (opponent positions, routes, ink seen on the map).

Blind rating: both arms in one sheet (88 rows), shuffled with seed
20261020. Columns: usefulness, redundancy, anchor_shows_situation,
missing_context (1-5), supported (yes/no), **map_use** (correct /
incorrect / not_mentioned), notes. Ratings sealed in a commit before the
key is opened.

## Noise floor dependency

No third arm. The usefulness noise floor is Experiment H's measured value,
0.1364 (`coaching_units_experiment_h/analysis.json`, `q2.noise_floor`;
reported there as 0.136; mean over its 11 control deaths of |mean usefulness B - mean
usefulness C|, two byte-identical arms, 2 repeats each). Reusing it
assumes the rating procedure is unchanged from Experiment H: same single
rater, same 1-5 usefulness scale and column wording
(`coaching_units.RATING_COLUMNS`), same model (cursor `gpt-5.6-luna`), one
death per call, 2 repeats per arm, and a blind shuffled sheet rated before
the key is opened. If any of these changes before rating, the floor no
longer applies and the deviation is recorded; the threshold is not
re-derived after outputs are seen.

## Decision rule

Gates are evaluated in order: safety, then uptake, then value. **Uptake is
not success by itself:** a prompt that mechanically forces map advice
could reach 15 of 30. Value (rule 3) is decisive.

1. **Safety gate (arm M).** 0 map-rule violations; 0 value-fidelity
   errors; blocked outputs no more than arm A; validator hits per call no
   more than arm A + 0.25. Failure: production unchanged; record failures.
2. **Q1 uptake.** On eligible deaths, at least 15 of 30 M outputs mention
   the map check. Failure: the wording did not take effect; production
   unchanged; investigate the prompt section.
3. **Q2 value.** On eligible deaths, mean usefulness M - A exceeds 0.1364
   (the Experiment H noise floor: same rater, model, deaths and scale);
   M's supported rate is not lower than A's on all 22 deaths; and over all
   22 deaths mean usefulness M - A is at least -0.1364 (no harm).
4. **Outcomes.**
   - Passes 1, 2 and 3: recommend a production change (separate
     decision: derived field in the coaching layer, prompt section, claim).
   - Passes 1 and 2, fails 3: handled correctly but no demonstrated value;
     production unchanged.
   - Fails 1 or 2: production unchanged; investigate.
   Directional only (one rater, small n).
5. **Covariates (descriptive).** `seconds_back_in_play` band (10-30 s,
   over 30 s), production selection, `map_use` per arm.

## Stop points

1. This design. Review and approval (then frozen; `design.json`). Done.
2. Tool, tests, built inputs and dry run, sealed. Go-ahead before any call.
3. Outputs, automatic summary, blind sheet.
4. Sealed ratings, then key and `analyze` report.
