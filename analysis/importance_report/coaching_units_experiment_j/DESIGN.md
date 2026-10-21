# Experiment J: incremental value of map-awareness coaching (draft)

**Status: DRAFT, revision 3, not frozen.** Nothing is built, analyzed or
called. There is no `design.json` until the design is approved at stop
point 1. Items marked **[review]** are open decisions.

Research only. Production code, prompts, claim catalog, validator and config
stay unchanged. Experiment H/I artifacts, ratings, flags, the Experiment I
postmortem and every frozen determination are not touched.

## Question

On fresh deaths independently judged map-relevant before any model output
exists, does the map-specific portion of M add useful, actionable coaching
to the otherwise identical output?

## Why the design changed (revision 2 → 3)

Revision 2 used an active control C (one non-map point per death). A
read-only count over 125 locally stored deaths showed that a clean control
is rarely available:

| C menu | deaths with a C point |
| --- | --- |
| death-specific (`death_low_ink`, `death_own_splat_before`) | 6% |
| full menu (adding first/last 30 s clock points) | 25%, mostly clock points |

The full menu would have skewed the population toward the first and last
30 s of matches, so J could have answered the wrong question. New evidence is
not invented to rescue a three-arm design. The primary test is therefore
**M vs A, plus a within-output map ablation**. C becomes a small diagnostic
on deaths that naturally carry a death-specific C point.

## Arms

| arm | view | system prompt | where |
| --- | --- | --- | --- |
| A | production (roster off) | production | P1, P2 |
| M | production + `back_in_play` + `map_awareness` + `extra_point` | production + extra-point section (M) | P1, P2, P3 |
| C (diagnostic) | production + `back_in_play` + `extra_point` | production + extra-point section (C) | P1/P2 deaths that naturally carry a C point |

- Model: cursor `gpt-5.6-luna`, one death per call, 2 repeats per arm.
- **M-minus-map** is not an arm and needs no call. It is derived
  deterministically from each M output (see "Map ablation").

### Block ownership

`back_in_play`, `map_awareness` and `extra_point` are **experiment-only,
view-only top-level N2 blocks**. They are not CoachInput (N1) fields, not
ScenarioContext, and never cited through `primary_context`. The validator
walks the serialized view literally (`claim_validation.resolve_view_path`),
so `map_awareness.map_checks_since_back_in_play` resolves without a
validator change. This avoids Experiment I's citation-form failure
(`primary_context.map_awareness.*` → `unknown_block`). Where these facts
live in production is decided only if J passes, as a separate decision.

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
| C | the death's C claim | its template (below) |

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

## Map ablation (primary contrast)

For each M output on an eligible death, **M-minus-map** removes only the map
unit. Nothing is rewritten.

### Map unit

The unit is located through the output's structure, not by fuzzy matching.
The production prompt requires two things. First, `claims[].sentence` is
"the assessment sentence you used". Second, advice appears only in
`recommendations`.

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
5. No item in R matches a non-map template pattern. An item such as "refill
   ink and check the map" is mixed, so the output is not ablatable.

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
written under I's prompt, not J's, so this is evidence about the output
structure, not a J result.

