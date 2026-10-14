# Assessment layout fix: result

Run 2026-10-14: `cursor` / `gpt-5.6-luna`, 26 calls (Experiment E `current`
inputs, byte-identical user prompts; only the system prompt changed). Both
sides re-validated with the current validator (`compare.json`).

| Measure | Before (E `current`) | After | Criterion | Result |
| --- | --- | --- | --- | --- |
| Layout violations (R14) | 7/26 | 0/26 | <= 1/26 | pass |
| Outputs with a recommendation | 18/26 | 18/26 | >= 16/26 | pass |
| Blocked outputs | 0 | 0 | 0 | pass |
| Outputs with any other badge | 1/26 (R9) | 0/26 | <= 3/26 | pass |

Mean selected claims 2.58 -> 2.54. A manual read of the 18 outputs with a
recommendation found no advice in `assessment`, including statement-form
advice R14 cannot catch: each assessment states the timing facts and the
back-to-back principle; the recommendation appears only in `recommendations`.

Decision: the fix is accepted. The field-layout prompt block and R14 stay in
production code.
