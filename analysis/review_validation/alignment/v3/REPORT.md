# Review to gameplay alignment v3: held-out gate report

**Verdict: FAIL.** Both the unchanged alignment gate and the separate
team-side gate fail. Recorded as run; not rescued. No tag. Stage 3 not begun.

## Provenance

| Item | Value |
|---|---|
| Preregistration | `7ad1a15` (`PREREGISTRATION.md`), Amendment 1 `92d5a41` |
| Frozen implementation | `5e781569e3aa37f917a8edbae4d45d4f37332c27` |
| Sealed input manifest | `INPUT_MANIFEST.json` at `82f4c7e`, sha256 `4ed58210fad0c98f3ca7b1125b67cee17561c3d4834c61cbc0483a69384bcdf3` (unmodified) |
| Processing | git `82f4c7e`; pipeline `3.0.0`; vision config `44854d18…906be`; `player_count@0.2.0` |
| `alignment` / `review` block sha256 | `463787c0…efad3f` / `98c2bf53…325e24` (unchanged since v1/v2) |
| Derived provenance | `heldout/RESULTS_MANIFEST.json` |
| Gate output | `heldout/gate/gate.{md,json}`, `heldout/gate/alignment_tuning.{md,json}` |

Pre-opening checks passed: manifest byte-identical to the sealed commit, all
20 input hashes matched, frozen identities exact, no `src/`, `configs/` or
gate-tool change. All 20 processing steps exited 0; the gate ran once.

## Frozen alignment gate (criteria unchanged verbatim)

| # | Criterion | Result |
|---|---|---|
| 1 | Every pair consistent and calibrated | **FAIL**: pair-009 `inconsistent` (`clock_domain_mismatch`), `fit_status = unknown` (`inconsistent_gameplay_clock`) |
| 2 | Pooled roster-X contains-0 share at least 0.75 | **FAIL**: 0.7313 (98 / 134) |
| 3 | Pooled roster-X median absolute residual at most 1.0 s | pass: 0.41 s |
| 4 | Every unknown value carries a declared reason | pass |
| - | At least 20 roster-X residuals used | met: 134 |

## Separate team-side gate

Ground truth is the sealed operator declaration; predicted side is production
`team_identity.ally_row`.

| Measure | Value | Threshold | Result |
|---|---|---|---|
| Known-side accuracy | 7 / 8 = 0.875 | exactly 1.00 | **FAIL** (pair-010) |
| Known-side coverage | 8 / 10 = 0.80 | at least 7/8 = 0.875 | **FAIL** (pair-003, pair-004 unknown) |
| Declared top / bottom | 4 / 6 | at least 3 each | met |

| Stratum | Declared | Known | Correct | Coverage |
|---|---|---|---|---|
| top | 4 | 3 | 3 | 3/4 |
| bottom | 6 | 5 | 4 | 5/6 |

## Per pair

| Pair | Mode (recorded) | Declared | Predicted | Side provenance | Pair / fit | Roster-X used / contains 0 / median abs |
|---|---|---|---|---|---|---|
| pair-001 | Turf War | top | top | roster-gated | consistent / calibrated | 14 / 0.79 / 0.21 s |
| pair-002 | Clam Blitz | bottom | bottom | roster-gated | consistent / calibrated | 14 / 0.93 / 0.34 s |
| pair-003 | Rainmaker | bottom | unknown | no calibration (`Ready?` not detected) | consistent / calibrated | 29 / 0.66 / 0.48 s |
| pair-004 | Tower Control | top | unknown | no calibration (`Ready?` not detected; stage/mode unresolved) | consistent / calibrated | 14 / 0.50 / 1.02 s |
| pair-005 | Tower Control | top | top | roster-gated | consistent / calibrated | 15 / 0.80 / 0.24 s |
| pair-006 | Rainmaker | bottom | bottom | roster-gated | consistent / calibrated | 18 / 0.94 / 0.35 s |
| pair-007 | Splat Zone | bottom | bottom | roster-gated | consistent / calibrated | 7 / 0.86 / 0.29 s |
| pair-008 | Splat Zone | bottom | bottom | roster-gated | consistent / calibrated | 9 / 1.00 / 0.21 s |
| pair-009 | Rainmaker | top | top | roster-gated | **inconsistent / unknown** | 0 (values unknown: `pair_inconsistent`) |
| pair-010 | Clam Blitz | bottom | **top** | roster-gated | consistent / calibrated | 14 / 0.29 / 1.74 s |

## Reported, not pass criteria

- **Local DEATH:** 6 used, 0.667 contain 0, median absolute residual 0.21 s;
  14 unassociated, 21 ambiguous, 10 without an ally row.
- **Negative controls** (cyclic Review i with gameplay i+1): 7 of 10
  structurally rejected. Not rejected: pair-003 x pair-004 (32 used, 0.19
  contain 0), pair-004 x pair-005 (15 used, 0.13), pair-007 x pair-008
  (6 used, 0.17). pair-004's gameplay analysis has no battle mode, so the
  mode check could not reject its controls.
- **Raw highest HUD read** above regulation in pair-001 (480), pair-005 (482)
  and pair-010 (498); the v2 consistent-edge check did not trip on them.

## Unexpected observations (not investigated)

- **pair-010 wrong known side.** Roster-gated calibration latched at 4.0,
  4.5 and 9.0 s (ally hue 100, opponent hue 25); the Review rows were hue 85
  (top) and 168 (bottom). Its roster-X residuals are shifted (median +1.46 s,
  0.29 contain 0).
- **pair-009 gameplay clock.** 31% of HUD edges inconsistent; consistent
  clock top 220 s against a 300 s Review label; coverage 0.73. This pair
  carries the retained acquisition anomaly (code spelling); the anomaly
  record is unchanged and the pair was not excluded.
- **pair-003, pair-004 fail-closed.** No `Ready?` detection, so calibration
  and map ink were dropped and side stayed `unknown`, as designed.

Research-only top-down recordings were not used.
