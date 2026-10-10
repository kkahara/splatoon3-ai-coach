# Review SPECIAL_USE facts: descriptive run

Contract: `src/splatoon3_ai_coach/review/SPECIAL_USE_FUSION.md`. Builder:
`review/special_fusion.py`, `s3-coach review special-facts <import_dir>`.

This is a descriptive run of the frozen system (tag
`special-use-timing-frozen`). Nothing was tuned, and no contradiction is fixed
here. Review clock only: no gameplay mapping, no GameEvents, nothing consumed
by `analysis/` or `coach/`; `coach.roster_evidence` stays `false`.

## Inputs

- D: `heldout2/<rid>` for 09-53-27, 09-54-34, 09-55-33, 09-56-35, 09-57-43,
  09-59-27.
- Tuning set: `gate/<rid>` for 21-23-30, 21-41-07, 21-49-20, 21-56-52.
- `special_use_facts.json` was written beside each `review_timeline.json`. The
  sha256 of every `review_timeline.json` is identical before and after the run.
- The fail-closed playable-interval check refused no import. All 6 negative
  uses (5 in D, 1 in tuning) lie within recordings with observed `+M:SS`
  overtime labels and inside their range. No use is before GO (maximum
  remaining 287.66 of 300).
- Axis semantics checked on the real imports: every cursor `x` equals
  `x_norm * 1920`, and every glyph's `elapsed_estimate` equals
  `axis.elapsed_at(x_norm)` exactly, so `seconds_per_px = |slope| / 1920`.
- The promoted X = 0 rule (`review/slot_state.py`) reproduces
  `pipeline_state_x0` for all 209 B, C and D boundary cases
  (`tests/test_review_slot_state.py`).

## Counts

| | D (6 imports) | Tuning (4 imports) |
|---|---|---|
| SPECIAL_USE facts | 144 | 83 |
| `integration_valid` | 142 | 78 |
| `slot_state_at_use` alive / dead / unknown | 143 / 0 / 1 | 80 / 0 / 3 |
| `slot_state_reason` near_roster_transition / no_lamp_frames | 1 / 0 | 3 / 0 |
| Blocker `slot_dead_at_use` | 0 | 0 |
| Blocker `slot_state_unknown` | 1 | 3 |
| Blocker `unknown_type` | 1 | 2 |
| Identity label_confirmed / unconfirmed / unknown type | 99 / 44 / 1 | 81 / 0 / 2 |
| Phase regulation / overtime | 139 / 5 | 82 / 1 |
| Side ally / opponent / unknown | 0 / 0 / 144 | 0 / 0 / 83 |

Unconfirmed types detected in D: inkjet, reefslider, super_chump,
tenta_missiles, zipcaster.

Own-transition coverage (non-null / total; raw seconds, no thresholds):

| Field | D | Tuning |
|---|---|---|
| `own_transition_distance_s` | 144 / 144 (min 0.0, median 13.6) | 81 / 83 (min 0.0, median 13.2) |
| `seconds_to_next_own_death` | 88 / 144 (min 3.8) | 41 / 83 (min 4.0) |
| `seconds_since_own_recovery_observed` | 93 / 144 (min 4.5) | 41 / 83 (min 4.4) |

## Blocked facts

| Import | Index | Slot | Special | Remaining | Slot state | Blockers |
|---|---|---|---|---|---|---|
| 09-54-34 | 4 | bottom_2 | unknown | -9.49 | alive | unknown_type |
| 09-56-35 | 8 | bottom_4 | ultra_stamp | 200.59 | unknown (near_roster_transition, in bracket) | slot_state_unknown |
| 21-23-30 | 5 | bottom_3 | big_bubbler | 273.44 | unknown (near_roster_transition, in bracket) | slot_state_unknown |
| 21-23-30 | 16 | top_3 | unknown | 77.66 | alive | unknown_type |
| 21-23-30 | 19 | top_4 | killer_wail_5.1 | 265.56 | unknown (near_roster_transition, in bracket) | slot_state_unknown |
| 21-41-07 | 4 | bottom_3 | unknown | 247.94 | alive | unknown_type |
| 21-49-20 | 25 | top_4 | triple_splashdown | 244.81 | unknown (near_roster_transition, in bracket) | slot_state_unknown |

## Observations (for future work, not fixed)

- No glyph/lamp contradiction (`slot_dead_at_use`) in either set.
- Every `slot_state_unknown` is a use inside the slot's own lamp transition
  bracket (`own_transition_distance_s = 0`); none is `no_lamp_frames`.
- `side` is `unknown` for every fact because these imports were made without
  `--ally-row` / gameplay color. Side becomes available only when the import
  supplies `TeamIdentity.ally_row`.
- Five special types seen in D (inkjet, reefslider, super_chump,
  tenta_missiles, zipcaster) have no label-confirmed tuning events; their
  facts carry `unconfirmed_type` and are not blocked.
- The disconnected-slot and ~1 px lamp issues remain future detector work;
  fusion has no disconnect classifier.

## Verification

- Promoted X = 0 rule parity: 209 / 209 held-out cases (B 71, C 30, D 108).
- Review config sha256 unchanged: `98c2bf537caee518ce609812eea509e53fea1e7f4239a72120631e4230325e24`.
- Full suite attempted (at `d021131`): 1456 passed; 33 database-dependent
  public-site/admin tests could not execute because local PostgreSQL was
  unavailable. No test assertions failed. A full-suite run with PostgreSQL
  available is pending as closure verification.