- **27 of 28 are ablatable.** The one failure (22-31-53 @208.5 r1)
  paraphrased the map fact in the assessment ("You then spent 18.0 seconds
  back in play without an observed map check"), so S did not appear
  verbatim. Rule 2 excludes it.
- 13 outputs mention the map in `limitations`. Limitations are not displayed,
  so they don't affect ablatability.
- **All 27 ablated outputs have no recommendation left.** On all 14
  eligible deaths, the claim basis grounded no non-map recommendation
  template, and A gave no recommendation in 28 of 28 outputs.

### What the ablation can and cannot separate

On these deaths, current evidence usually supports no other recommendation.
So "full M vs M-minus-map" will mostly compare "map fact + map
recommendation" against "the same statements with no recommendation". The
ablation tightly controls the death, call, wording and claim selection.
**It does not by itself separate map content from having any advice.**

J separates them with a contrast it already plans: **relevance**. If the
ablation gain came from "any advice beats none", it should be about the same
on deaths the rater labelled map-relevant (P1) and not relevant (P2). If it
comes from map content, it should be larger on P1. Ablation pairs are
therefore built on P1 and P2, and the comparison is pre-registered (decision
rule 5). **[review]**

Limit: the same rater labels relevance (sealed, from footage) and later
rates outputs. Labels and notes are hidden on the rating sheets, but the
rater may remember a death. This is stated in the report.

## C diagnostic (small, not a gate)

- **Menu:** `death_low_ink` → `rec_refill_ink` (production), then
  `death_own_splat_before` → `rec_after_splat_reposition` (experiment-only).
  The first eligible claim wins.
- **Excluded:**
  - clock claims, which manufactured availability;
  - map claims;
  - re-entry claims (`death_quick_redeath`, `death_out_of_play`,
    `rec_redeath_reset`);
  - `death_special_ready` (R4 and the production prompt);
  - roster claims (off).
- **Draft wording for `rec_after_splat_reposition` [review]:** "After you get a
  splat, reposition before taking the next fight."
  - It follows the production template style: a short imperative with the
    "before taking the next fight" ending, as in `rec_refill_ink`.
  - It deliberately omits "instead of immediately continuing forward". The
    evidence (`time_since_previous_splat`) shows a splat and then a death t
    seconds later, not the player's movement, so that clause would assert
    something unobserved.
  - It avoids "trade" / "exchange", which the claim's `contradicted_by`
    forbids.
  - Draft pattern:
    `(?i)\breposition\w*\b|\bfall\s+back\b|\b(?:safer|new)\s+(?:spot|position|angle)\b`.
    The tool's tests check that it matches no production template pattern.
- **Where it runs:** C runs only on P1/P2 deaths that naturally carry one of
  these claims. There is no target N, and no deaths are selected to reach
  one.
- **Count:**
  - The expected count is small: about 6% of deaths in the count above, so a
    few deaths.
  - At stop point 2, C is built and called only if at least 5 such deaths
    exist. **[review]** Otherwise it is dropped and recorded.
  - The A and M outputs on those deaths are reused, so only C calls are
    added.
- **What is reported:** C vs M pairwise on the same question, C's
  intended-point uptake and any-recommendation rate, all descriptive. The
  15-point uptake-match gate from revision 2 is removed; it protected an
  active-control comparison that no longer decides J.

## Populations

All membership is decided at stop point 2 from inputs and sealed relevance
labels only, before any call.

| population | definition | arms x repeats | purpose |
| --- | --- | --- | --- |
| P1 efficacy | M-eligible, relevance `yes` | A, M x 2 (+ C where natural) | primary |
| P2 no-harm / relevance contrast | M-eligible, relevance `no` | A, M x 2 (+ C where natural) | no harm; relevance contrast for the ablation |
| P3 policy | not M-eligible, sufficient observability | M x 2; automatic checks only, not rated | M must give no map advice |
| excluded | relevance `unclear`; insufficient observability | none | counts reported |

- **`unclear` is final.** Labels are sealed. `unclear` deaths are never
  converted later and never enter P1 or P2.
- **Size.** P1 needs at least 20 deaths, with a target of 24. If more
  qualify, 24 are drawn with a frozen seed. P2 is capped at 24 and P3 at 20,
  with the same seed. Nothing is relaxed to reach a size.
- **Census.** Recordings are analyzed in a frozen order (sorted by recording
  timestamp) until **50 M-eligible deaths** exist **[review]**. This stopping
  rule is mechanical and does not depend on labels; at an assumed relevance
  rate of 0.6 it gives about 30 P1 candidates. If P1 has fewer than 20 after
  labelling, the next frozen batch is analyzed and labelled under the same
  rules. Otherwise J stops. This is decided before any call.

### Census sources and exclusions

- **Included:**
  - local live player-view analyses;
  - GAME! tape held-out recordings (`analysis/game_tape_probe/`).
  - Each recording must pass the same frozen rules: untouched by earlier
    coaching work, a player-view source with `sufficient` map observability,
    and a known UI language.
  - Being a GAME! tape recording is not an inclusion qualification by
    itself.
  - Running `analyze` needs no model calls.
- **Excluded:**
  - Review-source recordings.
  - Public-site user submissions (consent).
  - Any recording used in Experiment H, Experiment I, the coaching gap
    study, the N2 citation experiment or the citation-contract fixtures. The
    tool derives this list from those manifests, not by hand.
- The feasibility counts above came from the local public-site mirror as
  aggregate counts only. No J death comes from it.

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

- It contains every M-eligible census death, plus a frozen-seed random
  sample of not-eligible deaths of equal size. The sample is used to measure
  how relevance relates to mechanical eligibility.
- Rows are shuffled. Eligibility, `back_in_play`, map counts and C points
  are hidden. The sheet shows only match, death time and a footage link.
- It is sealed in a commit before the key is joined and before any model
  call.
- **The ideal-coaching note never appears on any rating sheet.** After all
  ratings are sealed, it is used for one descriptive diagnostic: "Did M
  identify approximately the map issue the footage-first label named?"
  (`approx_match` / `partial` / `no`).

## Prompt sections (draft wording) **[review]**

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

The phrase "in one assessment sentence" supports ablatability by stating
the existing claim-sentence rule for this point. It does not require any
particular wording.

## Measures

Automatic, frozen in the tool before any output:

- **Validator** per arm under its own claim basis:
  - `claim_validation.validate_assessment` hits per call;
  - blocked outputs;
  - an **adjusted count** for C that removes only R10 `no_template` hits
    whose recommendation matches `rec_after_splat_reposition`. The
    production validator does not know that template, so those hits happen
    by construction. M uses only the production template. This adjustment
    is pre-registered here, not added later.
- **Any-recommendation rate** per arm.
- **Intended uptake (M):** a recommendation matching `rec_map_after_respawn`
  **and** a statement of `seconds_back_in_play`, with values rounded to
  0.5 s or whole seconds.
- **Ablatability:** rate and reasons, per population.
- **Map-rule violations (M):**
  - map advice on a non-eligible death;
  - a causal map claim;
  - described map content.
- **Value fidelity:** every number in a map or extra-point sentence equals
  the block value.
- **After ratings are sealed:** automatic map-advice classification per
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
- Left/right order is randomized by a frozen seed. Arm labels are hidden.
- One question: "Which coaching would be more useful and actionable for
  improving play in this situation?" The answer is `Left / Tie / Right`.
- The sheet is sealed before the key is opened.

**Blinding limit.** The map content and the missing recommendation make the
full output easy to spot within a pair. This is unavoidable and is stated.

## Decision rule

Gates are evaluated in this order. Results are **directional, not
inferential**: one rater and a small n.

1. **Safety (M; C where run).**
   - 0 value-fidelity errors.
   - M has 0 map-rule violations.
   - Blocked outputs are no more than A's.
   - Adjusted validator hits per call are no more than A + 0.25.
   - Failure: production is unchanged and the failures are recorded.
2. **Uptake (M on P1).** Intended uptake is at least 50% of M outputs.
   Failure: the intervention is not reliably reaching the user, and
   production is unchanged.
3. **Ablatability (M on P1).**
   - At least 75% of M outputs are ablatable **[review]**.
   - Failure: the ablation sample is too selected to read, and the result is
     reported as not interpretable.
4. **Incremental map value (P1, ablation pairs).** Both must hold:
   - full-M wins / (full-M wins + ablated wins) >= 0.65, with ties excluded;
   - full-M wins / all pairs >= 0.50, with ties included.
   - The tie rate is reported separately.
5. **Attributable to map relevance [review].**
   - The ablation win share (wins / all pairs) on P1 must exceed the same
     share on P2.
   - If P2 has fewer than 8 ablatable pairs, this is reported as untestable
     and the outcome says so.
   - Fail: the gain is not attributable to map content, only to having
     advice.
6. **Production value (P1).** Mean usefulness M − A > **0.25, the
   pre-registered minimum practically meaningful difference** (not a noise
   floor).
7. **No harm (P2).**
   - Mean usefulness M − A >= −0.25.
   - M's footage-supported rate is not lower than A's on P1 + P2.
8. **Policy (P3).** M gives 0 map-check recommendations on non-eligible
   deaths.
9. **Outcomes.**
   - Passes all: recommend a production change as a separate decision
     (block ownership, prompt section, claim).
   - Passes all except 5: map advice helps where production has no advice,
     but the gain is not shown to depend on map relevance. Production is
     unchanged.
   - Fails 4 or 6 with gates 1–3 and 7–8 passing: the map point is handled
     safely but shows no incremental value. Production is unchanged.
   - Fails 1, 2, 3, 7 or 8: production is unchanged; investigate.

### Reliability (descriptive, not a gate)

- Per arm on P1: mean |usefulness r1 − r2| (repeat disagreement).
- Per death: agreement between repeat 1 and repeat 2 in the ablation pairs.
- This replaces reusing the Experiment H noise floor. The minimum practical
  effect is a separate, named judgement.

### Covariates (descriptive)

- `seconds_back_in_play` bands on P1: 10–12, 12–20, 20–40, over 40 s. Also
  the census count of near-miss deaths at 8–10 s (not called).
- The relevance × eligibility table.
- The ablation win share by whether the ablated output kept a
  recommendation.
- Mean usefulness M − A on P2.
- The ideal-coaching-note diagnostic.
- Per-match distribution, and a leave-one-match-out range of the P1 win
  share.
- The C diagnostic results.

## Calls (at target size)

| population | deaths | arms | repeats | calls |
| --- | --- | --- | --- | --- |
| P1 | 24 | A, M | 2 | 96 |
| P2 | up to 24 | A, M | 2 | up to 96 |
| C diagnostic | natural, about a few | C | 2 | about 10 |
| P3 | up to 20 | M | 2 | up to 40 |
| **total** | | | | **about 240 at most** |

## Stop points

1. **This design.** Review, then freeze (`design.json`).
2. **Census and relevance labels; no model calls.**
   - Run `analyze` in frozen order and compute blocks, eligibility and C
     points.
   - The rater fills the blind worksheet, which is sealed.
   - Join the key and report:
     - P1/P2/P3 sizes;
     - the relevance × eligibility table;
     - natural C availability;
     - excluded counts.
   - Go/no-go is decided without any outputs.
3. **Tool, tests, inputs, prompts and the ablation function; no model
   calls.**
   - Run a dry run with synthetic compliant M and C outputs: 0 validator
     hits beyond the pre-registered C adjustment, and literal block paths
     resolve.
   - Re-run the ablation check on Experiment I's stored outputs (27 of 28,
     as above).
   - Sealed; go-ahead before any call.
4. **Outputs.** Automatic summary, ablated versions and the blind absolute
   sheet.
5. **Ratings.**
   - The sealed absolute ratings, then the pairwise sheet. Seal it.
   - Open the key; write the report and the post-seal diagnostics.

## Review checklist

- **Relevance contrast as gate 5** (main item). Without it, the ablation
  measures "map advice vs no advice" on these deaths. Also review the
  8-pair minimum for P2.
- **75% ablatability floor.** Also whether to add "in one assessment
  sentence" to the prompt section.
- **`rec_after_splat_reposition`:** wording and pattern.
- **C diagnostic:** the 5-death minimum.
- **Census:** the 50-eligible-death stopping point.
- **Prompt sections:** final wording.
