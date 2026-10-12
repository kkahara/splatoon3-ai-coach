# Review to gameplay alignment: held-out gate report

**Verdict: FAIL.** Recorded as run; not rescued. No tag.

## Provenance

| Item | Value |
|---|---|
| Freeze and pre-registration | `a1f447c` (`PREREGISTRATION.md`) |
| Held-out input hashes | `d1c986b` (`heldout/INPUTS.md`), committed before processing |
| Gate run at | `d1c986b24960b32d462cae4be7c70b337f6a4116`; frozen config, contract code and tool unchanged |
| `alignment` block sha256 | `463787c0d38f1698afda752bd83235b96c5de228182132c481de2e3343efad3f` |
| `review` block sha256 | `98c2bf537caee518ce609812eea509e53fea1e7f4239a72120631e4230325e24` |
| Gate output | `heldout/gate/gate.{md,json}`, `heldout/gate/alignment_tuning.{md,json}` |

## Criteria

| # | Criterion | Result |
|---|---|---|
| 1 | Every pair consistent and calibrated | **FAIL**: 22-31-53 `clock_domain_mismatch` |
| 2 | Pooled roster-X contains-0 share at least 0.75 | pass: 0.839 (47 / 56) |
| 3 | Pooled roster-X median absolute residual at most 1.0 s | pass: 0.31 s |
| 4 | Every unknown value carries a declared reason | pass |
| - | At least 20 roster-X residuals used | met: 56 |

## Per pair

| Pair | Mode | End | Pair status | Edges consistent | `c` width | Roster-X used / contains 0 / median |
|---|---|---|---|---|---|---|
| 22-20-42 | tower_control | 0:01 then unread (overtime unknown) | consistent | 284/284 | 0.5 s | 11 / 91% / +0.06 s |
| 22-31-53 | turf_war | 0:01 then unread | **inconsistent** | 158/158 | 0.5 s | 0 (values unknown: `pair_inconsistent`) |
| 22-37-47 | splat_zones | knockout (lowest read 18) | consistent | 270/270 | 0.5 s | 22 / 77% / +0.43 s |
| 22-57-30 | splat_zones | 0:01 then unread (overtime unknown) | consistent | 257/257 | 0.5 s | 23 / 87% / +0.21 s |

## Cause of the failure

The clock-domain check compares the highest Review regulation label with the
**single highest raw HUD read**. In 22-31-53 (Turf War, regulation 3:00) three
low-confidence timer misreads mid-match (`6:21` and `8:21` at video 149.5 to
160.0 s, confidence 0.60 to 0.63, above the 0.50 usable floor) made the
highest read 501 s against a Review label of 180 s. Both sides otherwise agree:
mode `turf_war`, edges spanning 1 to 180 s, coverage 99.4%, and the edge-based
calibration ignored the misreads (158 of 158 edges consistent).

This is a method weakness in the structural check, not an alignment error. An
earlier smoke run showed a stray 301 s read within tolerance; the check was not
hardened then. A revised check (for example the highest remaining value among
consistent HUD edges, or the frozen opening read) is a new method version and
needs a fresh freeze and new held-out pairs; it is not applied to this run.

## Reported, not pass criteria

- **Local DEATH:** 6 used, 4 contain 0 (67%), median absolute residual 0.35 s;
  8 unassociated, 6 ambiguous.
- **Death glyphs per row:** 22-20-42 top only (bottom hue 120), 22-31-53 top
  only (bottom hue 136), 22-37-47 both (bottom hue 167), 22-57-30 both
  (bottom hue 2). Across all nine tuning and held-out recordings, every
  bottom team hue from 120 to 136 gave no bottom-row glyphs and every other
  hue gave both rows: the Review recall follow-up from `TUNING.md` stands.
- **Negative controls** (cyclic Review i with gameplay i+1):

| Control | Structural result | Roster-X if consistent |
|---|---|---|
| 22-20-42 x 22-31-53 | rejected: `mode_mismatch`, `clock_domain_mismatch` | - |
| 22-31-53 x 22-37-47 | rejected: `mode_mismatch`, `clock_domain_mismatch` | - |
| 22-37-47 x 22-57-30 | **not rejected** (both Splat Zones, 5:00) | 32 used, 12.5% contain 0, median 2.19 s |
| 22-57-30 x 22-20-42 | rejected: `mode_mismatch` | - |

  The unrejected control shows why the operator's declaration stays part of
  the contract: two unrelated Splat Zones matches pass every structural check.
  Its death residuals (12.5% contain 0) differ sharply from the true pairs
  (84%), but death evidence is not part of `pair_status` by contract.

## What held

On the three consistent pairs, the mapping met both residual criteria, every
HUD edge in all four pairs was consistent, and `delta = 0` stayed within
uncertainty in every match.
