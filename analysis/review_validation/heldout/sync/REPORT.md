# Held-out synchronisation check

Six new Review timeline recordings in two batches (not used for any tuning;
batch 1 below, batch 2 further down), imported with
the frozen pipeline (`s3-coach review timeline-video-import`, default
config) into `../<rid>/`:

| Recording | Video | Samples | Specials | Death markers | Axis MAE (s) | s/px | Cursor step |
|---|---|---|---|---|---|---|---|
| 21-35-40 | yellowgreen / purple | 293 | 29 | 24 | 0.038 | 0.119 | 8 px (~0.95 s) |
| 06-43-43 | yellowgreen / pink | 859 | 31 | 55 | 0.061 | 0.189 | 5 px (~0.95 s) |
| 06-46-06 | limegreen / red | 775 | 33 | 38 | 0.061 | 0.189 | 5 px (~0.95 s) |
| 06-52-33 | yellow / purple, knockout | 268 | 8 | 7 | 0.033 | 0.059 | 17 px (~1.0 s) |

The timeline is scaled to match length, so s/px differs, but every recording
advances ~1 s per cursor step; brackets stay ~1 s wide. The diagnostics were
run unchanged. No special labels exist for these recordings, so special
detection accuracy itself is not measured here (glow timing is indirect
evidence).

## Lamp timing (`roster_timing/`)

| Group | Deaths | Bracketed | Contain 0 | Consistent offset (s) | Midpoint median (s) |
|---|---|---|---|---|---|
| all | 124 | 122 | 117 | [0.00, 0.00] | +0.18 |
| 21-35-40 | 24 | 23 | 23 | [0.00, 0.20] | +0.24 |
| 06-43-43 | 55 | 55 | 52 | [0.00, 0.00] | +0.09 |
| 06-46-06 | 38 | 38 | 36 | [0.00, 0.15] | +0.28 |
| 06-52-33 | 7 | 6 | 6 | [-0.25, 0.05] | -0.19 |
| top / bottom | 72 / 52 | 70 / 52 | 66 / 51 | [0, 0] / [0, 0] | +0.26 / +0.09 |

All five misses are exactly 1 px late (0.19 s), the same pattern as
21-23-30 in the tuning set. Zero remains the most consistent offset; no
correction. Dead-state durations [7.0, 8.9] s (tuning set [6.87, 8.92]).

## Special timing vs glow (`glow_timing/`)

90 of 101 specials have a glow-end bracket; 76 contain 0 (84%; tuning set
93%). Best-supported offset +0.10 s (77 brackets; 0 s fits 76). Misses:
-0.10 (x2), +0.06 (x2), +0.10 (x4), +0.19, +0.21, +0.28 (x4); the largest are
1.5 px at 0.189 s/px. The glow end tends to fall slightly after the glyph
centre, at sub-2-px scale; it is not distinguishable from zero (76 vs 77) and
is within glyph localisation. No correction.

## Synchronisation (`special_roster/`, X sweep)

| X (s) | Unknown slot states (of 808) | Specials with both counts known | Using slot dead | Lane order agree / disagree / lamp unknown |
|---|---|---|---|---|
| 0.0 | 53 | 63 / 101 | 0 | 67 / 0 / 6 |
| 0.5 | 81 | 46 / 101 | 0 | 57 / 0 / 16 |
| 1.0 | 109 | 32 / 101 | 0 | 45 / 0 / 28 |

17 `no_lamp_frames` slots are all `06-43-43` bottom_2: that player
disconnected mid-match (empty dark silhouette, no X, no weapon;
`bottom_row_06-43-43_x1200.png`). Reported as unknown, which is the correct
conservative outcome; there is no separate `disconnected` state.

## Batch 2: 07-45-59 (yellowgreen / pink, Turf War) and 07-54-48 (limegreen / orange)

Imported the same way. Timing/sync outputs above were regenerated over all six
held-out recordings (sections below supersede the four-recording numbers):

| | Lamp brackets containing 0 | Glow brackets containing 0 | Lane order agree / disagree (X = 0) | Using slot dead |
|---|---|---|---|---|
| six recordings | 172 / 183 (consistent {0}) | 108 / 124 (best +0.10, 0 fits 108 vs 111) | 97 / 0 | 0 |
| 07-45-59 | 21 / 21 | 10 / 11 | | |
| 07-54-48 | 34 / 40 (best +0.20..+0.35, 35) | 22 / 23 | | |

