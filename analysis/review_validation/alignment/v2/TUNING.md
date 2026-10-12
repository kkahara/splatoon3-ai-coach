# Review to gameplay alignment v2: tuning evidence

## Status of v1

Alignment v1: gate FAIL due to a pre-registered structural-check defect;
alignment measurements passed; no production freeze or tag
(`../REPORT.md`). v2 proceeds as a pre-registered method with a robust
clock-domain check and fresh held-out data.

## Promoted evidence

The four v1 held-out pairs (22-20-42, 22-31-53, 22-37-47, 22-57-30) were
opened by the v1 gate run, so they can no longer be held out. They are v2
**tuning** evidence, used in place (`../heldout/<id>`, input hashes in
`../heldout/INPUTS.md`). The v1 record (`../PREREGISTRATION.md`,
`../REPORT.md`, `../heldout/gate/`) is not edited. With the five v1 tuning
pairs (`../TUNING.md`), v2 tuning covers 9 pairs.

## The v2 change

Only the structural pair check changed (contract, Structural checks and
Method versions). Coverage and clock domain now use the **consistent HUD
edges**, the same set that fixes `c`:

- `consistent_clock_top` = `k` of the earliest consistent `k -> k-1` edge,
  compared with the highest Review regulation label (tolerance 2.0 s, as in v1);
- coverage = consistent-edge span over the Review regulation range.

`highest_hud_read` and the raw edge span stay as diagnostics only. The `c`
fit, the mapping, `delta = 0`, the association rules and every config value
are unchanged (`alignment` block sha256 `463787c0...`, as frozen in v1).

## v2 run on all 9 pairs

Output: `tuning/gate.{md,json}`, `tuning/alignment_tuning.{md,json}`,
`tuning/pairs/<id>/review_gameplay_alignment.json` (`schema_version` 2.0).

| Pair | v1 pair status | v2 pair status | Consistent top / raw highest | Roster-X used / contains 0 / median |
|---|---|---|---|---|
| 23-43-31 | consistent | consistent | 300 / 300 | 16 / 94% / +0.35 s |
| 23-56-41 | consistent | consistent | 300 / 300 | 11 / 91% / +0.38 s |
| 18-18-00 | consistent | consistent | 300 / 300 | 12 / 58% / +0.64 s |
| 18-25-16 | consistent | consistent | 180 / 180 | 8 / 100% / +0.10 s |
| 20-33-31 | consistent | consistent | 300 / 300 | 5 / 100% / +0.24 s |
| 22-20-42 | consistent | consistent | 300 / 300 | 11 / 91% / +0.06 s |
| 22-31-53 | **inconsistent** (`clock_domain_mismatch`) | **consistent** | **180 / 501** | 7 / 86% / +0.49 s |
| 22-37-47 | consistent | consistent | 300 / 300 | 22 / 77% / +0.43 s |
| 22-57-30 | consistent | consistent | 300 / 300 | 23 / 87% / +0.21 s |

- **22-31-53 passes for the intended reason:** its trusted clock top is 180
  (Turf War), while the three misreads (`6:21`, `8:21`) only raise the
  diagnostic raw highest read to 501.
- **No other change:** for the other eight pairs, pair status, reasons,
  coverage, `c` interval and every roster-X and local DEATH residual interval
  are identical to v1. 22-31-53's residuals are new only because its values
  are now mapped instead of `unknown` / `pair_inconsistent`.
- Pooled over 9 pairs: roster-X 115 used, 85.2% contain 0, median |residual|
  0.33 s; local DEATH (reported only) 11 used, 81.8% contain 0. `delta = 0`
  is within uncertainty in every match with enough roster-X evidence.

## Negative controls (diagnostic only)

| Control (Review x gameplay) | v2 structural result | Roster-X if consistent |
|---|---|---|
| 23-43-31 x 23-56-41 | rejected: `mode_mismatch` | - |
| 18-25-16 x 18-18-00 | rejected: `mode_mismatch`, `clock_domain_mismatch` | - |
| 22-20-42 x 22-31-53 | rejected: `mode_mismatch`, `clock_domain_mismatch` | - |
| 22-31-53 x 22-37-47 | rejected: `mode_mismatch`, `clock_domain_mismatch` | - |
| 22-37-47 x 22-57-30 | **not rejected** (both Splat Zones, 5:00) | 32 used, 12.5% contain 0 |
| 22-57-30 x 22-20-42 | rejected: `mode_mismatch` | - |

Genuinely different clock domains (180 against 300) are still rejected. The
unrejected Splat Zones control (12.5% against 84 to 85% for true pairs) is
evidence for why declared pairing stays part of the contract, not a test the
structural check is expected to pass.

## Unchanged follow-up

The frozen Review lane detector still finds no bottom-row death glyphs when
the bottom team hue is 120 to 136 (all nine recordings agree). Separate
Review-detector assignment; not part of v2.
