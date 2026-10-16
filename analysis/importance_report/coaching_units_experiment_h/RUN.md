# Experiment H run (stop point 3)

Inputs sealed at `9c03040`; run tooling at `1af348a`. Before the first
call, `run` verified the sealed inputs: `design.json` and `inputs/`
byte-identical to `9c03040` (same file set), manifest design hash,
every prompt byte-identical to a fresh production rebuild, and the arm
invariants (A has no roster block, control B = C, treatment B != C).

- Provider / model: cursor `gpt-5.6-luna` (temperature not honored, as
  pre-registered).
- Run: 2026-10-16 02:43:14 to 02:54:45 UTC, in order repeat, then
  death, then arm (A, B, C).
- 132 / 132 cells answered on the first attempt. Provider errors 0,
  timeouts 0, retries 0, unparseable replies 0.
- Coverage of the call records and of the rating key: every expected
  (death, arm, repeat) cell exactly once; none missing, duplicated or
  unexpected.
- `outputs/run_log.jsonl`: one line per attempt (132 lines).

## Recovery procedure (not invoked)

`recover --model gpt-5.6-luna --confirm` re-verifies the seal and
re-calls only cells whose record is missing or holds a provider error.
The failed record is moved to `outputs/failed_attempts/` with its
attempt number and the new record carries the next attempt number.
A cell with a model reply, including an unparseable one, is a result
and is never re-called. Successful cells are never regenerated and the
whole run is never restarted.

## Files

- `outputs/calls/*.json`: raw reply, parsed assessment and validator
  flags per cell, with match, death, set, change type, arm, repeat,
  provider, model, attempt and input hash. Arm-identified.
- `automatic_checks.json`, `automatic_summary.json` / `.md`: the frozen
  checks (validator against each arm's own view; roster mention, count
  fidelity, roster-rule violations on assessment + recommendations;
  B-vs-C divergence). Arm-identified. The decision rule is not applied.
- `rating_sheet.csv`: 132 rows shuffled with seed 20261018. Shows
  match, death time, assessment and recommendations only; no arm,
  repeat, set or change type.
- `rating_key.csv`: output_id to cell. Not opened for analysis until the
  ratings are sealed in a commit.

## Rating

Rate `rating_sheet.csv` blind, before reading the call records, the
automatic files or the key. Columns: usefulness, redundancy,
anchor_shows_situation, missing_context (1-5), supported (yes / no),
roster_use (correct / incorrect / not_mentioned), notes. Commit the
filled sheet before the key is opened (stop point 4).