07-54-48 repeats the 21-23-30 pattern: every lamp miss is exactly 1 px late
(0.19 s). Not corrected (held-out; and zero still fits 34 of 35 maximum).

### Held-out failure: disconnected slot read as alive

07-54-48 `top_4` disconnects mid-match (empty dark silhouette from ~x 530;
crops `top_row_07-54-48_mid.png`, `top_row_07-54-48_end.png`). Most frames
read it `unknown`, but some read `alive` at low confidence (0.17-0.43), e.g.
x 821, 848-863, and from x ~1660 where ink from the graph overlaps the slot
near the right edge. `lamp_states` keeps a known reading over `unknown` at the
same x, so these stray reads become the lamp state, and the special at x
834-847 and those at x 1679-1697 report top_4 alive (team count too high).
06-43-43 `bottom_2` (also disconnected) stays `unknown` throughout. A
disconnect also appears in the tuning set (21-41-07 `top_1`, dark silhouette
on boundary sheet B46), so any fix should be designed there and re-checked on
fresh held-out data, not tuned on this recording.

### Special-type coverage (detected events, not yet label-confirmed)

| Special | Tuning set (confirmed) | Held-out (detected) |
|---|---|---|
| big_bubbler | 12 | 0 |
| booyah_bomb | 5 | 4 |
| crab_tank | 2 | 7 |
| ink_storm | 8 | 11 |
| ink_vac | 4 | 5 |
| killer_wail_5.1 | 18 | 11 |
| reefslider | 0 | 8 |
| splattercolor_screen | 0 | 9 |
| super_chump | 0 | 4 |
| tacticooler | 5 | 9 |
| tenta_missiles | 0 | 2 |
| triple_inkstrike | 6 | 12 |
| triple_splashdown | 10 | 12 |
| trizooka | 1 | 0 |
| ultra_stamp | 4 | 19 |
| wave_breaker | 6 | 22 |
| zipcaster | 0 | 12 |
| inkjet, kraken_royale | 0 | 0 |

Five types (reefslider, splattercolor_screen, super_chump, tenta_missiles,
zipcaster) have never been label-confirmed; held-out special identity is
unverified until special labels exist for these recordings.

## Held-out boundary scoring (raw, unchanged system)

Blind labels from the compact BEFORE / AT / AFTER sheets
(`boundary/reviewed_labels.yaml`, `boundary_batch2/reviewed_labels.yaml`);
scores in `boundary/score/`, `boundary_batch2/score/`.

| Batch | Cases | Correct | Wrong | Indeterminate (X = 0 known) | In-bracket (X = 0 unknown) |
|---|---|---|---|---|---|
| B | 71 | 28 | 6 (max 0.57 s) | 2 | 35 |
| C | 30 | 18 | 0 | 1 | 11 |

Raw empirical X: B 0.57 s, C 0.19 s.

Sheet audit of every case that scores against X = 0 (no system change):

- B07, B08, B09, B39, B46, B58 (`wrong`): each sheet shows the X = 0 state
  (B09 and B39 via an AT frame, the others via matching BEFORE / AFTER); the
  labels are the opposite. Label slips, pending reviewer re-check.
- B66, C30 (`indeterminate`): BEFORE and AFTER both show the X; conservative
  labels; would be `dead`.
- B38 (`indeterminate`, X = 0 `alive`): the disconnected 06-43-43 `bottom_2`
  silhouette, read `alive` near the end of the match. A genuine pipeline
  error, the disconnect failure above, not a timing error.

If the eight label corrections are confirmed, no held-out case contradicts the
zero-offset / X = 0 timing; the remaining held-out failure is the
disconnected-slot false-alive read.

## Boundary cases (`boundary/`, `boundary_batch2/`)

Batch 2 (07-45-59, 07-54-48): 30 cases `C01`-`C30` (flip 7 alive->dead +
5 dead->alive; respawn supplement 18), same rules and sheet format.

### Raw held-out score (blind labels on compact BEFORE / AT / AFTER sheets)

Labels: `boundary/reviewed_labels.yaml` (B: 27 alive / 23 dead / 21
indeterminate), `boundary_batch2/reviewed_labels.yaml` (C: 8 / 14 / 8).
Scored unchanged against zero offset + X = 0 (`*/score/`).

