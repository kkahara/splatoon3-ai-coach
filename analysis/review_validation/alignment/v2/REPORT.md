# Review to gameplay alignment v2: held-out gate report

**Verdict: FAIL.** Recorded as run; not rescued. No tag.

## Provenance

| Item | Value |
|---|---|
| v2 method and pre-registration | `70c2eeb`, `a66a042` (`PREREGISTRATION.md`) |
| Held-out input hashes | `79d8fbe` (`heldout/INPUTS.md`), committed before processing |
| Declared deviation | 5 pairs instead of 4, declared before processing; no exclusions (19-59-26 knockout, kept) |
| Gate run at | `79d8fbe`; no diff since `70c2eeb` in `src/`, `configs/`, gate tool |
| `configs/default.yaml` sha256 | `17cf9fa6218686a2f2ba425cc2b80c23309cd71acbd71afb884b828263d19621` |
| `alignment` block sha256 | `463787c0d38f1698afda752bd83235b96c5de228182132c481de2e3343efad3f` |
| `review` block sha256 | `98c2bf537caee518ce609812eea509e53fea1e7f4239a72120631e4230325e24` |
| Gate output | `heldout/gate/gate.{md,json}`, `heldout/gate/alignment_tuning.{md,json}` |

## Criteria

| # | Criterion | Result |
|---|---|---|
| 1 | Every pair consistent and calibrated | pass (5 / 5) |
| 2 | Pooled roster-X contains-0 share at least 0.75 | **FAIL: 0.655 (36 / 55)** |
| 3 | Pooled roster-X median absolute residual at most 1.0 s | pass: 0.54 s |
| 4 | Every unknown value carries a declared reason | pass |
| - | At least 20 roster-X residuals used | met: 55 |

## Per pair

| Pair | Mode | Top / bottom hue | Ally row | Glyph rows | Edges consistent | `c` width | Raw highest read | Roster-X used / contains 0 / median residual | Direction |
|---|---|---|---|---|---|---|---|---|---|
| 17-42-59 | splat_zones | 38 / 152 | top | top 14, bottom 11 | 172/172 | 0.5 s | 486 | 10 / 30% / −0.85 s | negative |
| 19-47-46 | splat_zones | 10 / 119 | bottom | top 8 only | 117/119 | 0.5 s | 300 | 3 / 0% / −3.15 s | negative |
| 19-59-26 | splat_zones | 9 / 119 | bottom | top 6 only | 77/77 | 0.5 s | 300 | 6 / 33% / −0.75 s | negative |
| 20-17-32 | splat_zones | 38 / 152 | top | top 27, bottom 20 | 254/255 | 0.5 s | 491 | 19 / 79% / +0.43 s | within uncertainty |
| 20-25-22 | tower_control | 25 / 139 | bottom | top 25, bottom 22 | 284/284 | 0.5 s | 300 | 17 / 94% / +0.45 s | within uncertainty |

Pooled: 20-17-32 and 20-25-22 give 31 of 36 (86%); the other three give 5 of
19 (26%), with negative median residuals of 0.75 to 3.15 s.

## What the v2 change did

The v2 structural change held on fresh data. 17-42-59 and 20-17-32 have raw
highest HUD reads of 486 and 491 s (timer misreads) against a 300 s clock
domain. v1's raw-maximum check would have rejected both as
`clock_domain_mismatch`; v2 took the domain from consistent edges (top 300)
and passed them. Every pair calibrated with `c` width 0.5 s.

## The failure

Criterion 2 failed on residual direction, not structure. In three of five
pairs the Review death glyphs map earlier in gameplay video than the
associated roster-X onsets, by a median 0.75 to 3.15 s. Their residual
intervals (median half-width about 0.6 s) mostly exclude 0. The other two
pairs behave like the v1 and v2 tuning pairs (median about +0.4 s, 79–94%
contain 0).

Observed, without a causal claim:

- Association is per team row (ally roster-X to the ally glyph row, opponent
  to opponent), so this is not cross-team mixing.
- 19-47-46 and 19-59-26 have bottom hue 119 (ally row). As in the bottom-row
  glyph diagnosis (`../../row_hue_diagnosis/REPORT.md`), that row has no
  glyphs, so only opponent onsets can be used there (3 and 6 used; 19 and 12
  unassociated).
- 17-42-59 has glyphs on both rows and the same colours as 20-17-32, yet is
  negative. So the hue issue alone does not explain the direction.
- `delta = 0` test: no match had both references usable, so no non-zero
  delta is supported (`decision: delta_stays_0`). The pre-registration fixes
  `delta = 0`; it is not re-estimated.

Diagnosing why these three pairs shift is a separate, read-only question.
Any method change that follows would be v3 with its own freeze and fresh
held-out pairs; it is not applied to this run.

## Reported, not pass criteria

- **Local DEATH:** 2 used, 0 contain 0, median absolute residual 3.69 s;
  5 unassociated, 7 ambiguous.
- **Negative controls** (cyclic Review i with gameplay i+1):

| Control | Structural result | Roster-X if consistent |
|---|---|---|
| 17-42-59 x 19-47-46 | **not rejected** (both Splat Zones, 5:00) | 9 used, 0% contain 0, median 2.32 s |
| 19-47-46 x 19-59-26 | **not rejected** | 4 used, 25% contain 0, median 3.64 s |
| 19-59-26 x 20-17-32 | **not rejected** | 3 used, 0% contain 0, median 2.05 s |
| 20-17-32 x 20-25-22 | rejected: `mode_mismatch` | - |
| 20-25-22 x 17-42-59 | rejected: `mode_mismatch`, `overtime_mismatch` | - |

  As in v1, same-mode unrelated pairs pass structural checks, which is why
  the operator's pair declaration stays part of the contract. Their residuals
  (0–25% contain 0) are worse than the failing true pairs (26%) and far from
  the passing ones (86%), but death evidence is not part of `pair_status`.
