# Experiment J amendment 3 (2026-10-21): R5 map_absence window adjustment

`DESIGN.md`, `design.json`, `AMENDMENTS.md` (Amendment 1), `AMENDMENT_2.md`
and every stop point 2 artifact stay byte-identical (hashes in
`SHA256SUMS`). This amendment changes only how validator hits are counted for
arm M. It was approved by the rater (option A) before any model call.

## When it was made, and what had been seen

- Made at stop point 3, after the first dry run (commit `09bee94`) and before
  any model call. No J model output exists.
- Seen at that point:
  - the stop point 2 results (populations, label counts, the relevance ×
    eligibility table, C availability);
  - the stop point 3 dry run on **synthetic** outputs only.

## Finding

- Production rule R5 `map_absence` flags any "no map check" sentence unless
  `map.map_check_before_death.status` is `not_observed`. That status counts
  the respawn map.
- J's frozen M claim frame begins "No map check was observed…". So a
  compliant M output is a validator hit by construction whenever the status
  is `observed`.
- In the dry run, 30 of the 40 P1 and P2 deaths have status `observed`.
  - Each compliant M output on those deaths has 2 R5 `map_absence` hits (the
    assessment sentence and the claim sentence), 60 in total.
  - The 10 `not_observed` deaths have none.
  - The 6 compliant C outputs have 0 hits after the pre-registered C
    adjustment.
- The stop point 3 criterion (0 validator hits beyond the pre-registered C
  adjustment) was therefore not met. Without an adjustment, Gate 1 would
  fail for M by construction.
- This is the same mechanism Experiment I's Amendment 1 (2026-10-20)
  handled. J's frozen design did not carry that adjustment over.

## Rule (identical to Experiment I Amendment 1)

The rule is Experiment I's `r5_window_exempt`
(`tools/reports/coaching_units_experiment_i.py`), called unchanged.

An R5 hit is removed from arm M's **adjusted** count only if all of these
hold:

1. The hit is rule `R5`, check `map_absence`.
2. The call's own `map_awareness` block is non-null, and
   `map_checks_since_back_in_play == 0`.
3. The flagged sentence ties the absence to the back-in-play window, by
   either of:
   - an anchor: "since", back in play / into the fight / action / game,
     respawn…, return…, rejoin…, or "came back";
   - a cited seconds value equal to the call's own `seconds_back_in_play`,
     at 0.5 s or whole-second rounding.

**The one J-specific detail.** J's frozen block layout keeps
`seconds_back_in_play` in `back_in_play.seconds_back_in_play`, not in
`map_awareness`. The rule reads it from there. This is the same derived value
(Experiment I's arithmetic, unchanged). No condition is added, removed or
widened.

Like Experiment I, the rule does not look at `map_awareness.eligible`. It
applies wherever the call's own block shows 0 alive checks, including an M
call on a not-eligible P3 death with 0 checks and fewer than 10 s back in
play.

## Scope

- **Arm M only.** Arm A has no `map_awareness` block, and arm C has no
  `map_awareness` block, so the rule never applies to them. That was also
  true in Experiment I.
- **Nothing else is removed.** Other R5 checks, every other rule, causal map
  claims, unsupported recommendations and unresolved evidence paths all stay
  in the count. An unanchored "no map check" sentence stays a hit.
- **Map-rule violations are separate and unaffected:** map advice on a
  not-eligible death, a causal map claim, and described map content.

## Counting and reporting

- The production validator is not changed. Every record keeps the
  production count.
- Each record and every summary reports, side by side:
  - the production validator hits;
  - the C adjustment (arm C only, pre-registered);
  - the Amendment 3 R5 window exemption (arm M only);
  - the adjusted hits.
- For Gate 1, an arm's adjusted hits are its production hits minus its
  own adjustment. Arm A has no adjustment.
- The stop point 3 dry-run criterion uses the adjusted hits.

## Unchanged

Everything else stays as frozen:

- model inputs, user prompts and system prompts;
- populations and the call plan;
- treatment definitions;
- gates, thresholds and seeds;
- the ablation rule.