| Batch | X = 0 states correct | Wrong | Indeterminate where X = 0 gave a state | Raw empirical X |
|---|---|---|---|---|
| B | 38 | 6 (B07, B08, B09, B39, B46, B58) | 2 (B38, B66) | 0.57 s |
| C | 19 | 0 | 1 (C30) | 0.19 s |

All other indeterminate labels are in-bracket cases where X = 0 already
reports `unknown`; 20 in-bracket cases were decided by the reviewer (mostly
via an AT frame), which X = 0 leaves conservatively `unknown`.

Audit of the 9 disagreements against their own sheets:

- The 6 "wrong" cases each show the X = 0 state in every frame on the sheet
  (e.g. B07: full-colour icon BEFORE and AFTER, labelled dead; B09: X in the
  AT and AFTER frames, labelled alive). They look like label slips, not
  pipeline errors.
- B66 and C30 show the X both BEFORE and AFTER the special (decidable dead).
- B38 is the disconnected 06-43-43 `bottom_2` silhouette; `indeterminate` is
  the right label and X = 0 reporting `alive` there is the known
  disconnected-slot false-alive failure, not a timing failure.

The raw score is kept as the official first-pass result. The 8 cases
(excluding B38) are being re-checked blind (`recheck/`, no prior label or
pipeline state shown) before the final held-out score.

Same corpus rules as the tuning set (flip window 0.5 s, respawn supplement
within 1 s): 71 cases (flip 18 alive->dead + 10 dead->alive; respawn
supplement 43). 37 at distance 0, 14 between 0.05 and 0.25 s. Sheets now tag
each frame BEFORE / AT / AFTER the special. Awaiting review
(`boundary/draft_labels.yaml`); scoring then tests the tuning-set result
(zero offset, X = 0) on unseen data.

## Final gate criteria (written before the recheck)

Written and committed before `recheck/recheck_labels.yaml` is reviewed or
read. The gate is a validation of the frozen zero-offset / X = 0 timing; batch
B and C are held-out and are never used for tuning. Inputs: batch B
(`boundary/`) and batch C (`boundary_batch2/`) raw reviewed labels, with the
blind recheck of B07, B08, B09, B39, B46, B58, B66, C30 merged over them
(recheck wins; the raw label is kept). Mechanical implementation:
`tools/calibration/review_sync_boundary_sheet.py gate`.

**Timing failure.** X = 0 resolved to a state (`alive` / `dead`) and the
final reviewed label is either

1. the opposite state, or
2. `indeterminate`.

Clause 2 implements "indeterminate even though BEFORE and AFTER disagree"
strictly. In B and C every case where X = 0 gives a state lies outside the
lamp transition bracket (all 46 in-bracket cases read `unknown`), so the
BEFORE / AFTER disagreement cannot be derived from `cases.json`. Rather than
let the clause never fire, any X = 0 state that review does not support counts
(decided with the user before the recheck; same as the existing `score`
window, which treats indeterminate as unsupported).

X = 0 reporting `unknown` inside a genuine transition bracket is expected
boundary uncertainty, not a timing failure, whatever the label.

**Non-timing failure.** A disagreement is excluded from the timing gate only
with independently observable evidence that it arose outside
synchronisation / timing. The pre-registered exclusion set
(`gate/known_failures.yaml`) is exactly `B38: disconnected_slot` (06-43-43
`bottom_2`, disconnected-slot false-alive read, documented above). No case is
added after the result is seen; later diagnoses of detector, label or
Review-cursor problems are reported separately and leave the mechanical
result unchanged. Non-timing failures stay listed in the output.

**Direction** (timing failures only; mechanical, from `transition_type`,
`transition_bracket_s_relative_to_special`, final label and X = 0 state):

- `lag`: X = 0 shows the transition's before-state, label shows its
  after-state (bracket after the special).
- `lead`: X = 0 shows the after-state, label shows the before-state
  (bracket before the special).
- `none`: anything else (label `indeterminate`, or X = 0 inconsistent with
  the bracket side).

**Systematic bias.** At least 3 timing failures and at least 80% of them in
the same direction (`lag` or `lead`; integer test 5 x dominant >= 4 x n).
One or two isolated failures are not evidence for an offset change.

**PASS** requires

1. zero timing failures over merged B + C, and
2. X = 0 remains the empirical choice: among timing-attributable cases with a
   determinable label, X = 0 has zero timing failures (in-bracket `unknown`
   excluded).

