# v2 held-out FAIL: read-only diagnosis

Status: **diagnosis only.** The v2 verdict (`../REPORT.md`, FAIL) stands as
recorded and is not rescued. No code, config, threshold, offset, exclusion or
relabeling change. No v3 design. The five held-out pairs are now a failed
diagnostic corpus.

## Summary

The displacement does not enter at the clock. It enters at **team-side
assignment**: the Review import's `ally_row` is swapped in all three failing
pairs. `ally_row` comes from the gameplay **team colour calibration**, which
latched on pre-HUD scenery (sky and ink splashes) instead of the roster icons.
Association pairs only same-side deaths, so these pairs compared each death
with the other team, which produced scattered ±5 s residuals. Alignment
itself (clock fit, Review axis, mapping) is consistent with `delta = 0` once
the side is correct.

## Is the offset constant within a recording? No

Per-death roster-X residual midpoints (`alignment_tuning.json`):

| Pair | Used | Residuals | Shape |
|---|---|---|---|
| 20-17-32 (pass) | 19 | 16 of 19 at +0.16 … +0.61 s; 3 at +3.9 … +5.2 s | tight cluster, a few wrong-onset matches |
| 20-25-22 (pass) | 17 | all +0.24 … +0.75 s | tight cluster |
| 17-42-59 | 10 | −5.45, +5.52, −2.16, +4.84, −0.17, +4.83, −2.21, +1.91, −1.53, −1.53 | spread over the whole ±5 s window |
| 19-47-46 | 3 | −3.15, +5.53, −5.39 | three unrelated values |
| 19-59-26 | 6 | −4.05, −3.95, +0.05, +0.05, +5.48, −1.55 | spread |

In the failing pairs the same glyph is matched to onsets 5–7 s apart. The
"−3.15 s median" of 19-47-46 is the middle of three unrelated errors, not a
consistent term.

## Stage by stage

1. **Raw timestamps, timer observations, clock fit: fine.** Every pair
   calibrated with `c` width 0.5 s and 77–284 consistent edges. The Review
   timeline's own end clock equals the gameplay's last HUD read to the second
   in 17-42-59 (124 = 124), 19-47-46 (174 = 174) and 20-17-32 (17 = 17).
   That is independent evidence that the pairs are the right matches. Three
   of the five ended by knockout, and the Review axis fit adapts to that
   (slope −107 to −215 against −343 and −366 for full-length matches; maximum
   axis error at most 0.19 s).
2. **Review glyph timestamps: fine, wrong side label.** Side by side for
   17-42-59 (remaining clock, s):

   | Sequence | First values |
   |---|---|
   | Review glyphs labelled ally (top row) | 284.0, 281.2, 276.2, 249.5, 247.8, 243.6, 237.7, 220.0, 213.0 |
   | Gameplay **opponent** roster-X onsets | 284.0, 281.0, 276.0, 249.5, 247.5, 243.5, 237.5, 220.0, 213.0 |
   | Review glyphs labelled opponent (bottom row) | 289.5, 270.2, 258.4, 217.8, 207.3, 188.5, 181.9 |
   | Gameplay **ally** roster-X onsets | 289.5, 270.0, 258.5, 218.0, 207.5, 188.5, 182.0 |

3. **Association: compares the wrong teams.** Share of roster-X onsets with
   a same-side glyph within 1 s, under each possible `ally_row`:

   | Pair | Imported `ally_row` | If top | If bottom | Verdict |
   |---|---|---|---|---|
   | 17-42-59 | top | 0.07 | **0.83** (median +0.02 s) | swapped |
   | 19-47-46 | bottom | **0.28** | 0.08 | swapped |
   | 19-59-26 | bottom | **0.25** | 0.10 | swapped |
   | 20-17-32 | top | **0.86** | 0.22 | correct |
   | 20-25-22 | bottom | 0.22 | **0.92** | correct |

   19-47-46 and 19-59-26 are close to their ceiling of about 0.3: their
   bottom team is blue (hue 119) and has no Review glyphs (the separate
   bottom-row hue issue, `../../../row_hue_diagnosis/REPORT.md`). All nine
   v1/v2 tuning and v1 held-out pairs pass this check with their imported
   side, so 3 of 14 pairs so far are swapped, all in this batch.

