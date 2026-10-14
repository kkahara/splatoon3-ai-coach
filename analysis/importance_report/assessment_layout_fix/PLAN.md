# Assessment layout fix: preregistered check

## Defect

The public review page shows `assessment`, then `recommendations` below it.
Under the `claim_basis` prompt the model often copies the recommendation into
`assessment` too, so the player reads the advice twice. Measured with R14 over
saved experiment outputs: Experiment D 10/30, E `current` (production catalog)
7/26, E `expanded` 4/26, F 12/26, G 15/26. The older production outputs in the
claim corpus (legacy prompt) have 0/23. The "3 of 52" noted during Experiment G
undercounted it.

Contract: `assessment` (and `claims[].sentence`) = what the evidence says about
the moment; `recommendations` = what the player could consider doing, and the
only place for advice.

## Change

- `coach_system.txt`: a "Field layout" block stating the contract; output-format
  descriptions for `assessment`, `claims[].sentence` and `recommendations`.
- R14 (`claim_integrity.assessment_layout_hits`, badge): an `assessment` or
  `claims` sentence that contains a `recommendations` item
  (`recommendation_repeated`) or gives advice (`advice_in_assessment`:
  should / try to / make sure / remember to / need to / must / consider, or an
  imperative at the start, optionally after one short lead-in clause). Advice
  in `assessment` also escaped R10 grounding, so it counts like R12.
- Nothing rewrites model text.

## Check

Re-run Experiment E's `current` arm: same 13 deaths, production catalog, user
prompts byte-identical to E (verified at `build`), `cursor` / `gpt-5.6-luna`,
2 repeats, 26 calls. Only the system prompt differs. Both sides re-validated
with the current validator (`compare`).

Before (E `current`, current validator): 7/26 layout violations (all
`recommendation_repeated`), 18/26 with a recommendation, 0 blocked, 1 output
with another badge (R9).

Accept the fix only if all hold:

1. Layout violations <= 1/26.
2. Outputs with a recommendation >= 16/26 (fixing the layout by dropping
   advice is not a fix).
3. Blocked outputs = 0.
4. Outputs with any other badge <= 3/26.

No blind rating: the change is a field-layout contract checked
deterministically. Temperature is not honored by the provider (known).