Condition 2 is redundant with condition 1 under this scorer (same case set:
every timing failure is timing-attributable and X = 0-known); the gate reports
it as such, with the timing-only empirical X, instead of a second statistic.

**Bias found.** FAIL. No tuning on B or C, no search over B + C for another
X; a correction hypothesis is derived separately and validated only on new
recordings.

**Timing failures without bias.** FAIL. Implementation unchanged; failing
cases listed and diagnosed; no threshold or offset change.

## Final held-out gate

Mechanical result: `gate/gate.json`, `gate/gate.md` (committed `9f052f6`
before any failure was inspected). Pre-registration `8828b46`; gate tool
`cefeb80` (tool and config clean at run time); `configs/default.yaml` sha256
`3ef4b496…e503f`, `review` block sha256 `98c2bf53…5e24`. Blind recheck labels:
`recheck/recheck_labels.yaml` (8 cases, reviewer `status: reviewed`).

**Verdict: FAIL** (isolated timing failures, no systematic bias).
Implementation unchanged; no threshold, offset, template, scale or dedupe
change. Nothing is frozen.

### Merged labels and score (B 71 + C 30 = 101 cases)

| | Raw labels | Final (recheck merged) |
|---|---|---|
| Labels alive / dead / indeterminate | 35 / 37 / 29 | 35 / 37 / 29 |
| Correct (X = 0 state matches) | 46 | 52 |
| Wrong | 6 | 0 |
| Indeterminate label | 29 | 29 |
| X = 0 `unknown`, label decided | 20 | 20 |
| X = 0 `unknown` in bracket / out of bracket | 46 / 0 | 46 / 0 |
| Timing failures | 8 (B07, B08, B09, B39, B46, B58, B66, C30) | 2 (B09, B39) |
| Non-timing failures (pre-registered) | 1 (B38) | 1 (B38) |
| Direction of timing failures | lag 4, lead 2, none 2 | lag 0, lead 0, none 2 |
| Empirical X, all cases | 0.57 s | 0.28 s (B38) |

Recheck: 8 cases rechecked; 6 now agree with X = 0 (B07, B08, B46, B58 flipped
from the opposite state; B66, C30 from `indeterminate` to `dead`); B09 and B39
moved from `alive` to `indeterminate`. Label totals happen to be unchanged.

### Gate conditions

1. Timing failures over merged B + C: **2** (B09, B39). Condition fails.
2. Among 52 timing-attributable cases with a determinable label and an X = 0
   state: 0 timing failures. Redundant with condition 1 (a subset of it); the
   timing-attributable empirical X is 0.09 s, set by B39 (`indeterminate`
   label, no `wrong` case).

Bias test: 2 timing failures (lag 0, lead 0, none 2); needs >= 3 and >= 80% in
one direction: **no systematic bias**. Both failures are `none` (the label is
`indeterminate`, not the opposite state), so they carry no offset direction.

### Failures (diagnosis written after the result was committed)

| Case | Recording / slot | Sheet frames | Final label | X = 0 | Direction | Mechanical kind | Diagnostic cause |
|---|---|---|---|---|---|---|---|
| B09 | 21-35-40 `top_2`, alive->dead bracket [-1.01, -0.06] s | `recheck/sheets/B09.jpg`: BEFORE -1.01 s alive, AT -0.06 s (-0.5 px) X, AFTER +1.01 s X | indeterminate | dead | none | timing | label |
| B39 | 06-43-43 `top_4`, dead->alive bracket [+0.09, +1.04] s | `recheck/sheets/B39.jpg`: BEFORE -1.04 s X, AT +0.09 s (+0.5 px) X, AFTER +1.04 s alive | indeterminate | dead | none | timing | label |
| B38 | 06-43-43 `bottom_2` | `boundary/sheets_compact/B38.jpg`: BEFORE -0.28 s and AFTER +0.66 s empty dark silhouette | indeterminate | alive | none | non_timing (pre-registered) | detection |

- **B09, B39 (label):** each sheet has an AT frame within 0.5 px of the special
  glyph centre, and it shows the X. The state at the special is therefore
  demonstrably `dead`, which is the X = 0 state; the transition lies on the
  far side of the AT frame (between BEFORE and AT for B09, between AT and
  AFTER for B39). The reviewer's basis was "both dead and alive exist" on the
  sheet, i.e. the BEFORE / AFTER disagreement was treated as indeterminate
  without letting the AT frame decide. No sheet shows the opposite state at
  the special, so no timing defect is observed. This diagnosis does not change
  the mechanical FAIL. Both cases also fail under the literal pre-registered
  wording (BEFORE and AFTER do disagree on these sheets), so the strict reading
  of clause 2 did not decide the verdict.