4. **Where the wrong side comes from: gameplay team colour calibration.**
   `review/team_identity.py` picks the Review row whose hue is nearer the
   gameplay `team_color_calibration.ally_h` (`match_identity.json`).

   | Pair | Mode | Gameplay ally / opponent hue | Review top / bottom hue | Truth (HUD) |
   |---|---|---|---|---|
   | 17-42-59 | splat_zones | 22 / **103** | 38 / 152 | ally magenta (152), opponent lime |
   | 19-47-46 | splat_zones | 128 / **101** | 10 / 119 | ally on top row (from deaths) |
   | 19-59-26 | splat_zones | 133 / **101** | 9 / 119 | ally on top row (from deaths) |
   | 20-17-32 | splat_zones | 37 / **109** | 38 / 152 | ally correct by the nearer-hue rule |
   | 20-25-22 | tower_control | 128 / 41 | 25 / 139 | correct |

   The calibrator (`vision/team_color_calibration.py`) samples the ally-4
   and opponent-1 HUD slot boxes on frames after `Ready?` and before the
   first clock tick. It accepts a frame when the two boxes' hues differ by at
   least 30°, and latches on the median of the first three accepted frames.
   In 17-42-59 the roster HUD only appears at about 13.0 s; from 9.5 to
   12.5 s both boxes show sky (hue 103 to 105). Replaying the calibrator
   class on decoded frames reproduces the stored values within about 2 hue
   units:

   | Pair | Accepted frames (t, ally hue, opponent hue) | Latched |
   |---|---|---|
   | 17-42-59 | (6.5, 24, 102), (7.0, 22, 154), (13.0, 150, 41) | 24 / 102 |
   | 19-47-46 | (40.5, 133, 6), (41.0, 8, 100), (41.5, 137, 100) | 133 / 100 |
   | 19-59-26 | (11.5, 135, 100), (12.0, 7, 99), (12.5, 135, 100) | 135 / 100 |
   | 20-17-32 | (10.0, 41, 109), (10.5, 39, 109), (11.0, 39, 109) | 39 / 109 |

   Only one accepted frame in 17-42-59 (13.0 s, 150 / 41) shows the roster
   icons; the other two are scenery. The 30° separation check is satisfied
   when one box catches an ink splash or stage colour and the other catches
   sky. In 20-17-32 the side came out right only because the scenery ally
   hue (39) happened to equal the true ally colour.

   The replay started at a fixed time rather than at the detected `Ready?`,
   so the accepted times are indicative; the latched values match the stored
   calibration.

## What this is, and what it is not

- **Not an alignment-method problem.** Clock calibration, the Review axis
  and `delta = 0` behave as in tuning once the side is correct (17-42-59:
  0.83 within 1 s, median +0.02 s).
- **An upstream identity provenance problem.** Team side reaches alignment
  through a colour calibration that can latch on pre-HUD scenery. All six
  Splat Zones matches in this corpus have a scenery opponent hue (87 to
  109); every other mode got both hues from the icons. Whether that is mode,
  stage or intro-camera dependent is not established here.
- **Wider exposure, not measured here.** The same calibration also binds
  ally / opponent ink colours for map ink (`ScenarioContext.map.ink`). A
  scenery latch there would misattribute ink. This diagnosis does not
  measure that.
- **The FAIL stands.** The 19 swapped residuals are now understood, but the
  gate ran with the imported side, as pre-registered.

## For a later, separately decided step (not designed here)

Possible directions, each needing its own pre-registration and fresh data:

- calibrate team colours only on frames where the roster icons are present;
- take `ally_row` from an operator declaration (`--ally-row`), like the pair
  declaration;
- carry the side's provenance and refuse alignment references when the
  colour evidence is weak (for example separation under a bound, or ally and
  opponent hues not near the two Review row hues).

Which layer owns the fix (vision calibration, Review import, or the
alignment contract) is the first decision, before any v3.

## Reproduce

- Identity matrix (all Review x gameplay, shift scan):
  `PYTHONPATH=tools/diagnostics .venv/bin/python tools/diagnostics/review_gameplay_identity.py --pair <review> <gameplay> ... --out analysis/review_validation/alignment/v2/diagnosis`
  writes `identity.{md,json}`. The diagonal passing pairs score 0.86 and 0.92
  at shift about 0; the failing pairs score at most 0.44 against every
  gameplay video within ±30 s, because the side is swapped.
- The side-swap shares, the calibrator replay and the frame crops were
  computed inline from stored artifacts and the source videos; nothing was
  written back.
