# Experiment H: roster evidence in coaching (pre-registration)

Research only. Frozen before any input is built or any model call is
made. Production code, prompts, catalog and config are unchanged
(`coach.roster_evidence=false` in production). A1/O3 detector tuning
stays closed whatever this experiment shows. **Gates and thresholds are
not adjusted after the outputs are seen**, including results close to a
boundary.

Design computed at `b346c2e`; config sha256
`e0b96fb6ac4d`. Machine-readable: `design.json`.

## Questions

- **Q1 correction effect:** with roster evidence on, does refreshed
  evidence produce appropriately different coaching than pre-refresh
  evidence? B vs C on the treatment deaths.
- **Q2 value:** does exposing roster evidence improve coaching over
  production today (roster off)? A vs C.

## Arms

Each arm is the production pipeline with only the production switch
set: `score_match` + `build_coach_llm_view` + `load_system_prompt`. No
experiment-specific prompt or view manipulation.

| arm | analysis | coach.roster_evidence | system prompt sha256 |
| --- | --- | --- | --- |
| A | refreshed | false | `894fa0d0736c` |
| B | pre_refresh | true | `167241479d89` |
| C | refreshed | true | `167241479d89` |

Production equivalence: every arm's LLM view was compared with the
`s3-coach coach-inputs` output for the same analysis, using the
production config with only `roster_evidence` set:
A 46/46, B 46/46, C 46/46 identical.

Arm A carries no roster values: `roster` is null; the death block's
roster fields (`numbers_state_at_death`, `roster_changed_before_death`,
`seconds_since_roster_change`) are null; the `roster_evidence_disabled`
limit and the `roster` undetermined topic are present. Its
`importance.factor_thresholds` still lists the two roster factors'
configured thresholds (config values, not evidence); that is production
behavior and is left as is.

Model: cursor `gpt-5.6-luna` (temperature not honored),
one death per call, 2 repeats.

## Deaths

46 death units in 12 matches,
classified by how the arm-B view differs from the arm-C view:
at_or_before_death 5, rank_only 3, unchanged 32, window_only 6.

- `at_or_before_death`: an evidence path outside the roster window
  differs (roster before/at death, death roster fields, roster context,
  supporting evidence, limits or claim basis).
- `window_only`: outside `importance`, only the roster window differs.
- `rank_only`: only `importance` differs (selection side effect).
- `unchanged`: B and C views (and prompts) are identical.

### Treatment (11): every death whose roster evidence differs

All `at_or_before_death` and `window_only` deaths, regardless of
production selection. Change type, count change and selection are
covariates.

| match | death | change type | before death B -> C | score A / B / C | selected A / B / C |
| --- | --- | --- | --- | --- | --- |
| 2026-10-11 18-25-16 | `death_episode:85.000` | at_or_before_death | 4v4 -> 3v4 | 0 / 0 / 0 | N / N / N |
| 2026-10-11 20-33-31 | `death_episode:199.500` | at_or_before_death | 3v4 -> 3v4 | 3 / 3 / 3 | Y / Y / Y |
| 2026-10-11 22-20-42 | `death_episode:152.000` | window_only | 3v3 -> 3v3 | 3 / 3 / 3 | Y / Y / Y |
| 2026-10-11 22-37-47 | `death_episode:210.500` | at_or_before_death | none -> 4v3 | 0 / 0 / 0 | N / N / N |
| 2026-10-11 22-37-47 | `death_episode:58.000` | window_only | 4v3 -> 4v3 | 0 / 0 / 0 | N / N / N |
| 2026-10-11 22-57-30 | `death_episode:106.500` | window_only | 3v3 -> 3v3 | 0 / 0 / 0 | N / N / N |
| 2026-10-11 22-57-30 | `death_episode:176.500` | window_only | 3v3 -> 3v3 | 2 / 2 / 2 | N / N / N |
| 2026-10-11 22-57-30 | `death_episode:278.500` | window_only | 3v3 -> 3v3 | 0 / 0 / 0 | N / N / N |
| 2026-10-11 22-57-30 | `death_episode:308.000` | at_or_before_death | 3v4 -> 2v4 | 1 / 1 / 3.5 | N / N / Y |
| 2026-10-11 22-57-30 | `death_episode:49.500` | at_or_before_death | 4v4 -> 3v4 | 0 / 0 / 0 | N / N / N |
| 2026-10-12 20-17-32 | `death_episode:65.500` | window_only | 3v4 -> 3v4 | 3 / 3 / 3 | Y / Y / Y |

