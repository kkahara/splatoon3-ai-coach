# Dynamic roster ROI prototype comparison

Status: development evidence only. This comparison does not change production, fusion or coaching. The sealed labels are not held-out validation.

## Scope and method

- Fixed path: unchanged production fixed ROIs and masked-X threshold `0.03`.
- Dynamic path: wide side search, constrained M1/M2 localization, canonical crop normalization, then the unchanged masked-X matcher at `0.03`.
- Localization and classification remain separate. The prototype has no fusion path and no fused-AvB result.
- Live gameplay and replay-derived footage are separate; the replay result is one recording.

## Primary comparison

| Source | Path | Slot precision / recall | Exact side count | Exact raw AvB |
| --- | --- | --- | --- | --- |
| Live gameplay | fixed | 96.0% / 91.5% | 91.4% (138/151) | 82.7% (62/75) |
| Live gameplay | dynamic | 100.0% / 15.1% | 54.7% (82/150) | 29.3% (22/75) |
| Replay-derived | fixed | 100.0% / 77.1% | 77.8% (28/36) | 55.6% (10/18) |
| Replay-derived | dynamic | 100.0% / 17.1% | 47.2% (17/36) | 16.7% (3/18) |

Dynamic localization did not improve the development comparison. On live gameplay, fixed raw AvB is 82.7% versus 29.3% for the prototype; fixed slot recall is 91.5% versus 15.1%. This prototype is therefore not a candidate for production selection.

## Paired slot outcomes

| Source | Both correct | Fixed correct / dynamic wrong | Fixed wrong / dynamic correct | Both wrong |
| --- | ---: | ---: | ---: | ---: |
| live_gameplay | 513 | 81 | 4 | 9 |
| replay_derived | 115 | 21 | 0 | 8 |

The paired table is computed only on human-determinable slots for which the dynamic path produced a reading. It is a development diagnostic, not a confidence interval or acceptance test.

## Geometry and failure analysis

- `GEOMETRY.md` contains the constrained geometry measurements and the 13 auditable live-miss overlays.
- All 13 known live misses were manually classified as `classification with adequate icon coverage`; none showed clear clipping or a visibility failure in the saved panels. This is an audit conclusion from the panels, not an automatic segmentation claim.
- The dynamic path missed many X markers because its localization/activity objective selected boxes that were visually plausible but poorly aligned for masked-X matching. This demonstrates why localization confidence and X classification must remain separate.
- The eight replay silhouette misses remain classification failures; dynamic localization does not solve them.

## Consistency boundary

`baseline/consistency.md` remains the authoritative unlabeled 44-match raw fixed-detector consistency baseline. A dynamic 44-match run is not reported as if the stratified 120-frame development sample were a consecutive stream; that would fabricate a cadence result. The dynamic artifacts include per-sample outputs for direct failure inspection.

## Decision boundary

Do not change production based on this prototype. If dynamic localization is revisited, first define a new constrained locator against a fresh held-out labeled sample from matches outside the sealed baseline, then repeat the same slot → side count → raw AvB ladder. No threshold sweep was used to select the primary result.
