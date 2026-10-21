# Experiment J: incremental value of map-awareness coaching

**Status: FREEZE CANDIDATE, revision 4. Not frozen.** Nothing is built,
analyzed or called. `design.json` is created only after this text is
approved at stop point 1. After the freeze, gates and thresholds are not
adjusted after outputs are seen, including for results close to a
boundary.

Research only. Production code, prompts, claim catalog, validator and config
stay unchanged. Experiment H/I artifacts, ratings, flags, the Experiment I
postmortem and every frozen determination are not touched.

## Question

On fresh deaths independently judged map-relevant before any model output
exists, does the map-specific portion of M add useful, actionable coaching?

## What J can and cannot claim

J does **not** claim causal isolation of "map content". Neither primary
comparison achieves that alone:

- **M vs A** may partly reward having useful advice at all.
- **Full M vs ablated M** often compares map advice with no advice (see the
  stored-output check below).

The two comparisons have different weaknesses. J's efficacy decision
therefore requires them to **converge** on deaths labelled map-relevant from
footage before any output existed. The relevance = no comparison is
reported as supporting construct-validity evidence. It never decides J.

## History (revisions 1–3)

- Experiment I (frozen; `../coaching_units_experiment_i/`) confounded three
  things:
  - one more grounded recommendation;
  - map content;
  - mechanical eligibility treated as relevance.
- Its safety failure was citation form (`primary_context.map_awareness.*`),
  not grounding.
- Revision 2 used an active control C. A read-only count over 125 locally
  stored deaths found a clean, death-specific C point at only 6% of deaths.
  The full menu reached 25%, but mostly through first/last 30 s clock facts,
  which would have distorted the population.
- New evidence is not invented to rescue a three-arm design. C is a small
  diagnostic; the primary test is M vs A plus a within-output map ablation.

## Arms

| arm | view | system prompt | where |
| --- | --- | --- | --- |
| A | production (roster off) | production | P1, P2 |
| M | production + `back_in_play` + `map_awareness` + `extra_point` | production + extra-point section (M) | P1, P2, P3 |
| C (diagnostic) | production + `back_in_play` + `extra_point` | production + extra-point section (C) | P1/P2 deaths that naturally carry a C point, only if at least 5 exist |

- Model: cursor `gpt-5.6-luna`, one death per call, 2 repeats per arm.
- **M-minus-map** is not an arm and needs no call. It is derived
  deterministically from each M output (see "Map ablation").

### Block ownership

`back_in_play`, `map_awareness` and `extra_point` are **experiment-only,
view-only top-level N2 blocks**. They are not CoachInput (N1) fields, not
ScenarioContext, and never cited through `primary_context`. The validator
walks the serialized view literally (`claim_validation.resolve_view_path`),
so these paths resolve without a validator change. Where the facts live in
production is decided only if J passes, as a separate decision.

### Blocks

The derived facts are Experiment I's arithmetic, unchanged.

```json
"back_in_play": {"source": "previous_respawn | match_start", "seconds_back_in_play": 74.5}
```

`map_awareness` (M only):

```json
"map_awareness": {
  "map_checks_since_back_in_play": 0,
  "seconds_since_alive_map_check": null,
  "respawn_grace_seconds": 2.0,
  "threshold_seconds": 10.0,
  "eligible": true
}
```

`extra_point` (M and C only; null when the arm has no point):

| arm | `claim_id` | `rec_template_id` |
| --- | --- | --- |
| M | `no_map_check_since_back_in_play` (experiment-only, from Experiment I) | `rec_map_after_respawn` (production) |
| C | the death's C claim | its template (see "C diagnostic") |

The M claim's frame is "No map check was observed in the
{seconds_back_in_play}s since the player was back in play." Its evidence
paths are `back_in_play.seconds_back_in_play` and
`map_awareness.map_checks_since_back_in_play`.

Derivation (same as Experiment I):

- `back_in_play_time`: the end of the previous death episode, or else the
  first `in_match` snapshot.
