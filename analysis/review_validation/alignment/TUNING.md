# Review to gameplay alignment: tuning summary

Five operator-declared tuning pairs, each a live player-view recording plus
that match's Review timeline sweep, matched by recording timestamp. Processed
with `s3-coach analyze --language en` and
`s3-coach review timeline-video-import --gameplay-analysis-dir`. Diagnostics:
`tuning/diagnostics/alignment_tuning.{md,json}`; per-pair mappings:
`tuning/<id>/alignment/review_gameplay_alignment.json`.

## Inputs (sha256, first 16 hex)

| Pair | Live video | Review video | `vision_manifest.json` | `review_timeline.json` |
|---|---|---|---|---|
| 23-43-31 | (moved, not hashed) | (moved, not hashed) | `fe72a9e1175d1fa3` | `98783a184fc45f76` |
| 23-56-41 | (moved, not hashed) | (moved, not hashed) | `9167636cfa715735` | `b2963e7a6864f85b` |
| 18-18-00 | `04e9c2799ec385e1` | `d0b34dbfcfbbbafe` | `f864e817c6322e56` | `3f3d32a73172644c` |
| 18-25-16 | `bed9d8db3a6b05d2` | `f37ba5c68d1b4dca` | `309392f12c30e852` | `868028f21c7c0e2f` |
| 20-33-31 | `9390fa05c32bc25f` | `2b91c7e26c957e2d` | `d730c79b4cd08f2c` | `8d3930e87b5643be` |

## Results

| Pair | Mode | End | Pair | Edges consistent | Roster-X used / contains 0 / median | Local DEATH used |
|---|---|---|---|---|---|---|
| 23-43-31 | clam_blitz | overtime | consistent | 288/288 | 16 / 94% / +0.35 s | 1 |
| 23-56-41 | rainmaker | overtime | consistent | 286/286 | 11 / 91% / +0.38 s | 0 |
| 18-18-00 | tower_control | 0:00 | consistent | 284/284 | 12 / 58% / +0.64 s | 2 |
| 18-25-16 | turf_war | 0:00 not read | consistent* | 171/171 | 8 / 100% / +0.10 s | 0 |
| 20-33-31 | rainmaker | knockout 1:44 | consistent | 171/171 | 5 / 100% / +0.24 s | 0 |

Every `c` interval is 0.5 s wide (the HUD cadence); playback rate normal.
Pooled roster-X: 52 used, 86.5% contain 0, median |residual| 0.38 s (one
outlier at 4.3 s). Pooled local DEATH: 3 used, all contain 0.

\* 18-25-16 was first `inconsistent` (`overtime_mismatch`): the final 0:00
frame was not read, which the original gameplay overtime rule took for
overtime. See decision 1.

## Decisions at freeze

1. **Gameplay overtime is three-valued** (contract, Structural checks). Not
   overtime after a clock-zero end, in Turf War, or after a knockout; unknown
   (not compared) when the last read is 0:01 without a 0:00 read. A structural
   rule change, made before any held-out data exists.
2. **`delta = 0` stays.** Every match with enough roster-X evidence is within
   its measurement uncertainty; the small positive lean (gameplay later) fits
   roster-X display latency. No match had both references usable, so the
   replication rule cannot be met.
3. **Roster-X is the gate's pass reference; local DEATH is reported only.**
   The frozen Review lane detector found no bottom-row death glyphs in the
   three recordings whose bottom team hue was 125 to 136 (23-56-41, 18-18-00,
   20-33-31). In 23-56-41 and 20-33-31 that is the ally row, where the local
   player alone died 5 and 6 times; in 18-18-00 it is the opponents' row.
   Separate follow-up; the Review detector and config stay frozen here.
4. Association window (5 s), onset persistence and ambiguity rules unchanged
   from the committed contract.

## Gate dry run on tuning data (machinery check, not validation)

`tuning/gate_dryrun/gate.md`: PASS (thresholds were set from these pairs).
Both deliberately mismatched tuning pairs were rejected structurally
(`mode_mismatch`; one also `clock_domain_mismatch`). The Review import takes
its mode from the gameplay analysis it was imported with, so a mode mismatch
there is not independent evidence of match identity.
