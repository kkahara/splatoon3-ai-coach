# Experiment E: recommendation-catalog coverage

Research only. Production catalog, prompts and config were not changed; the
two experiment claims existed only inside the runner process.

## Design

- 13 deaths (Experiment D `individual` units), one death per unit.
- Arms: `current` (production catalog after the clock-wording fix, 0acb2ed)
  vs `expanded` (plus `process_after_own_splat` -> `rec_after_splat_check`
  and `process_final30_respawn_cost` -> `rec_final30_weigh_fight`).
- Cursor `gpt-5.6-luna`, temperature not honored (documented limitation);
  each input called twice: 52 calls, 0 call/parse errors, 0 blocked.
- Blind sheet (seed 20261014), ratings sealed in e82041e before the key was
  opened. Scale directions as in Experiment D (see `rating_declarations.yaml`).

## Groups (from `dry_run.json` coverage)

- A: 4 deaths with no recommendation template before (now one).
- B: 7 deaths with `rec_redeath_reset` that gained a second option.
- C: 2 deaths unchanged.

## Results (means; supported = rater's "enough to support coaching")

| group | arm | n | usefulness | redundancy | missing ctx | supported | with rec |
| --- | --- | --- | --- | --- | --- | --- | --- |
| all | current | 26 | 2.38 | 2.23 | 4.31 | 18/26 | 18/26 |
| all | expanded | 26 | 3.77 | 2.23 | 4.77 | 6/26 | 26/26 |
| A | current | 8 | 1.00 | 1.00 | 5.00 | 0/8 | 0/8 |
| A | expanded | 8 | 4.00 | 2.25 | 5.00 | 0/8 | 8/8 |
| B | current | 14 | 3.00 | 2.57 | 4.00 | 14/14 | 14/14 |
| B | expanded | 14 | 3.86 | 2.14 | 4.86 | 2/14 | 14/14 |
| C | current | 4 | 3.00 | 3.50 | 4.00 | 4/4 | 4/4 |
| C | expanded | 4 | 3.00 | 2.50 | 4.00 | 4/4 | 4/4 |

Validator: R12 ("video seconds") 0 hits in 52 outputs; R7 11, R9 1 (badges).

## Findings

1. Ratings track the recommendation sentence, not the arm or the model run.
   No recommendation -> usefulness 1, unsupported. `rec_redeath_reset` alone ->
   3, supported. Any output containing a new template -> 4, unsupported.
   Repeats of the same input that chose the same sentences got identical
   ratings; the assessment prose did not move ratings.
2. The new templates raise usefulness (A: 1.00 -> 4.00; B: 3.00 -> 3.86) but
   the rater judges them **not supported by the evidence shown** (supported
   falls 18/26 -> 6/26; missing context rises 4.31 -> 4.77). Rater reasons:
   - after-own-splat: "Opponent proximity is not established by the shown
     evidence. Splat timing alone does not show ink level or surrounding
     threats." (The frame's "the next opponent may already be close" is read
     as a claim the evidence does not make.)
   - final 30 s: "Endgame timing is relevant, but fight value depends on
     objective/position context."
3. In group B the model sometimes replaced the supported reset recommendation
   with the final-30 one instead of adding it (4/14 outputs carried only the
   final-30 sentence); every B output containing it lost "supported".
4. Gap closed: every expanded output had a recommendation (26/26 vs 18/26).

## Interpretation

Catalog coverage is the lever for usefulness, as Experiment D suggested, but
these two templates trade support for usefulness: they point at context
(threat proximity, ink level, objective/position) the unit does not carry.
A template is only worth adding when its grounding claim carries the evidence
the advice depends on. Effect sizes come from 4 + 7 deaths and one rater;
directional only.

## Not changed

Production `claim_basis`, prompts, validator and config are untouched; the
experiment templates are not promoted.
