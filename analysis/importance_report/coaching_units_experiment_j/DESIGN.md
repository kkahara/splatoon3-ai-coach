# Experiment J: incremental value of map-awareness coaching (draft)

**Status: DRAFT, revision 2, not frozen.** Nothing is built, analyzed or
called. There is no `design.json` until the design is approved at stop
point 1. Items marked **[review]** are open decisions.

Research only. Production code, prompts, claim catalog, validator and config
stay unchanged. Experiment H/I artifacts, ratings, flags, the Experiment I
postmortem and every frozen determination are not touched.

## Question

When map-awareness evidence is actually relevant to a coaching opportunity,
does using it produce coaching that is more useful and actionable than the
same production coaching with one equally grounded, non-map coaching point?

The measured quantity is the **incremental value attributable to map
evidence**, not the value of "one more supported recommendation".

## What Experiment I could not separate

Experiment I (frozen; `../coaching_units_experiment_i/`) compared A
(production) with M (production + map block + map prompt section). M scored
4.0 vs A 2.14 on eligible deaths, with 28/28 uptake. That difference mixes
three things:

1. **One more grounded recommendation.** M always added a supported
   recommendation; A often had none. Any extra concrete point may score
   higher.
2. **The map content of that point.** This is what J measures.
3. **Mechanical eligibility as relevance.** "0 checks and at least 10 s" says
   the coaching unit applies, not that the map mattered for this death.

Its safety failure (adjusted hits 0.7273/call) was citation form
(`primary_context.map_awareness.*` resolving to `unknown_block`), not grounding.
J uses a literal top-level N2 block (below), so that failure should not
recur. The stop point 3 dry run checks it before any call.

J removes confound 1 with an active control arm C and handles confound 3
with a footage-first relevance label that the rater seals before seeing any
output.

## Arms

| arm | view | system prompt | extra point |
| --- | --- | --- | --- |
| A | production (roster off) | production | none |
| C | production + `back_in_play` + `extra_point` | production + extra-point section (C) | one non-map point chosen by the C selection rule |
| M | production + `back_in_play` + `map_awareness` + `extra_point` | production + extra-point section (M) | the map-awareness point |

- C and M differ in one thing: **which** additional point is coached. Both
  see the same timing fact (`back_in_play`). Only M sees map counts.
- Framing, matched:
  - C: "Here is one additional grounded, actionable coaching point."
  - M: "Here is one additional grounded, actionable coaching point,
    derived specifically from map awareness."
- A is the production reference. It answers "is either extra point better
  than production?" and is descriptive for the map question.
- Model: cursor `gpt-5.6-luna`, one death per call, 2 repeats per arm.

### Block ownership (settles the open question in `docs/CITATION_NAMESPACES.md`)

`back_in_play`, `map_awareness` and `extra_point` are **experiment-only,
view-only top-level N2 blocks**. They are not CoachInput (N1) fields and not
ScenarioContext, and they are never cited through `primary_context`. The
validator already walks the serialized view literally
(`claim_validation.resolve_view_path`), so `map_awareness.seconds_back_in_play`
resolves with no validator change. Where these facts live in production is
decided only if J passes, as a separate decision.

### Blocks

The derived facts are Experiment I's arithmetic, unchanged, split into a
shared timing block and an M-only map block.

