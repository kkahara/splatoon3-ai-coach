# Roster ROI Experiment 2: slot isolation

**Closed.** No production changes. The evidence rejects neighbor contamination as the primary explanation for the known live roster errors: Variant A fixed 0/13 known misses and introduced 4 collateral errors; Variant B fixed 0/13 and introduced 32; all three production false-positive X matches were already in target territory. Do not rerun or modify this experiment unless later evidence gives a reason to revisit localization. The next step is X appearance/classification (`../x_appearance/X_APPEARANCE.md`).

**Implementation deviation.** The plan specified a joint 3-node dynamic-programming optimization for the dividers. `estimate_dividers()` instead selects each divider by sequential greedy valley search (left to right, each confined to +/-18px of its calibrated prior and kept ordered). This deviation is recorded, not corrected; given that neither variant fixed any known miss, a joint optimizer is not expected to change the conclusion.

Status: development measurement only. Production detector, templates, threshold `0.03`, fusion and coaching remain unchanged. Experiment 1 is untouched. The sealed labels are development data, not held-out validation.

## Method

- Control is the unchanged fixed-ROI matcher.
- Variant A preserves all fixed-ROI pixels and restricts masked-X placements to estimated slot territory. Template center = centroid of nonzero X-mask support, not geometric center.
- Variant B uses a divider-bounded native-scale crop translated/padded into production ROI dimensions; no icon rescaling.
- Dividers retain slot order, move at most 18px, and fall back on weak evidence.

## Contamination measurement

Among 607 human-determinable live slots: 390 were classified as target-covered/neighbor-contaminated, 217 as well-isolated, and 21 additional slots were uncertain. No determinable slot was classified target-clipped. These are reproducible profile measurements, not ground-truth boundaries.

| Status | Slots | Fixed errors | A errors | B errors |
| --- | ---: | ---: | ---: | ---: |
| target_covered_neighbor_contaminated | 390 | 6 (1.5%) | 10 (2.6%) | 38 (9.7%) |
| well_isolated | 217 | 6 (2.8%) | 6 (2.8%) | 6 (2.8%) |

## Development comparison

| Source / method | Comparable slots | X precision | X recall | Exact side count | Exact raw AvB |
| --- | ---: | ---: | ---: | ---: | ---: |
| Live fixed | 607 | 97.0% | 91.5% | 92.0% (138/150) | 84.0% (63/75) |
| Live Variant A | 607 | 96.9% | 87.7% | 89.3% (134/150) | 80.0% (60/75) |
| Live Variant B | 607 | 95.6% | 61.3% | 73.3% (110/150) | 53.3% (40/75) |
| Replay fixed | 144 | 100.0% | 77.1% | 77.8% (28/36) | 55.6% (10/18) |
| Replay Variant A | 144 | 100.0% | 77.1% | 77.8% (28/36) | 55.6% (10/18) |
| Replay Variant B | 144 | 100.0% | 60.0% | 75.0% (27/36) | 50.0% (9/18) |

### Per-slot live accuracy

| Slot | Fixed | Variant A | Variant B |
| --- | ---: | ---: | ---: |
| A1 | 92.1% | 90.8% | 86.8% |
| A2 | 100.0% | 97.4% | 96.1% |
| A3 | 100.0% | 100.0% | 93.4% |
| A4 | 100.0% | 100.0% | 96.0% |
| O1 | 98.7% | 98.7% | 98.7% |
| O2 | 100.0% | 100.0% | 89.5% |
| O3 | 96.1% | 96.1% | 88.2% |
| O4 | 97.4% | 96.1% | 93.4% |

## Paired transitions / collateral damage

| Variant | Both correct | Fixed correct → wrong | Fixed wrong → correct | Both wrong |
| --- | ---: | ---: | ---: | ---: |
| A | 591 | 4 | 0 | 12 |
| B | 563 | 32 | 0 | 12 |

Variant A fixes 0 of the 13 original live misses and causes 4 collateral errors. Variant B fixes 0 and causes 32 collateral errors. Neither passes the paired transition test.

## Original 13 live misses

| Sample | Slot | Fixed | A | B | Contamination | Fixed FP minLoc territory |
| --- | --- | ---: | --- | ---: | ---: | --- |
| R013 | A1 | 0.3522 | 0.35216450691223145 / alive | 0.4258463382720947 / alive | 0.204 | n/a |
| R019 | O4 | 0.0318 | 0.03180727735161781 / alive | 0.030168181285262108 / alive | 0.114 | n/a |
| R021 | O3 | 0.2168 | 0.2167694866657257 / alive | 0.28327926993370056 / alive | 0.200 | n/a |
| R022 | O3 | 0.1537 | 0.15372969210147858 / alive | 0.14599725604057312 / alive | 0.217 | n/a |
| R023 | A1 | 0.3514 | 0.35138416290283203 / alive | 0.48449915647506714 / alive | 0.051 | n/a |
| R028 | A1 | 0.1424 | 0.14240732789039612 / alive | 0.1309005618095398 / alive | 0.067 | n/a |
| R029 | O1 | 0.2636 | 0.26360905170440674 / alive | 0.1963091492652893 / alive | 0.063 | n/a |
| R036 | A1 | 0.2174 | 0.21738611161708832 / alive | 0.16836801171302795 / alive | 0.086 | n/a |
| R049 | O4 | 0.0244 | 0.02440454252064228 / x | 0.02069373056292534 / x | 0.085 | target territory |
| R052 | A1 | 0.0261 | 0.02605191245675087 / x | 0.024046827107667923 / x | 0.260 | target territory |
| R057 | O4 | 0.0193 | 0.01933826506137848 / x | 0.013807174749672413 / x | 0.089 | target territory |
| R101 | A1 | 0.2635 | 0.263529509305954 / alive | 0.25840017199516296 / alive | 0.138 | n/a |
| R119 | O3 | 0.1101 | 0.11005751043558121 / alive | 0.10924795269966125 / alive | 0.295 | n/a |

The three fixed false-positive cases place the production minLoc in target territory, not divider/overlap or neighbor territory. The other ten are false negatives and neither isolation variant rescues them.

## Answers

1. **Neighbor contamination:** materially present as a geometric opportunity and common under the profile heuristic, especially in A1/O4 examples.
2. **Association with failure:** not decisive; contaminated slots did not show a clean positive error association.
3. **Divider improvement:** no. Variant A lowers live recall versus fixed; Variant B lowers it substantially further.
4. **A1/O4:** neither benefits consistently in the paired results.
5. **Collateral damage:** A breaks 4 existing correct live slots; B breaks 32.

## Artifacts and limits

- `isolation.json` and `summary.json` contain per-slot measurements and scores.
- `miss_audits/` and the four requested sheets contain visual evidence. `special_ready.png` uses margin-stratum candidates because the baseline has no special-ready label.
- Replay results stay separate; dark silhouettes are not evidence for or against isolation.
- All results are development evidence; fresh held-out labels would be required before any production consideration.

No production changes were made or recommended.