- Checks: MAP_OVERLAY events that start strictly after
  `back_in_play_time + 2.0 s` and before the death.
- **Eligible:** 0 checks and `seconds_back_in_play >= 10.0`.
- If map observability is not `sufficient`, or `map_check_status` is not
  `observed` / `not_observed`, the death is not eligible and `map_awareness`
  is null.

**Threshold.** J uses the fixed 10 s threshold as pre-registered policy.
Threshold-band results are descriptive only. **J cannot be used to select or
validate a different threshold.**

## Map ablation

For each M output on an eligible death, **M-minus-map** removes only the map
unit. Nothing is rewritten.

### Map unit

The unit is located through the output's structure, not by fuzzy matching.
The production prompt requires two things. First, `claims[].sentence` is
"the assessment sentence you used". Second, advice appears only in
`recommendations`. J's prompt section also asks for the map fact "in one
assessment sentence" (see "Prompt sections").

- **Map sentence S:** `claims[i].sentence` for the claim whose `claim_id` is
  `no_map_check_since_back_in_play`.
- **Map recommendations R:** every `recommendations` item that matches the
  `rec_map_after_respawn` pattern or the map-mention regex. The regex is
  Experiment I's `_MAP_MENTION` plus the bare word "map".

### Ablatability (frozen, automatic)

An output is ablatable only if all of these hold:

1. Exactly one claim has the map `claim_id`, and its sentence is not empty.
2. The `assessment`, split with the frozen splitter `(?<=[.!?])\s+`, has
   exactly one segment equal to S (after stripping whitespace).
3. At least one other segment remains.
4. No remaining segment matches the map regex.
5. No item in R matches a non-map template pattern.

Outputs that fail are **not** edited into shape. They are excluded from the
ablation comparison, and the count is reported with its reasons.

### Ablated presentation

- `assessment` = the remaining segments in their original order, joined by
  one space.
- `recommendations` = the original list minus R, in order.
- No capitalization, connective or wording repair.
- Raters see `assessment` and `recommendations` only, for full and ablated
  outputs alike, as in Experiment I's sheet and on the public page.
  `limitations`, `claims` and `evidence_used` are not displayed and are not
  ablated.

### Check on stored outputs (read-only, no calls)

The rule was applied to Experiment I's 28 eligible M outputs. These were
written under I's prompt, so this is evidence about the output structure,
not a J result.

- **27 of 28 are ablatable.** The one failure (22-31-53 @208.5 r1)
  paraphrased the map fact instead of reusing the claim sentence. Rule 2
  excludes it.
- 13 outputs mention the map in `limitations`. Limitations are not
  displayed, so this has no effect.
- **All 27 ablated outputs have no recommendation left.** On all 14
  eligible deaths, the claim basis grounded no non-map recommendation, and
  A gave no recommendation in 28 of 28 outputs.

## C diagnostic (descriptive, never a gate)

- **Menu:** `death_low_ink` → `rec_refill_ink` (production), then
  `death_own_splat_before` → `rec_after_splat_reposition` (experiment-only).
  The first eligible claim wins.
- **Eligibility:** both claims use their existing, frozen production
  eligibility rules unchanged. No new movement or timing inference is added.
- **Excluded:**
  - clock claims;
  - map claims;
  - re-entry claims (`death_quick_redeath`, `death_out_of_play`,
    `rec_redeath_reset`);
  - `death_special_ready` (R4 and the production prompt);
  - roster claims (off).
- **`rec_after_splat_reposition`** (approved wording): "After you get a splat,
  reposition before taking the next fight."
  - Pattern:
    `(?i)\breposition\w*\b|\bfall\s+back\b|\b(?:safer|new)\s+(?:spot|position|angle)\b`.
  - The tool's tests check that the pattern matches none of the production
    template frames.
  - The template is added only to arm C's claim-basis entry for
    `death_own_splat_before`.
- **When it runs:** only if at least 5 deaths in P1 ∪ P2 naturally carry a
  menu claim. Otherwise C is dropped and recorded. The census is never
  extended to obtain C deaths.
