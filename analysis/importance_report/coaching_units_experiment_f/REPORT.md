# Experiment F: minimal grounding correction

Research only; production catalog, prompts and config unchanged.

## Design (preregistered)

- Same 13 deaths, eligibility, evidence, system prompt, model (Cursor
  `gpt-5.6-luna`, temperature not honored) and two repeats as Experiment E.
- One arm, `reworded`: only the two experiment claims' frame and
  recommendation wording changed (verified field by field in b7e86d8).
  - after own splat: "Your splat and this death were {time_since_previous_splat}s
    apart." / "After you get a splat, slow down for a moment before pushing on."
  - final 30 s: "This death happened with {display} left, inside the last 30
    seconds." / "In the last 30 seconds, keep in mind that a respawn uses up
    part of the time left."
- 26 calls: 0 call/parse errors, 0 blocked. Blind sheet (seed 20261015) mixed
  them with E's 26 `expanded` outputs, re-rated in the same session; ratings
  sealed in 71c7065 before the key was opened.

## Results (same-session ratings)

| group | arm | n | usefulness | redundancy | anchor | missing ctx | supported |
| --- | --- | --- | --- | --- | --- | --- | --- |
| all | E expanded | 26 | 2.77 | 2.42 | 2.73 | 3.54 | 19/26 |
| all | F reworded | 26 | 2.85 | 2.65 | 2.85 | 3.35 | 20/26 |
| A (4 deaths, new rec only) | E | 8 | 2.50 | 2.25 | 2.62 | 3.88 | 5/8 |
| A | F | 8 | 3.00 | 3.12 | 3.25 | 3.12 | 6/8 |
| B (7 deaths, gained option) | E | 14 | 2.86 | 2.86 | 2.71 | 3.50 | 10/14 |
| B | F | 14 | 2.79 | 2.71 | 2.64 | 3.43 | 11/14 |
| C (2 deaths, unchanged) | E | 4 | 3.00 | 1.25 | 3.00 | 3.00 | 4/4 |
| C | F | 4 | 2.75 | 1.50 | 2.75 | 3.50 | 3/4 |

## Findings

1. **Session drift is larger than the arm effect.** The same 26 E outputs
   were rated usefulness 3.77 / supported 6/26 in the E session and 2.77 /
   19/26 here; only 6/26 kept the same usefulness. E's "useful but
   unsupported" result therefore does not replicate on re-rating, and only
   within-session comparisons are trustworthy.
2. **Within session, F is at least as good as E, with the gain where the
   new templates act alone (group A):** usefulness 2.50 -> 3.00, missing
   context 3.88 -> 3.12, anchor 2.62 -> 3.25, supported 5/8 -> 6/8. Cost:
   redundancy 2.25 -> 3.12, driven by the after-splat wording (two outputs
   rated 5, "Very redundant"). Groups B and C show no meaningful difference.
3. **Rater's notes (qualitative):**
   - E's "the next opponent may already be close" was flagged three times as
     needing a fact; the F frame drew no such note.
   - "Slow down for a moment" reads as advice rather than observation and as
     redundant with the frame.
   - The F final-30 recommendation is "O.K. to say", and one output was
     praised as factual and balanced. But the rater questions whether a
     final-30 death matters at all without score / players-left context, and
     asks that it not be repeated "like a broken record".
   - Suggested precise claim: "the player did not return to active gameplay
     before regulation ended", which is derivable arithmetic
     (`death_to_active_again` vs `seconds_remaining`) on existing evidence.
4. Rating noise within session is large: identical recommendation sets on the
   same death sometimes differ by 1-2 points, so per-group differences of
   about 0.5 on n = 8 are directional only.
5. Validator: F outputs had more R7 badges (11 vs 4), all `value_mismatch` on
   correct-looking cited clock/duration values (format of `claims[]`
   citations), plus one R1 and one R4 hit on limitation sentences ("does not
   identify what caused the death / whether a special was used"). Neither
   comes from the reworded templates; nothing was blocked.

   Follow-up (R7 diagnosis): all 20 E/F `value_mismatch` hits were false
   positives from the value parser (`=` inside `[label=death]`, `"14.5s"`
   string values, a trailing unit word, trailing remarks). After the fix,
   offline re-validation of every saved E/F record leaves only E's 2 real
   `unknown_claim` hits (an invented `principle_final30_respawn_cost`).
   Saved call records are not rewritten.

## Interpretation

Removing the unsupported inference did not cost usefulness and helped where
the new templates are the only recommendation. The final-30 claim's
remaining issue is relevance, not grounding: the rater wants context (score,
players left) or a sharper factual claim before emphasising it. The
after-splat recommendation needs less redundant wording.

Directional only: one rater, 4 + 7 affected deaths, and between-session
drift larger than the effect.