- **B38 (detection):** the disconnected `bottom_2` silhouette (no icon, no X)
  read `alive`; the documented disconnected-slot false-alive defect.

### Observed defects

- Disconnected-slot false-alive read: 06-43-43 `bottom_2` (B38) and 07-54-48
  `top_4` (stray low-confidence `alive` reads kept over `unknown`). Not fixed;
  to be designed on the tuning set (21-41-07 `top_1`) and re-checked on fresh
  held-out data.

### Known precision limitations

- Roster lamps about 1 px (0.19 s) late on 21-23-30 (tuning) and 07-54-48
  (held-out); zero offset still the most consistent fit. Not corrected.
- Glow-end timing best fit +0.10 s vs 0 s (77 vs 76 brackets), within glyph
  localisation. Not corrected.

### Validation coverage gaps

- Five special types never label-confirmed: reefslider,
  splattercolor_screen, super_chump, tenta_missiles, zipcaster.
- Big Bubbler and Trizooka have no held-out detections (absent from the held-out
  recordings; not a detector failure). Inkjet and Kraken Royale absent from
  both sets.
- Held-out special identity is unverified (no special labels for these
  recordings); this gate validates timing / sync only.

### Decision

The pre-registered gate **fails**; the SPECIAL_USE synchronisation / timing
lane is **not frozen**. No systematic directional bias, so no correction
hypothesis; production synchronisation (zero offset, X = 0) is unchanged and
B / C are not reused for tuning. The two failures are diagnosed as label
errors, which the gate rules do not allow to overturn the result. Closing the
lane needs a new held-out set (fresh recordings), labelled with an explicit
rule that an AT frame within glyph localisation decides the state when BEFORE
and AFTER disagree, and the same pre-registered gate.
`coach.roster_evidence` stays `false`; no scenario / CoachInput work.

### Closure (after the verdict)

- The FAIL stands as recorded. B09 / B39 are not relabelled; the post-gate
  "label" diagnosis is not used to rescue the gate.
- **B and C are permanently sealed and finished.** They are not reused for
  tuning, threshold or offset search, or as part of any later gate.
- 0.09 s (timing-attributable empirical X, set by the disputed B39) and the
  +0.10 s glow-end fit are descriptive outputs only, never candidate
  corrections. X = 0 and the zero offset are unchanged.
- What the gate showed: after the blind recheck, 0 opposite-state errors and
  no lag / lead signal (raw 4 lag / 2 lead collapsed to 0 / 0). The remaining
  failures point at the labelling protocol, not at synchronisation.

### Next gate (to be pre-registered before any new labelling)

Same question, unchanged system: does X = 0 survive fresh recordings? No
SPECIAL_USE redesign or recalibration.

Draft labelling rule, fixed from sheet geometry before labelling:

- `g` is the special glyph centre; every sheet frame prints `dx` = cursor x
  minus `g` (px) and the matching `dt` (s).
- **AT frame:** the single nearest frame with |dx| <= 0.5 px (`AT_PX`, the
  existing sheet constant; <= 0.095 s at the observed 0.059-0.189 s/px).
  Frames beyond 0.5 px are never AT, however close they look.
- **Label:** (1) if an AT frame exists and the icon is legible, its state is
  the label; (2) otherwise, if the nearest BEFORE (dx < 0) and AFTER (dx > 0)
  frames are legible and agree, that state; (3) otherwise `indeterminate`.
  Alive and dead both appearing on the sheet is not by itself grounds for
  `indeterminate`. Illegible / disconnected icons are `indeterminate`.
- This mirrors the frames X = 0 consults (last lamp frame at x <= g, first at
  x > g), but the label is the reviewer's reading of the icon, never the lamp
  detector output; sheets keep hiding the pipeline state.

Corpus: only cases where X = 0 returns a state close to a transition
challenge it (in-bracket cases read `unknown` by design). B + C had 101 cases
but only 55 with an X = 0 state, 43 of them within 0.6 s of a transition and
22 within 0.25 s. The next set should be selected for such boundary cases
(target size fixed at pre-registration), not padded with far-from-transition
cases.