```json
"back_in_play": {
  "source": "previous_respawn | match_start",
  "seconds_back_in_play": 74.5
}
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

`extra_point` (C and M; null when the arm has no point for this death):

```json
"extra_point": {"claim_id": "death_low_ink", "rec_template_id": "rec_refill_ink"}
```

In M, `extra_point` is always
`{"claim_id": "no_map_check_since_back_in_play", "rec_template_id": "rec_map_after_respawn"}`
on eligible deaths. The experiment-only claim is the one from Experiment I.
Its frame is "No map check was observed in the {seconds_back_in_play}s since
the player was back in play.", with evidence paths
`back_in_play.seconds_back_in_play` and
`map_awareness.map_checks_since_back_in_play`.

Derivation (same as Experiment I):
- `back_in_play_time` is the end of the previous death episode, or else the
  first `in_match` snapshot.
- Checks are MAP_OVERLAY events that start strictly after
  `back_in_play_time + 2.0 s` and before the death.
- **Eligible** means 0 checks and `seconds_back_in_play >= 10.0`.
- If map observability is not `sufficient`, or `map_check_status` is not
  `observed` / `not_observed`, the death is not eligible and
  `map_awareness` is null.

## The C selection rule

C needs one non-map coaching point per death that is grounded in existing
evidence, as specific and actionable as M's point, and fixed before any J
output. The rule is a frozen priority list over `claim_basis` entries, and
it uses only that death's production view.

### Constraints on the menu

| excluded | why |
| --- | --- |
| every map claim (`death_map_overlay_before_false`, `process_map_after_respawn`, `map_check_observed_before_death`) | C must not depend on map evidence |
| `death_quick_redeath`, `principle_redeath_out_of_play`, `rec_redeath_reset` | same re-entry moment as M; too semantically close |
| `death_out_of_play` | about the respawn and re-entry interval |
| `death_special_ready` | the only natural recommendation is special usage. The production prompt says "Statement only — never 'should have used special'", and R4 `SPECIAL_USAGE` blocks it |
| `death_while_outnumbered` and other roster claims | roster is off in production (`coach.roster_evidence=false`); numbers awareness is not available |
| non-coachable claims (`special_not_ready_at_death`, `timer_sample_exists`, count claims, …) | not coachable in production |

### Menu (priority order, first eligible wins)

| priority | claim (existing, coachable, level A) | recommendation template | template status |
| --- | --- | --- | --- |
| 1 | `death_low_ink` (LOW_INK interval contains the death) | `rec_refill_ink` "Refill your ink before taking the next fight." | production |
| 2 | `death_own_splat_before` ("You got a splat {t}s before you were splatted.") | `rec_after_splat_reposition` "After you win a fight, reposition before taking the next one." | experiment-only **[review wording]** |
| 3 | `death_final_30s` | `rec_final_seconds_survive` "In the last 30 seconds, take only fights you can survive." | experiment-only **[review wording]** |
| 4 | `death_first_30s` | `rec_opening_ink_first` "In the opening seconds, ink forward with your team before taking a fight." | experiment-only **[review wording]** |

- The order is fixed before outputs and follows how specific the fact is to
  this death, not expected usefulness: player state at the death, then an
  event seconds before it, then match-clock context.
- Ties cannot happen: the list is ordered, and each claim has one template.
- If no menu claim is eligible, there is **no C point**. The death cannot
  enter the M-vs-C efficacy population. The criteria are not relaxed to
  find one.
- Each experiment-only template gets a regex that must not match any
  production template's pattern. This is checked in the tool's tests. The
  template is added only to arm C's claim basis entry for the selected
  claim.
- Both arms state the selected claim's fact with its value. This keeps the
  specificity matched: one death-specific fact plus one habit
  recommendation, as in M.

### Feasibility estimate (aggregate counts only; no J deaths selected)

Applied read-only to the 125 deaths with stored `coach_inputs` in the local
public-site mirror (33 matches):

| menu | deaths with a C point | share |
| --- | --- | --- |
| production-only (`death_low_ink`) | 5 | 4% |
| death-specific (`death_low_ink`, `death_own_splat_before`) | 8 | 6% |
| full menu above | 31 | 25% |

First picks under the full menu were final 30 s 16, first 30 s 7, low ink 5
and own splat 3.

Per-claim eligibility was:
- `death_final_30s` 17
- `death_map_overlay_before_false` 22
- `death_quick_redeath` 14
- `death_first_30s` 7
- `death_low_ink` 5
- `death_special_ready` 5
- `death_own_splat_before` 3

What this means:

- **Production supports almost no matched non-map point today.** Apart from
  the excluded re-entry template, `rec_refill_ink` is the only non-map
  production recommendation, and it applies to about 4% of deaths.
- **Requiring a C point narrows and skews the population.** Even with the
  full menu, about three quarters of deaths have no C point. Of those that
  do, 23 of 31 are clock points, so the efficacy population would be
  concentrated in the first and last 30 s of matches.
- **Clock points are probably weaker than M's point.** That would favour M
  for reasons unrelated to map content, reintroducing a milder form of
  confound 1.

**[review] C menu decision. This is the main open item.** Options:

1. **Full menu (written above).** About 25% availability. It needs three
   experiment-only templates. The efficacy population is skewed toward
   match-clock deaths, so clock share is a pre-registered descriptive
   covariate and is reported at stop point 2.
2. **Death-specific menu** (`death_low_ink`, `death_own_splat_before`). This
   gives the best specificity match with M, but only about 6% availability.
   Reaching the target size would need roughly 900 census deaths (estimate
   below).
3. **Production-only** (`death_low_ink`). Nothing new is added to the claim
   catalog, but about 4% availability is not feasible at the target size.

None of the three gives a C point at most deaths, because existing evidence
contains no universally available, non-map, non-re-entry coaching fact.
Adding one would be a new evidence or catalog design, outside J.

Rough census size for 24 efficacy deaths, assuming eligible 0.68 (Experiment
I) and relevant 0.6 (a guess; measured at stop point 2):

| menu | deaths needed |
| --- | --- |
| full | about 235 |
| death-specific | about 920 |
| production-only | about 1470 |

At about 3.8 deaths per match, the full menu needs about 60 matches.

## Populations

All membership is decided at stop point 2 from inputs and sealed relevance
labels only, before any call. Deaths are labelled with the relevance
worksheet below.

| population | definition | arms x repeats | purpose |
| --- | --- | --- | --- |
| P1 efficacy | M-eligible, relevance `yes`, C point available | A, C, M x 2 | primary |
| P2 no-harm | M-eligible, relevance `no`, C point available | C, M x 2 **[review: add A]** | negative control: M must not do worse when the map did not matter |
| P3 policy | not M-eligible (fails threshold or has a map check), sufficient observability | M x 2, automatic checks only, not rated | M must give no map advice |
| excluded | relevance `unclear`; M-eligible with no C point; insufficient observability | none | counts reported |

- **`unclear` is final.** Labels are sealed. `unclear` deaths are never
  converted later and never enter P1 or P2.
- **Size.** P1 needs at least 20 deaths. The target is 24 if the census
  provides them without stretching any criterion. If P1 has more than 24,
  24 are drawn with a frozen seed. P2 is capped at 24 and P3 at 20, with
  the same seed.
- If P1 has fewer than 20 at stop point 2, there are two options, both
  decided before any call: extend the census with recordings named **before**
  their labels are seen, under the same rules; or stop. Nothing is relaxed.

### Census sources and exclusions

Included:
- Local live player-view analyses not used by any earlier coaching
  experiment or study.
- GAME! tape held-out recordings (`analysis/game_tape_probe/`) that resolve to
  a player-view source with `sufficient` map observability and a known UI
  language. Running `analyze` needs no model calls.

Excluded:
- Review-source recordings.
- Public-site user submissions (consent).
- Any match whose recording appears in Experiment H, Experiment I, the
  coaching gap study, the N2 citation experiment or the citation-contract
  fixtures. The tool derives this list from those manifests; it is not
  typed by hand.

**[review]** The feasibility counts above came from the local public-site
mirror, used only as aggregate counts. No J death comes from it.

## Relevance labels (footage-first, sealed before any output)

For each candidate death, the rater watches the footage before the death and
records:

- `map_relevant`: `yes` / `no` / `unclear`. **[review wording]**: "Yes if
  knowing the map state while back in play would plausibly have changed what
  the player knew about the situation that led to this death (for example a
  flank or push visible on the map). No if the death came from something the
  map would not have shown. Unclear if you cannot tell from the footage."
- `ideal_coaching_note`: free text, what you would coach at this death.

Worksheet rules:

- It contains every M-eligible death with a C point, plus a frozen-seed
  random sample of not-eligible deaths of equal size. The sample exists to
  measure how relevance relates to mechanical eligibility.
- Rows are shuffled. Eligibility, `back_in_play`, map counts and the C point
  are hidden. The sheet shows only match, death time and a footage link.
- It is sealed in a commit before the key is joined and before any model
  call.
- **The ideal-coaching note is hidden during output rating.** It never
  appears on the absolute or pairwise sheets.
- After all ratings are sealed, it is used for one diagnostic only: "Did M
  identify approximately the map issue the footage-first label named?"
  (`approx_match` / `partial` / `no`). This is descriptive and never gates.

## Prompt sections (draft wording) **[review]**

C and M share every line except the map lines. Production rules stay in
force, including "never treat map_check_before_death as should have
checked".

```text
ONE ADDITIONAL COACHING POINT (extra_point block)
- back_in_play.seconds_back_in_play is the time from when the player was
  last back in play (previous respawn, or match start) to this death.
