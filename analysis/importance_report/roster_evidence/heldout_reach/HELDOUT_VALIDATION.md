# Held-out validation of the frozen roster reach candidate

Candidate frozen and pre-registered in `FREEZE.md` (commit `0fcf3ed`) before any
held-out frame was decoded: **A1 search area +16 native px right, O3 +8 native
px right**, everything else production (templates, masks, scale, masked
`TM_SQDIFF_NORMED`, threshold 0.03, visibility gate, fusion, coaching).
Production code was unchanged during validation. The values were not altered
after scanning or labeling.

**Outcome: PASS. Promoted to production exactly as frozen** (see *Decision and
promotion*). The ROI-reach investigation is closed.

## Result

On fresh held-out live gameplay the candidate fixed 115 slots and broke none.

| Paired slot transition (production -> candidate) | All | Random | Discordant | After 2026-09-10 |
|---|---|---|---|---|
| correct -> correct | 1380 | 724 | 663 | 711 |
| wrong -> correct | **115** | **8** | **108** | **62** |
| correct -> wrong | **0** | **0** | **0** | **0** |
| wrong -> wrong | 0 | 0 | 0 | 0 |

There are no candidate-created false positives, so no FP overlays exist. Every
changed slot is a recovered X, and every recovered placement sits on the slot's
own X (overlays below).