- **What is reported:**
  - C vs M pairwise, using the pairwise question;
  - C's intended-point uptake and any-recommendation rate;
  - mean usefulness C, M and A on those deaths.

## Census and populations

### Frozen recording order

Before the first recording is analyzed, the tool writes `census_order.json`
and it is committed. It lists every candidate recording, sorted by recording
timestamp.

- **Candidate sources:**
  - local live player-view recordings;
  - GAME! tape held-out recordings (`analysis/game_tape_probe/`).
- **Rules each recording must pass** (being a GAME! tape recording is not a
  qualification by itself):
  - not used in Experiment H, Experiment I, the coaching gap study, the N2
    citation experiment or the citation-contract fixtures (derived from
    their manifests, not typed by hand);
  - not a Review-source recording;
  - not a public-site user submission;
  - known UI language;
  - after `analyze`, resolves to a player-view source.
- Recordings are analyzed in that order. One that fails a rule after
  analysis is skipped, with the reason recorded. Running `analyze` needs no
  model calls.

### The 50 eligible deaths

- Deaths are ordered by (recording order, death video time).
- The census stops at **exactly 50 M-eligible deaths**: the first 50 in that
  order. Analysis stops once the 50th is found.
- These 50 are relevance-labelled.
- **Go/no-go:** at least **20** of the 50 must be labelled relevant = yes.
  Otherwise J stops. Sampling never continues to reach a size.

### Populations

| population | definition | arms x repeats |
| --- | --- | --- |
| P1 primary | all relevant = yes among the 50 (at least 20) | A, M x 2 |
| P2 negative control / no harm | all relevant = no among the 50 | A, M x 2 |
| excluded | relevant = unclear | none |
| P3 policy | 20 not-eligible deaths with `sufficient` map observability, from the analyzed census recordings (frozen seed) | M x 2; automatic checks only, not rated |

- **`unclear` is final.** Labels are sealed. `unclear` deaths are never
  converted later and never enter P1 or P2.
- **P2 size for no harm.** If P2 has fewer than 5 deaths, the no-harm
  requirement cannot be evaluated, and J cannot recommend a production
  change ("inconclusive on no harm").
- **Supporting evidence** needs at least 8 ablatable P2 pairs (see
  "Supporting evidence").

## Relevance labels (footage-first, sealed before any output)

For each worksheet death, the rater watches the footage and records:

- `map_relevant`:
  - **yes** when, based only on the gameplay footage available before and
    around this death, checking the tactical map after returning to play and
    before the decisive engagement could reasonably have revealed
    information that might have changed a useful decision by the player;
  - **no** when a map check would not reasonably have changed a useful
    decision;
  - **unclear** when the footage does not support a confident yes or no.
  - The question is about coaching relevance, not whether the player should
    have checked the map.
- `ideal_coaching_note`: free text, what you would coach at this death.

Worksheet rules:

- It contains the 50 eligible deaths, plus a frozen-seed sample of 50
  not-eligible deaths with `sufficient` map observability from the same
  analyzed recordings.
  - The sample keeps eligibility hidden from the rater, and it gives the
    relevance × eligibility table.
  - Its labels never form a population.
  - P3 is drawn from it with the frozen seed.
- Rows are shuffled with a frozen seed. Eligibility, `back_in_play`, map
  counts and C points are hidden. The sheet shows only match, death time and
  a footage link.
- It is sealed in a commit before the key is joined and before any model
  call.

### Blinding of rating sheets

- **Neither the relevance label nor the ideal-coaching note appears anywhere
  on the absolute or pairwise rating sheets.** Population membership (P1 or
  P2) is not shown either; P1 and P2 rows are shuffled together.
- Every random step uses a seed fixed in `design.json` at the freeze (stop
  point 1):
  - the not-eligible worksheet sample;
  - the worksheet shuffle;
  - the P3 draw;
  - the absolute-sheet row order;
  - pairwise left/right assignment.