- extra_point.claim_id names one claim_basis entry for this death.
  extra_point.rec_template_id names the recommendation for it.
- When extra_point is present, coach it as one additional point: state the
  claim's fact with its value from the view, and give one recommendation
  reworded from that entry's rec_frames.
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

The length difference (two bullets) is the map content itself, and it is
reported.

## Measures

Automatic, frozen in the tool before any output:

- **Validator.** These are measured per arm under its own claim basis:
  - `claim_validation.validate_assessment` hits per call;
  - blocked outputs;
  - an **adjusted count** that removes only R10 `no_template` hits whose
    recommendation matches the arm's own experiment-only template. The
    production validator does not know these templates, so these hits occur
    by construction. Nothing else is removed. The adjustment is pre-registered
    here, not added later as an amendment.
- **Any-recommendation rate** per arm: at least one recommendation.
- **Intended experimental recommendation uptake** per arm (C and M).
  - Uptake means the output has a recommendation matching the selected
    template's pattern **and** states the selected claim's value.
  - For C, that is the C template plus the claim value. For M, it is
    `rec_map_after_respawn` plus `seconds_back_in_play`.
  - Values are rounded to 0.5 s or whole seconds.
- **A's spontaneous rate** of the C point and of the map point (descriptive:
  how often production already says it).
