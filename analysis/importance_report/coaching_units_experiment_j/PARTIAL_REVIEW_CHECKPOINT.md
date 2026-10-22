# Experiment J: partial human review checkpoint (human review stopped at 64/172)

Recorded 2026-10-22. Not a design amendment. `DESIGN.md`, `design.json`, the
amendments, the procedure notes, the stop point 1-4 seals, the blank sheet
(commit `6b637cd`), the key and every automatic artifact are unchanged.
Experiment J's sample size is still 172 absolute-sheet rows; this note does
not redefine it.

## State

- **Assigned:** 172 absolute-sheet rows (40 deaths).
- **Rated by the human rater:** 64 rows, `o001`-`o040` and `o149`-`o172`.
  Every one of them has all four required columns valid (`usefulness_1to5`,
  `actionable_1to5`, `specific_to_this_death_1to5`,
  `supported_by_footage_yes_no`). The rater first reported 63, then confirmed
  that all 64 are genuine ratings.
- **Unrated:** 108 rows, `o041`-`o148`. All four rating columns and `notes`
  are empty. No row is partly rated.
- **Further human rating is paused indefinitely.** This is a deliberate
  decision, not a failure. The 64 ratings are valid human observations and
  are preserved as entered.
- Rows are rated in two runs of the frozen shuffled sheet order (the first 40
  and the last 24). Where the rater stopped was not pre-specified. The 64
  rows cover 33 of the 40 deaths; only 2 deaths have every row rated.

## Integrity

- Row `o013`'s fixed `output_id` cell had been changed to `3` (a stray edit).
  With the rater's approval, only that cell was restored to `o013`. No other
  byte changed.
- After that, the frozen validator
  (`tools/reports/coaching_units_experiment_j_ratings.py validate`) reports
  only the four empty rating columns on each of `o041`-`o148`. The header,
  row ids, row order and every fixed column match the sealed blank sheet. The
  blank sheet from git still matches `stop_point_4.sha256`.
- Partial sheet as committed with this note: `rating_sheet.csv` sha256
  `1dffbe80f1b513b62f293c1f0f5dded96e8913292a95dbc2251cb1a0bf4a2530`.
  That hash is recorded here, not in a `shasum` file, because resuming the
  rating changes the file.
- `seal` was **not** run. It runs once, only on a complete 172-row sheet, so
  it stays available.

## Determination

**Experiment J's pre-specified determination is not made.**

- Gates 2c and 3 need the sealed absolute sheet, aggregated death-first with
  both arms per death. The design has no rule for a partial sheet, and the
  key may not be opened before the absolute sheet is sealed.
- Gates 2d and 2e need the pairwise sheet. It is built only after the
  absolute sheet is sealed, so it does not exist.
- Gates 1, 2a, 2b and 4 are automatic and sit in the sealed, unopened
  automatic summary. Under the frozen outcome table, a failure of one of them
  would by itself mean "production unchanged". Whether that applies is
  unknown: checking it means opening that summary, which is a separate
  decision and has not been made. No automatic or rating result can produce
  a pass while the ratings are incomplete.
- Production is unchanged, as the design requires without a pass.

## Still closed

These stay unopened until the point the frozen design sets (after the
required sheets are sealed): `rating_key.csv`, `automatic_summary.md` /
`.json`, `automatic_checks.json`, `ablations.json`, the relevance labels and
ideal-coaching notes, and `populations.json`.

## What this does not block

Product, detector, pipeline, replay-code, SCLC and coaching development
continue. Experiment J was research only, and nothing in production depends
on its outcome.

## Resuming later

- **Resume the original study:** rate any of `o041`-`o148` in place, then run
  the frozen `validate` → `seal` sequence once all 172 rows are rated. The
  pairwise stage and the post-seal key join follow as designed.
- **Analyze the 64 further:** read them from this commit. Any arm-level
  analysis needs the key, which closes the blind rating of the remaining rows.
- **A smaller targeted follow-up** is a new study with its own design and
  freeze. It is not a substitute for this procedure.
- **Caveat for resuming:** the rater has seen the exploratory diagnostics in
  `PARTIAL_REVIEW_DIAGNOSTICS.md`. Some of them group rows by recommendation
  type, which correlates with arm. Any resumed rating should record that.