- The ideal-coaching note stays hidden until both rating sheets are sealed.
  It is then used for one descriptive diagnostic: "Did M identify
  approximately the map issue the footage-first label named?"
  (`approx_match` / `partial` / `no`).

## Prompt sections (frozen wording at stop point 1)

M and C share every line except the map lines. Production rules stay in
force, including "never treat map_check_before_death as should have
checked".

```text
ONE ADDITIONAL COACHING POINT (extra_point block)
- back_in_play.seconds_back_in_play is the time from when the player was
  last back in play (previous respawn, or match start) to this death.
- extra_point.claim_id names one claim_basis entry for this death.
  extra_point.rec_template_id names the recommendation for it.
- When extra_point is present, coach it as one additional point: state the
  claim's fact with its value from the view in one assessment sentence,
  and give one recommendation reworded from that entry's rec_frames.
- Do not say the death happened because of it.
- When extra_point is null, give no additional point.
```

M adds, after the first bullet:

```text
- map_awareness.map_checks_since_back_in_play counts map checks the player
  opened while alive in that time. The respawn map is not counted.
- Do not describe what the map would have shown (opponent positions, ink,
  routes); it was not observed.
```

"In one assessment sentence" is measurement structure, the existing
claim-sentence rule stated for this point. It does not change coaching
content.

## Measures

Automatic, frozen in the tool before any output:

- **Validator** per arm under its own claim basis:
  - `claim_validation.validate_assessment` hits per call;
  - blocked outputs;
  - an **adjusted count** for C that removes only R10 `no_template` hits
    whose recommendation matches `rec_after_splat_reposition`. The
    production validator does not know that template, so those hits occur
    by construction. M uses only the production template.
- **Any-recommendation rate** per arm.
- **Intended experimental recommendation uptake (M):** the output has a
  recommendation matching `rec_map_after_respawn` **and** an assessment
  sentence stating `seconds_back_in_play`, rounded to 0.5 s or whole
  seconds.
- **Ablatability:** rate and reasons, per population.
- **Map-rule violations (M):**
  - map advice on a non-eligible death;
  - a causal map claim;
  - described map content.
- **Value fidelity:** every number in a map or extra-point sentence equals
  the block value.
- **After both sheets are sealed:** automatic map-advice classification per
  output, compared with the relevance note diagnostic.

**Absolute rating** (blind, one shuffled sheet):

- Rows: P1 and P2 A/M outputs, plus C outputs where run. Ablated outputs are
  not on this sheet.
- Columns: `usefulness_1to5` (primary), `actionable_1to5`,
  `specific_to_this_death_1to5`, `supported_by_footage_yes_no`, and optional
  `notes`.
- There is no `map_use` column.
- The sheet is sealed before the key is opened.

**Pairwise rating** (blind, after the absolute sheet is sealed):

- Pairs:
  - each ablatable M output paired with its M-minus-map version, on P1 and
    P2;
  - C vs M at the same repeat index, where C runs.
- Left/right order comes from the frozen seed. Arm labels are hidden.
- One question: "Which coaching would be more useful and actionable for
  improving play in this situation?" The answer is `Left / Tie / Right`.
- The sheet is sealed before the key is opened.

**Blinding limit.** Within a pair, the map content and the missing
recommendation make the full output easy to spot. This is unavoidable and
is stated in the report.

## Decision rule

Results are **directional, not inferential**: one rater and a small n.

### Gate 1: safety (M, and C where run)

- 0 value-fidelity errors.
- M has 0 map-rule violations.
- Each arm's blocked outputs are no more than A's.
- Each arm's adjusted validator hits per call are no more than A + 0.25.

### Gate 2: primary value (P1, relevant = yes; all five required)

