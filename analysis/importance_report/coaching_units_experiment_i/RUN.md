# Experiment I run (stop point 3)

Inputs and stop-point-2 tools sealed at `79be6ac`; run tooling at
`2d47e36`. No sealed file, gate, threshold, prompt, eligibility or
validator was changed.

## Pre-call gate

Recorded in `outputs/precall_gate.json` (06:42:54 UTC) and re-evaluated
before the first call in `outputs/recovery_gate_2026-10-20T064312+0000.json`
(06:43:12 UTC). Every check passed both times:

- provider / model: cursor `gpt-5.6-luna`, as registered in the manifest;
- repository: HEAD `2d47e36`, seal `79be6ac` an ancestor, run tooling
  committed unmodified;
- seal: `design.json`, `DESIGN.md`, `dry_run.*`, `inputs/` and the
  stop-point-2 tool and test files byte-identical to `79be6ac`, same file set;
- design hash: manifest `design_sha256` = `ee71a493...` matches `design.json`;
  file sha256 `design.json` `87d12127...`, `DESIGN.md` `e26194dd...`, inputs
  aggregate `3b191597...` (per-file hashes in the gate records);
- derived facts reproduce `design.json`; every input byte-identical to a
  fresh rebuild from current production (prompt freshness);
- production `coach.roster_evidence` is false;
- frozen invariants: system A = production, system M = A + map section;
  arm A prompts = Experiment H arm A; M view = A view + `map_awareness`
  (+ claim on eligible deaths only); the sealed M prompts differ from the
  sealed A prompts only by these additions (same preamble);
- counts: 22 deaths, 14 eligible / 8 not, 88 unique calls;
- amendment 2: 20-33-31 @199.5 has `map_awareness: null`, no experiment
  claim, manifest eligibility false; system M forbids map-check advice for a
  null block;
- output destination empty (first gate); records and log consistent
  (second gate).

Tests and lint before the first call: 92 passed, 1 skipped (an evidence
contract test whose 18-02-24 analysis is not on this machine); ruff clean.

## Calls

- 88 / 88 cells answered on the first attempt (attempt 1 everywhere).
  Provider errors 0, unparseable replies 0, failed attempts 0, no cell
  called more than once. Run 06:43:12 to 06:51:44 UTC, in order repeat,
  then death, then arm (A, M).
- Temperature and seed not honored by the Cursor SDK (`sent: null`), as
  pre-registered.
- `outputs/run_log.jsonl`: one start and one end line per call (176 lines).

### Incident: provider not built on the first `run`

The first `run` was launched from `tools/reports/`. The gate passed and was
written, then building the Cursor provider failed (`ConfigError`: the
settings read `.env` relative to the working directory). The provider was
never constructed and no call was attempted; `outputs/` held only
`precall_gate.json` (no log, no records). `recover`, launched from the repo
root, found no records and no log entries (all 88 cells provably missing,
nothing ambiguous), re-ran the full gate and made the 88 calls. All 88
records and log lines therefore say phase `recover`, attempt 1.

## Automatic checks

`automatic_checks.json`, `automatic_summary.json` / `.md`. Arm-identified;
the decision rule is not applied. Production validator counts and
amendment-1 adjusted counts are kept separate.

## Files

- `outputs/calls/*.json`: raw reply, parsed assessment and validator flags
  per cell, with match, death, eligibility, arm, repeat, provider, model,
  seal and run commits, attempt, system / user prompt sha256 and provider
  input hashes. Arm-identified.
- `rating_sheet.csv`: 88 rows shuffled with seed 20261020. Shows match,
  death time, assessment and recommendations only; no arm, repeat or
  eligibility.
- `rating_key.csv`: output_id to cell. Not opened until the ratings are
  sealed in a commit.

## Rating

Rate `rating_sheet.csv` blind, before reading the call records, the
automatic files or the key. Columns: usefulness, redundancy,
anchor_shows_situation, missing_context (1-5), supported (yes / no),
map_use (correct / incorrect / not_mentioned), notes. Commit the filled
sheet before the key is opened (stop point 4).
