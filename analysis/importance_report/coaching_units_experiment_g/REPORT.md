# Experiment G: sharpened final-30 facts and next-move recommendation

Research only; production catalog, prompts and config unchanged.

## Design (approved before the run)

- Built on Experiment F; same 13 deaths, triggers, evidence, system prompt,
  model (Cursor `gpt-5.6-luna`, temperature not honored) and two repeats.
- Final-30: F's single process claim replaced by three mutually exclusive
  computed facts (6 deaths "back in play with about {back_with}s on the
  clock", 1 death "not back in play before the match clock reached 0:00",
  2 deaths "no return to play was observed"); recommendation unchanged.
- After own splat: F's frame kept; recommendation "After a splat, decide where
  you are going next before you move up."
- 26 calls: 0 errors, 0 blocked. Blind sheet (seed 20261016) mixed with F's
  26 `reworded` outputs; ratings sealed in 1e9cc55 before the key was opened.

## Results (same-session ratings)

| deaths | arm | n | usefulness | redundancy | anchor | missing ctx | supported |
| --- | --- | --- | --- | --- | --- | --- | --- |
| all 13 | F | 26 | 2.69 | 2.81 | 2.77 | 3.69 | 21/26 |
| all 13 | G | 26 | 2.85 | 2.46 | 2.81 | 3.81 | 22/26 |
| final-30 (9) | F | 18 | 2.72 | 2.94 | 2.78 | 3.61 | 15/18 |
| final-30 (9) | G | 18 | 3.00 | 2.39 | 2.94 | 3.61 | 16/18 |
| of which back in play (6) | F | 12 | 2.67 | 3.00 | 2.75 | 3.75 | 9/12 |
| of which back in play (6) | G | 12 | 2.92 | 2.58 | 2.92 | 3.42 | 11/12 |
| of which not back / no return (3) | F | 6 | 2.83 | 2.83 | 2.83 | 3.33 | 6/6 |
| of which not back / no return (3) | G | 6 | 3.17 | 2.00 | 3.00 | 4.00 | 5/6 |
| after own splat (3) | F | 6 | 2.50 | 3.17 | 2.50 | 4.17 | 4/6 |
| after own splat (3) | G | 6 | 2.50 | 3.00 | 2.33 | 4.33 | 3/6 |
| neither (2) | F | 4 | 2.75 | 1.75 | 3.00 | 3.50 | 4/4 |
| neither (2) | G | 4 | 2.75 | 2.00 | 3.00 | 3.75 | 4/4 |

Session drift: F's same 26 outputs averaged usefulness 2.85 (20/26
supported) in the F session and 2.69 (21/26) here; 10/26 kept the same
usefulness score. Smaller than the E-to-F drift, still comparable to the
effects below.

## Findings

1. **Exact final-30 facts help.** Usefulness 2.72 -> 3.00 and redundancy
   2.94 -> 2.39 on the 9 final-30 deaths, with the gain concentrated where the
   rater could see a consequence. Notes: "the player back in play with about
   7.5 seconds on the clock" makes the observation "more convincing"; it puts
   the death "in context of how much time remaining in the entire game"; for a
   no-return death "this is what I was talking about ... factual and
   potentially can give more context".
2. **Relevance is still open.** The rater repeatedly says significance needs
   more evidence ("if useful, then I don't know the answer. We need more
   evidences"; "how significant ... that last 18 seconds"). Grounding is no
   longer the complaint; relevance is.
3. **The after-splat claim does not work on current evidence.** The new
   recommendation "doesn't tell very much" and reads as redundant. Its frame
   duplicates the level-A fact already in claim_basis ("You got a splat 2.5
   seconds before you were splatted" / "Your splat and this death were 2.5
   seconds apart. Those two sentences essentially mean the same thing").
   Three wordings (E, F, G) have now failed to make it useful.
4. **Recommendations leak into the assessment** (noted on 3 outputs across
   both arms): a presentation issue in the model output, not a catalog one.

## Interpretation

Following the agreed sequence (grounded template -> check relevance -> only
then score/roster gating): the final-30 concept is now grounded and preferred
in its computed form, and its remaining gap is relevance, which needs match
context the unit does not carry. The after-splat process claim should not be
promoted. Directional only: one rater, 9 final-30 and 3 after-splat deaths.