### Controls (11): seeded draw from the `unchanged` deaths

Seed 20261017, stratified to the treatment's arm-C
selection mix. B and C prompts are byte-identical, so B-vs-C
differences on controls are model variation (the noise floor).

| match | death | change type | before death B -> C | score A / B / C | selected A / B / C |
| --- | --- | --- | --- | --- | --- |
| 2026-10-11 18-18-00 | `death_episode:197.500` | unchanged | 3v3 -> 3v3 | 2 / 2 / 2 | Y / Y / Y |
| 2026-10-11 18-25-16 | `death_episode:175.500` | unchanged | 3v3 -> 3v3 | 1 / 1 / 1 | Y / Y / Y |
| 2026-10-11 20-33-31 | `death_episode:180.500` | unchanged | 2v4 -> 2v4 | 0 / 2.5 / 2.5 | N / Y / Y |
| 2026-10-11 22-31-53 | `death_episode:208.500` | unchanged | 4v4 -> 4v4 | 3 / 3 / 3 | Y / Y / Y |
| 2026-10-11 20-33-31 | `death_episode:151.000` | unchanged | 4v3 -> 4v3 | 0 / 0 / 0 | N / N / N |
| 2026-10-11 20-33-31 | `death_episode:37.500` | unchanged | 3v3 -> 3v3 | 0 / 0 / 0 | N / N / N |
| 2026-10-11 22-37-47 | `death_episode:301.500` | unchanged | 4v4 -> 4v4 | 0 / 0 / 0 | N / N / N |
| 2026-10-11 22-57-30 | `death_episode:244.000` | unchanged | 4v3 -> 4v3 | 0 / 0 / 0 | N / N / N |
| 2026-10-12 20-17-32 | `death_episode:179.500` | unchanged | 3v4 -> 3v4 | 0 / 0 / 0 | N / N / N |
| 2026-10-12 20-17-32 | `death_episode:221.000` | unchanged | 4v4 -> 4v4 | 0 / 0 / 0 | N / N / N |
| 2026-10-12 20-17-32 | `death_episode:257.500` | unchanged | 4v4 -> 4v4 | 0 / 0 / 0 | N / N / N |

## Selection effect (no model calls)

Deaths whose rank or selection differs between arms. Reported
separately from the coaching text.

| match | death | change type | rank A / B / C | selected A / B / C |
| --- | --- | --- | --- | --- |
| 2026-10-11 18-18-00 | `death_episode:197.500` | unchanged | 1 / 2 / 2 | Y / Y / Y |
| 2026-10-11 18-18-00 | `death_episode:65.500` | unchanged | 2 / 1 / 1 | Y / Y / Y |
| 2026-10-11 20-33-31 | `death_episode:123.000` | unchanged | 4 / 5 / 5 | N / N / N |
| 2026-10-11 20-33-31 | `death_episode:151.000` | unchanged | 5 / 6 / 6 | N / N / N |
| 2026-10-11 20-33-31 | `death_episode:180.500` | unchanged | 6 / 3 / 3 | N / Y / Y |
| 2026-10-11 20-33-31 | `death_episode:199.500` | at_or_before_death | 1 / 2 / 2 | Y / Y / Y |
| 2026-10-11 20-33-31 | `death_episode:37.500` | unchanged | 3 / 4 / 4 | N / N / N |
| 2026-10-11 20-33-31 | `death_episode:68.500` | unchanged | 2 / 1 / 1 | Y / Y / Y |
| 2026-10-11 22-57-30 | `death_episode:119.000` | rank_only | 2 / 2 / 3 | Y / Y / Y |
| 2026-10-11 22-57-30 | `death_episode:142.500` | rank_only | 3 / 3 / 4 | Y / Y / N |
| 2026-10-11 22-57-30 | `death_episode:176.500` | window_only | 4 / 4 / 5 | N / N / N |
| 2026-10-11 22-57-30 | `death_episode:308.000` | at_or_before_death | 5 / 5 / 1 | N / N / Y |
| 2026-10-11 22-57-30 | `death_episode:65.500` | rank_only | 1 / 1 / 2 | Y / Y / Y |
| 2026-10-12 19-59-26 | `death_episode:37.500` | unchanged | 1 / 1 / 1 | N / Y / Y |