The pre-registered failure rule ("if either value proves inadequate, record the
failure and stop") is **not triggered**: no held-out A1/O3 X is missed by the
candidate and no slot gets worse. Two margin observations are recorded under
*Margins*; neither is a failure under the pre-registered criteria.

## Data

- Pool: 105 unreferenced live 1080p videos (`pool.json`); 104 produced eligible
  frames. 1 Hz scan: 27,697 frames passed the production visibility gate; the
  two methods disagreed on 1,115 frames in 312 runs.
- Sample (seed 20261015): 100 random frames (at most 2 per video) and 100
  discordant runs (one frame each, capped from 312); one frame was in both, so
  199 frames.
- Labels: completed blind to `sealed/key.json`, sealed in `90572c5` before
  scoring. 188 frames `visible`, 11 `hidden` (8 random, 3 discordant). 1,495
  determinable slots, 324 human X, 97 `?` slots (1 on O3, 0 on A1).
- Commit `3e8f1a1` captured an earlier, mostly prefilled draft of
  `labels.csv`; it was never scored. `90572c5` is the label set used here.

## Metrics

All determinable slots, both methods, same frames and labels.

| Set | Method | TP | FP | FN | TN | X precision | X recall | Side count exact | Raw AvB exact |
|---|---|---|---|---|---|---|---|---|---|
| All | production | 209 | 0 | 115 | 1171 | 1.000 | 0.645 | 255/367 | 81/180 |
| All | candidate | 324 | 0 | 0 | 1171 | 1.000 | 1.000 | 367/367 | 180/180 |
| Random | production | 95 | 0 | 8 | 629 | 1.000 | 0.922 | 172/180 | 81/89 |
| Random | candidate | 103 | 0 | 0 | 629 | 1.000 | 1.000 | 180/180 | 89/89 |
| Discordant | production | 116 | 0 | 108 | 547 | 1.000 | 0.518 | 84/189 | 0/92 |
| Discordant | candidate | 224 | 0 | 0 | 547 | 1.000 | 1.000 | 189/189 | 92/92 |
| After 2026-09-10 | production | 110 | 0 | 62 | 601 | 1.000 | 0.640 | 130/191 | 40/95 |
| After 2026-09-10 | candidate | 172 | 0 | 0 | 601 | 1.000 | 1.000 | 191/191 | 95/95 |

The **random** stratum estimates ordinary-frame behavior: production X recall
0.922 (development recomputed production: 0.915) rises to 1.000 with no
precision cost. The **discordant** stratum is by construction the frames where
the methods disagree, so its production numbers are not a frame-level rate.
Raw AvB transitions (all): correct->correct 81, wrong->correct 99,
correct->wrong 0.

The after-2026-09-10 subset (recorded after the template commits, so it cannot
contain the unrecorded template source) shows the same pattern.

### Per slot

| Slot | Set | Paired c->c / w->c / c->w / w->w | Production TP/FP/FN/TN | Candidate TP/FP/FN/TN |
|---|---|---|---|---|
| A1 | all | 126 / 62 / 0 / 0 | 5 / 0 / 62 / 121 | 67 / 0 / 0 / 121 |
| A1 | random | 87 / 5 / 0 / 0 | 5 / 0 / 5 / 82 | 10 / 0 / 0 / 82 |
| O3 | all | 134 / 53 / 0 / 0 | 11 / 0 / 53 / 123 | 64 / 0 / 0 / 123 |
| O3 | random | 89 / 3 / 0 / 0 | 11 / 0 / 3 / 78 | 14 / 0 / 0 / 78 |

All other slots are identical under both methods by construction.

## Held-out A1/O3 X centres and reach

`heldout_results.json` -> `x_reach` (schema 2) lists every held-out human X on
A1 and O3 (67 A1, 64 O3) with its centre (best placement over the ROI widened
24 px each side), reach intervals and both methods' labels.

| Slot | X-centre clusters (native x, count) | Production detected | Candidate detected |
|---|---|---|---|
| A1 | 559 (1), 568 (2), 591-592 (2), **607-609 (62)** | 5 (all centres <= 592) | 67 |
| O3 | 1237-1238 (2), 1247-1248 (2), 1263-1264 (4), 1269 (3), **1279-1281 (53)** | 11 (all centres <= 1269) | 64 |

These are the same clusters found in development (A1 outer cluster 607-608,
O3 outer cluster 1280). Every production miss is in the outer cluster; every
outer-cluster X is recovered by the candidate.

The first version of this report measured reach with only the *free-window
best template* (`player-x-3` for O3). Under that template the candidate's O3
reach ends at 1277.6, so 52 of 53 outer-cluster O3 X were flagged "beyond
candidate reach" while being detected. That was a flaw in the diagnostic, not
a miss: the candidate wins those slots with `player-x-1`, whose placement
centre lands at 1279.1-1281.1 (on the X, within about 0.5 px).

Commit `60a51c7` fixed the diagnostic only (detector, sealed key and labels
unchanged; every non-reach field of `heldout_results.json` identical). `score`
now re-decodes each A1/O3 human-X frame and records three reach measures per
method: the free-window template (`reachable_*_free_template`, the old field),
any fitted template (`reachable_*_any_template`) and the template that actually
wins (`reachable_*_winner_template`).

| Slot | Method | Free template | Any template | Winning template | Detected |
|---|---|---|---|---|---|
| A1 | production | 5 / 67 | 5 / 67 | 5 / 67 | 5 |
| A1 | candidate | 67 / 67 | 67 / 67 | 67 / 67 | 67 |
| O3 | production | 11 / 64 | 11 / 64 | 11 / 64 | 11 |
| O3 | candidate | 12 / 64 | **64 / 64** | **64 / 64** | 64 |

With the corrected measures, reach agrees with detection on every slot. The
candidate wins A1 with `player-x-1` 66 times (`player-x-3` once) and O3 with
`player-x-1` 58 times (`player-x-3` 6 times).

## Margins

These are observations, not failures; the values remain frozen.

1. **O3 +8 is fully used (zero margin).** 15 of 53 O3 recoveries need all
   8 px (placement right edge 8 px past the production ROI); none needs more.
   The `player-x-1` reach ends at 1281.1, so an O3 X centred at 1282 or beyond
   would not be reached. None was observed in 64 held-out O3 X or in
   development. This is recorded, not acted on: the value stays frozen.
2. **A1 +16 uses at most 14 px** (12 px for 43 cases, 13 for 18, 14 for 1).
3. **Recovered A1 scores approach the threshold.** Candidate scores on the 62
   recovered A1: min 0.0095, median 0.0161, max 0.0298 (H134 0.0298, H141
   0.0296). H134 is overlapped by a "Danger!" banner; placement is on the X.
   Recovered O3 scores are well clear (max 0.0143).
4. **Alive headroom.** Lowest candidate score on a human-alive slot: A1 0.0471
   (H095, unchanged from production), O3 0.0474 (H121, production 0.0593).
   Both stay above 0.03.

## Overlays

`sheets/recovered_examples.png`: worst score and largest extension per slot
(H134 A1, H109 A1, H090 O3, H004 O3). Green = production ROI, cyan = candidate
extension, magenta = winning template mask, orange = neighbour ROI.

![Recovered examples](sheets/recovered_examples.png)

All 115 recovered overlays are written to `overlays/` by `score` (not committed;
about 32 MB). There are no candidate-FP overlays because there are no candidate
FPs.

## Limitations

- Template provenance is unrecorded; the after-2026-09-10 subset addresses this
  and agrees.
- Random stratum is 92 visible frames; the absence of FPs there bounds the FP
  rate only coarsely. The discordant stratum covers 100 of 312 disagreement
  runs; all 100 sampled were recoveries, none collateral.
- Replay footage, R029 / dark-silhouette handling and Lead!-conditional
  behavior remain out of scope.

## Decision and promotion

**Decision (user): PASS.** A1 +16 px right and O3 +8 px right were promoted to
production exactly as frozen. There was no further ROI expansion and no
threshold change (0.03). The O3 reach finding was treated as a diagnostic
defect, logged and fixed separately; it did not block promotion.

### Audit trail

| Commit | Content |
|---|---|
| `0fcf3ed` | Freeze and pre-registration (`FREEZE.md`, `pool.json`) |
| `3e8f1a1` | Earlier, mostly prefilled **draft** of `labels.csv`. **Not the sealed labels**; never scored |
| `90572c5` | **Sealed** final held-out labels, committed before scoring |
| `33f89c7` | Held-out scoring and this report (`heldout_results.json`, overlays) |
| `5a84802` | Production promotion: config fields, detector search extension, tests |
| `3ca8fab` | Production-equivalence diagnostic and `production_equivalence.json` |
| `60a51c7` | Diagnostics-only fix of the O3 reach measure |

### Production implementation

`vision.player_count.ally_search_extend_right_px: [16, 0, 0, 0]` and
`opponent_search_extend_right_px: [0, 0, 8, 0]` (px at 1920 wide, scaled by
frame width). Only the template search crop widens to the right. Templates and
masks are still fitted to the production slot ROI, and the ROI itself (used by
the visibility gate, team-colour calibration and the manifest viewer) is
unchanged. A slot with zero extension takes the original code path.

### Production equivalence

`tools/diagnostics/roster_reach_equivalence.py` ran the production detector,
with and without the extensions, against the independent research
implementation (`roster_roi_reach` placement search). It covered 120
development frames (decode-verified) and the 199 held-out frames
(image-hash verified): 300 visible frames, 2,400 slot comparisons.

- **0 mismatches** (tolerance 1e-6). Result: `production_equivalence.json`,
  `passed: true`.
- 4 slots score above 1.0 in the raw reference (R059 O2, H054 O3/O4,
  H078 A4). Production reports them as 1.0 because its best score starts at
  1.0, both before and after the change. The check treats this ceiling as
  equal; neither implementation was altered.
- Label changes occur only on A1 (72) and O3 (56), all alive -> X. 123 are
  human-labelled X recoveries (115 held-out, 8 development). The other 5 are
  on frames humans labelled `hidden` that still pass the production
  visibility gate (R068, H033, H089, H105, H128; A1, scores 0.019-0.0295).
  These slots are `?` in the labels and are not scored either way.
- Zero-extension slots match the old code path exactly (unit test with exact
  numeric equality).

### Regression

`ruff check src tools tests` and the full `pytest` suite: 1,635 passed,
49 skipped. There are 2 failures
(`test_public_site.py::test_dev_mode_keeps_regular_upload_limits`,
`test_public_site_refuses_to_start_without_turnstile`) and 1 ruff E501 in
`tests/test_public_abuse.py`. All three fail identically without these
commits, and they are unrelated to the roster detector.

### Closure

The ROI-reach investigation is closed. +16 / +8 passing equivalence means
promotion and closure, not another optimization round. Replay footage,
R029 / dark-silhouette handling and Lead!-conditional behavior remain out of
scope.

## Reproduce

```bash
cd tools/diagnostics
../../.venv/bin/python roster_heldout_reach.py build --output ../../analysis/importance_report/roster_evidence/heldout_reach
../../.venv/bin/python roster_heldout_reach.py score --output ../../analysis/importance_report/roster_evidence/heldout_reach
```

`score` checks every blind image against its sealed SHA-256 before scoring.

Production equivalence (from the repo root):

```bash
.venv/bin/python tools/diagnostics/roster_reach_equivalence.py \
  --baseline analysis/importance_report/roster_evidence/baseline/labels \
  --heldout analysis/importance_report/roster_evidence/heldout_reach \
  --output analysis/importance_report/roster_evidence/heldout_reach/production_equivalence.json
```