- **Map-rule violations** (M):
  - map advice on a non-eligible death;
  - a causal map claim;
  - described map content.
- **Value fidelity:** every number in a map or extra-point sentence equals
  the block value.
- **Map mentions in C** (descriptive).

Absolute rating (blind):
- One shuffled sheet holds P1 A/C/M and P2 C/M outputs, seed frozen at stop
  point 3.
- Columns: `usefulness_1to5` (primary), `actionable_1to5`,
  `supported_yes_no`, `map_use` (correct / incorrect / not_mentioned),
  `notes`. **[review columns]**
- No relevance label and no ideal-coaching note appear on it. The sheet is
  sealed before the key is opened.

Pairwise rating (blind, after the absolute sheet is sealed):
- P1 only, C vs M at the same repeat index, so 2 pairs per death (48 at 24
  deaths). Left/right order is randomized by a frozen seed and arm labels
  are hidden.
- Question: "Which is more useful and actionable coaching for this death?"
  The answer is left / right / tie.
- Sealed before the key is opened.

Blinding limit: M outputs mention the map, so the arm can often be guessed.
This is unavoidable and stated in the report.

## Decision rule

Gates are evaluated in this order: safety, matched control, value, no harm,
policy. Results are **directional, not inferential**: one rater and a small
n.

1. **Safety (C and M).**
   - 0 value-fidelity errors.
   - M has 0 map-rule violations.
   - Each arm's blocked outputs are no more than A's.
   - Each arm's adjusted validator hits per call are no more than A + 0.25.
   - Failure: production is unchanged and the failures are recorded.