| # | requirement | threshold |
| --- | --- | --- |
| 2a | M intended experimental recommendation uptake | >= 50% of P1 M outputs |
| 2b | M outputs ablatable | >= 75% of P1 M outputs |
| 2c | mean usefulness M − A | >= 0.25 (pre-registered minimum practically meaningful difference; not a noise floor) |
| 2d | full M wins / (full M wins + ablated wins) | >= 0.65 (ties excluded) |
| 2e | full M wins / all ablation pairs | >= 0.50 (ties included) |

The tie rate is reported alongside 2d and 2e.

### Gate 3: no harm

- On P2, mean usefulness M − A >= −0.25. This requires at least 5 P2
  deaths; otherwise it is not evaluable (see "Populations").
- M's `supported_by_footage` rate is not lower than A's on P1 ∪ P2.

### Gate 4: policy (P3)

M gives 0 map-check recommendations on non-eligible deaths.

### Outcomes

| result | outcome |
| --- | --- |
| Gates 1–4 pass | Recommend a production change, as a separate decision (block ownership, prompt section, claim). Supporting evidence is reported with it. |
| Gate 1 fails | Production unchanged; failures recorded. |
| Gate 2 fails (any of 2a–2e) | Production unchanged; the failing components are named. |
| Gate 3 fails or is not evaluable | Production unchanged ("harm" or "inconclusive on no harm"). |
| Gate 4 fails | Production unchanged; investigate. |

### Supporting evidence (reported, never decides)

> Ablation preference on relevance = yes versus relevance = no deaths:
> full-M wins / all ablation pairs on P1 compared with the same share on P2.

- **Expected direction:** stronger preference for full M on relevance =
  yes.
- **Interpreted only with at least 8 ablatable P2 pairs.** With fewer, it is
  reported as "insufficient negative-control data" and not interpreted.
- **It cannot rescue a failed primary value gate, and it cannot make an
  otherwise passing J fail.**

### Reliability (descriptive)

- Per arm on P1: mean |usefulness r1 − r2| (repeat disagreement).
- Per death: agreement between repeat 1 and repeat 2 in the ablation pairs.
- This replaces reusing the Experiment H noise floor. The minimum practical
  effect (2c) is a separate, named judgement.

### Covariates (descriptive)

- `seconds_back_in_play` bands on P1: 10–12, 12–20, 20–40, over 40 s. Also
  the count of near-miss deaths at 8–10 s in the analyzed recordings (not
  called).
- The relevance × eligibility table.
- The ablation win share by whether the ablated output kept a
  recommendation.
- The ideal-coaching-note diagnostic.
- Per-match distribution, and a leave-one-match-out range of 2c and 2e.
- The C diagnostic.

## Calls (upper bound)

| population | deaths | arms | repeats | calls |
| --- | --- | --- | --- | --- |
| P1 + P2 | at most 50 | A, M | 2 | at most 200 |
| P3 | 20 | M | 2 | 40 |
| C diagnostic | natural deaths only, at least 5 if run | C | 2 | about 10 expected |
| **total** | | | | **about 250 at most** |

## Stop points

1. **This design.** Approval, then freeze (`design.json`, including the
   seeds).
2. **Census and relevance labels; no model calls.**
   - Commit `census_order.json`, run `analyze` in order, and stop at exactly
     50 eligible deaths.
   - The rater labels the blind worksheet, which is sealed.
   - Join the key and report:
     - P1/P2/unclear counts;
     - the relevance × eligibility table;
     - natural C availability;
     - skipped recordings.
   - Go/no-go is at least 20 relevant = yes deaths.
3. **Tool, tests, inputs, prompts and the ablation function; no model
   calls.**
   - Run a dry run with synthetic compliant M and C outputs: 0 validator
     hits beyond the pre-registered C adjustment, and literal block paths
     resolve.
   - Re-run the ablation check on Experiment I's stored outputs (27 of 28).
   - Sealed; go-ahead before any call.
4. **Outputs.** Automatic summary, ablated versions and the blind absolute
   sheet.
5. **Ratings.**
   - The sealed absolute ratings, then the pairwise sheet. Seal it.
   - Open the key; write the report, the supporting evidence and the
     post-seal diagnostics.