Calls: 22 deaths x 3 arms x 2 repeats = 132.

## Measures

Automatic, per output, implemented and frozen in
`tools/reports/coaching_units_experiment_h.py` before any output:

- **Validator:** `claim_validation.validate_assessment` rule hits and
  blocked outputs, each arm validated under its own roster setting.
- **Roster mention** (`mentions_roster`): alive counts, numbers state,
  outnumbered / advantage wording, roster or player count.
- **Count fidelity** (`count_fidelity`): every cited ally-vs-opponent
  pair and every named numbers state (even / advantage / disadvantage)
  is present in that arm's own roster block (before death, at death or
  window). A stale pre-refresh value cited in C is an error.
- **Roster-rule violations** (`roster_violations`, from
  `coach_roster.txt`): a verdict in a roster sentence (should have
  retreated / pushed / played passively ...); fight framing
  (outnumbered fight); teammate-death or trade inference in a roster
  sentence; turning a presence bound into continuous time.
- **Divergence** (`divergence`): 1 - difflib ratio between B and C
  output text, repeat 1 with repeat 1 and 2 with 2, mean per death.

Blind rating: all outputs in one sheet, shuffled with seed
20261018. Columns: usefulness, redundancy,
anchor_shows_situation, missing_context (1-5), supported (yes/no),
**roster_use** (correct / incorrect / not_mentioned), notes. Ratings
are sealed in a commit before the key is opened.

## Decision rule

1. **Safety gate (arm C).** 0 roster-rule violations; 0 count-fidelity
   errors; blocked outputs no more than arm A; validator hits per call
   no more than arm A + 0.25. Failure: keep roster
   evidence off, record the failures.
2. **Q1 correction effect ("appropriately different").**
   - Among C outputs on the `at_or_before_death` deaths that mention
     roster, at least 90% pass count fidelity against C's (refreshed)
     evidence. **If none of the C outputs on those deaths (5 deaths
     x 2 repeats = 10) mentions roster, Q1 fails.** Roster-mention
     frequency for B and C is reported explicitly and considered when
     interpreting Q1; no minimum mention rate beyond that.
   - Mean B-vs-C divergence on treatment deaths exceeds the mean on
     controls.
   - On treatment deaths, C is rated `roster_use=correct` at least as
     often as B.
3. **Q2 value.** Over all treatment and control deaths, mean usefulness
   C - A exceeds the noise floor: the mean over controls of
   |mean usefulness B - mean usefulness C| (per death, over repeats).
   C's supported rate is not lower than A's. Missing context is
   reported alongside.
4. **Outcomes.**
   - Passes 1, 2 and 3: recommend enabling `coach.roster_evidence`.
   - Passes 1 and 2, fails 3: evidence handled correctly but no
     demonstrated value; keep off.
   - Fails 1 or 2: keep off; investigate the coach's roster handling.
     The detector is not reopened.
   Directional only (one rater, small n). Any production change remains
   a separate decision.
5. **Covariates (descriptive, not a gate).** Whether effects track
   change type, the size of the count change, or production selection.

## Stop points

1. This design (tool, tests, `DESIGN.md`, `design.json`). Review.
2. Built inputs and dry run, sealed. Go-ahead before any call.
3. Outputs, automatic summary, blind sheet.
4. Sealed ratings, then key and `analyze` report.

## Pre-output amendments

**Amendment 1 (2026-10-15, after 78f0f6f (stop point 1)).**

After Stop Point 1 and before inputs were built or any model outputs existed, inspection of the frozen automatic checker found that generic 'advantage' matching would incorrectly classify ordinary constructions such as 'take advantage of your special' as numerical-roster claims. The checker was narrowed to exclude non-roster 'take advantage of ...' usage (take / takes / taking / took / taken, optionally 'full' or 'more') from both the numbers-state match used by count fidelity and the roster-mention match, so such an output is neither a roster claim nor a roster mention. No experimental output had been generated or observed when this change was made. All decision thresholds and other checks remain unchanged.