2. **Matched control (P1).**
   - Intended uptake is at least 50% in C and in M. **[review: 50% minimum]**
   - |uptake M − uptake C| is at most 15 percentage points.
   - Failure: the arms did not deliver comparable extra points, so value is
     not interpretable. Production is unchanged.
3. **Value (P1).** All three must hold:
   - M wins / (M wins + C wins) >= 0.65, with ties excluded from this
     denominator;
   - M wins / all pairs >= 0.50, with ties included;
   - mean usefulness M − C > **minimum practical effect 0.25**
     **[review: confirm 0.25 as the smallest difference you consider
     meaningful]**.
   - The tie rate is reported separately.
4. **No harm.**
   - On P2, mean usefulness M − C >= −0.25 (the same named effect, used as
     a no-harm margin).
   - M's supported rate is not lower than C's on P1 + P2.
5. **Policy (P3).** M gives 0 map-check recommendations on non-eligible
   deaths.
6. **Outcomes.**
   - Passes all: recommend a production change as a separate decision
     (block ownership, prompt section, claim and template).
   - Passes 1, 2, 4 and 5 but fails 3: the map point is handled safely but
     shows no value over a matched non-map point. Production is unchanged.
   - Fails 1, 2, 4 or 5: production is unchanged; investigate.

### Reliability (descriptive, not a gate)

- Per arm on P1: mean |usefulness r1 − r2| (repeat disagreement), with C's
  value as the headline reliability diagnostic.
- Pairwise agreement between repeat 1 and repeat 2 per death.
- This replaces Experiment I's reuse of the Experiment H noise floor. The
  minimum practical effect is a separate, named judgement, not a noise
  estimate.

### Covariates (descriptive)

- `seconds_back_in_play` bands on P1: 10–12, 12–20, 20–40, over 40 s. Also
  the census count of near-miss deaths at 8–10 s (not called).
- **J uses the fixed 10 s threshold as pre-registered policy. It cannot be
  used to select or validate a different threshold.**
- C point type (low ink / own splat / final 30 s / first 30 s), and M − C by
  type.
- Relevance × eligibility table from the worksheet.
- Post-seal diagnostic: M vs the ideal-coaching note.
- Mean usefulness M − A and C − A.
- Per-match distribution, and a leave-one-match-out range for M − C.

## Calls (at target size)

| population | deaths | arms | repeats | calls |
| --- | --- | --- | --- | --- |
| P1 | 24 | A, C, M | 2 | 144 |
| P2 | up to 24 | C, M | 2 | up to 96 |
| P3 | up to 20 | M | 2 | up to 40 |
| **total** | | | | **up to 280** |

P2 is likely much smaller than its cap.

## Stop points

1. **This design.** Review, then freeze (`design.json`).
2. **Census and relevance labels; no model calls.**
   - Run `analyze` on the census sources and compute blocks, eligibility and
     C points.
   - The rater fills the blind worksheet, which is sealed.
   - Join the key and report:
     - population sizes, including the P1 count;
     - the relevance × eligibility table;
     - C-point availability and clock share;
     - excluded counts.
   - Go/no-go is decided without any outputs.
3. **Tool, tests, built inputs and both prompts; no model calls.** Run a
   dry run with synthetic compliant C and M outputs: 0 validator hits beyond
   the pre-registered by-construction R10 hits, and literal block paths
   resolve. Sealed; go-ahead before any call.
4. **Outputs.** Automatic summary and the blind absolute sheet.
5. **Ratings.** The sealed absolute ratings, then the pairwise sheet. Seal it,
   open the key, write the report and the post-seal diagnostic.

## Review checklist

- **C menu:** full, death-specific or production-only (main decision), and
  the three template wordings if experiment-only templates stay.
- Whether `death_first_30s` ("opening") is too close to M's "before you move
  up" point. If so, drop it (availability falls to about 19%).
- Relevance-label wording.
- A in P2.
- The 50% minimum uptake.
- Confirm 0.25 as the minimum practical effect and no-harm margin.
- Rating columns.
- Census sources, and whether the GAME! tape corpus is in scope.
