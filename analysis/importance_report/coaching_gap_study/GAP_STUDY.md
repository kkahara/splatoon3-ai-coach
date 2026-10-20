# Coaching gap study results

39 missing facts. Categories: A 20, B 3, C 7, D 9

| theme | A-view | A-pipeline | B | C | D |
| --- | --- | --- | --- | --- | --- |
| ink | 0 | 0 | 0 | 1 | 0 |
| map | 6 | 0 | 0 | 0 | 0 |
| objective | 1 | 0 | 0 | 2 | 0 |
| opponent | 0 | 0 | 0 | 3 | 7 |
| other | 0 | 8 | 0 | 0 | 1 |
| positioning | 0 | 0 | 0 | 1 | 1 |
| special | 2 | 0 | 2 | 0 | 0 |
| timing | 3 | 0 | 1 | 0 | 0 |

A + B facts per death: selected 2.0, unselected 2.75. Ranking review flagged: no (threshold 1.0).

## Decision: view-extension experiment

Path A/B (A + B 23 vs C 7); theme(s): other; kind: A-pipeline.

## Rater decision (2026-10-19)

The frozen rule was applied unchanged. Its pick does not open a new
experiment:

- All 8 A-pipeline `other` facts are roster counts (the frozen theme list has
  no roster theme; the 1 `other` D fact is gear). Showing roster to the model
  is what Experiment H tested; it closed as `keep_off_no_value`.
- The rater's ideal coaching for these facts is interpretation of the roster
  trajectory ("wait until allies are back", "retreat when outnumbered"),
  which the roster prompt rules do not allow. That is a coaching-policy gap,
  not an evidence gap.

The next experiment is therefore the runner-up: a prompt / claim-catalog
experiment on the map-check facts (6 A-view facts that the model saw and did
not use, for example `seconds_since_map_check_before_death` = 75.0 at
18-18-00 @197.5).
