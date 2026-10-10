# Review special <-> roster timing calibration

Question: at game time T, how accurately can we say which special was used, by
which slot, and which of the eight players were alive at that same time?

Inputs: current gate imports (`gate/<rid>`, echo fix adopted) for 21-49-20,
21-23-30, 21-56-52 and 21-41-07. Validation artifacts only:
`coach.roster_evidence` stays false; nothing here reaches ScenarioContext,
CoachInput or GameEvents (no `SPECIAL_USED`).

All timing is interval-censored. A Review frame shows the roster at its
cursor position, consecutive cursor positions are ~1 s apart (~5 px at
~0.207 s/px), so any visual change is only known to lie inside a ~1.03 s
bracket. Offsets are match-forward seconds (+ = later than the glyph centre).

## A. Special-use timestamp accuracy

Independent reference: the roster **special-ready glow**. A slot's icon turns
pale while its special is ready and returns to full team colour at use. Read
as the mean saturation of the icon's bright pixels; per-slot two-level
threshold with an uncertain band (`tools/diagnostics/review_special_glow_timing.py`,
output `glow_timing/`). The glyph-centre timestamp is the production anchor.

| | Value |
|---|---|
| Detected specials (all human-confirmed in `specials/<rid>/gate_labels.yaml`) | 81 |
| Glow->plain bracket found (alive on both sides) | 60 |
| Brackets containing 0 | 56 / 60 |
| Offsets consistent with most brackets | [0.00, +0.15] s |
| Midpoint mean / median | +0.066 / +0.043 s |
| Brackets missing 0 | by -0.19, +0.04, +0.06, +0.17 s (<= 1 px) |
| Absolute error | lower bound 0 for 56/60 (max 0.19 s); upper bound = bracket, median 0.70 s |

No recording, team or slot has a bias beyond the bracket resolution.

21 specials have no bracket, all benign for a confirmed glyph:

- 13: no glow at all near the glyph (special used within about one frame of
  charging; e.g. 21-23-30 bottom_4 ink_vac shows a one-frame pink glow).
- 3: glow goes straight to dead (death at use time).
- 5: sparse/uncertain frames around the glyph.

Glow absence is uninformative, never a contradiction.

**Conclusion A:** the glyph-centre timestamp shows no measurable bias against
glow end; the error is within the ~1 s frame bracket. Use
`special time = glyph centre +/- 0.2 s` systematic + the frame bracket; no
correction.

## B. Roster-lamp transition timing

`tools/diagnostics/review_roster_timing.py`, output `roster_timing/`.
Delta = lamp alive->dead transition - death-glyph centre, same slot.

| Group | Deaths | Contain 0 | Consistent offset (s) | Midpoint median (s) | Dead duration median bounds (s) |
|---|---|---|---|---|---|
| all | 88 | 81 | [0.00, 0.00] | +0.069 | [6.87, 8.92] |
| 21-49-20 | 34 | 33 | [0.00, 0.10] | +0.096 | [5.99, 8.03] |
| 21-23-30 | 23 | 17 | [0.25, 0.40] | +0.208 | [6.42, 8.50] |
| 21-56-52 | 6 | 6 | [0.00, 0.00] | -0.037 | [7.00, 8.99] |
| 21-41-07 | 25 | 25 | [0.00, 0.00] | 0.000 | [6.87, 8.93] |
| top | 77 | 70 | [0.00, 0.00] | +0.086 | [6.42, 8.92] |
| bottom | 11 | 11 | [0.00, 0.10] | 0.000 | [7.01, 8.92] |

Per-slot tables are in `roster_timing/roster_timing.md`; no slot pattern.
All 88 death markers have a bracket (median width 1.03 s). Outliers:

- 21-23-30: five brackets miss 0 by 0.21 s (exactly 1 px); this recording's
  lamps lag its glyphs by ~0.25-0.4 s.
- 21-49-20 top_2 x719: misses by 0.13 s.
- 21-23-30 top_1 x1361 (clock 49.66): -2.28 s. Open: likely a glyph/axis
  anomaly at that position, not a lamp effect.

Dead->alive (respawn) brackets give death-state intervals of ~6.9-8.9 s
(median bounds).

**Conclusion B:** no systematic offset; zero is a defensible alignment. Lamp
uncertainty = the frame bracket plus <= ~0.2 s observed excess (see
"Offset vs uncertainty").

## C. Synchronisation and reliability window

`tools/diagnostics/review_special_roster_sync.py`, output `special_roster/`
(`sync.md` validation table, `sync.json`, `sheets/` for manual review).

For every special, each of the 8 lamps is read from the nearest known frames
before and after the glyph centre. A slot is `unknown` with
`reason = near_roster_transition` when those disagree or when any of its
lamp-transition brackets lies within X s of the special (`no_lamp_frames` when
a side has no frame within 40 px).

Independent check (lane order): when a slot has a death glyph within 3 s of
the special, the order of the two glyphs on the same lane axis says whether
that slot was alive or dead at the special (ties within 2 px skipped). This
does not use the lamps.

| X (s) | Unknown slot states (of 648) | Specials with both counts known | Using slot read dead | Lane order agree / disagree / lamp unknown |
|---|---|---|---|---|
| 0.0 | 30 | 55 / 81 | 0 | 44 / 0 / 5 |
| 0.25 | 36 | 51 / 81 | 0 | 42 / 0 / 7 |
| 0.5 | 52 | 41 / 81 | 0 | 40 / 0 / 9 |
| 1.0 | 81 | 28 / 81 | 0 | 31 / 0 / 18 |
| 2.0 | 140 | 9 / 81 | 0 | 11 / 0 / 38 |

The using slot's own lamp is never dead at its special, at any X, and no
known lamp state contradicts lane order, at any X (X = 0 included).

**X is not calibrated yet.** The tool's `--window` is a reporting choice
(now required on the command line, no default). The lane-order check barely
reaches the range that would separate candidate windows: the closest known
cells are 0.32 s and 0.64 s from the special; below 0.5 s there is one agreeing
cell, one lamp-unknown cell and two ties. The data therefore does not
contradict X = 0 but cannot distinguish 0 / 0.25 / 0.5. Respawn transitions
have no glyph and are not covered by this check at all.

The error bounds from A and B (special excess <= 0.19 s, lamp excess
<= 0.21 s beyond the frame bracket, outside the clock-49.66 cluster) suggest
the needed margin is small (~0.2 s), but that is an inference, not an observed
sync failure. The deciding evidence is manual review of exactly the cells
that change state between X = 0 and the candidate windows (22 cells between
X = 0 and 0.5), including respawn transitions.

Stratified manual sample (19 specials; 3 per category spread across
recordings; categories counted over all 81: far_from_death 49, after_death 23,
before_death 16, stacked_with_death 15, simultaneous_deaths 13,
obscuring_special_near_death (Killer Wail / Big Bubbler) 9, overtime 1).
Inspected so far (with an X = 0.5 table): S01, S06, S09, S11, S12, S13, S16,
S18, S19, which together cover every category. Every known state matches the
crops; every `?` is a real lamp change within 0.5 s of the special, several of
them conservative (the frame at the special already shows the new state).
S02-S05, S07, S08, S10, S14, S15, S17 remain for reviewer confirmation. The
current `sync.md` / sheets were regenerated with `--window 0`.

## Offset vs uncertainty

These are separate findings and are kept separate:

- **Systematic offset (would shift alignment):** none found. Lamp vs death
  glyph: 81/88 brackets contain 0 and 0 is the single most consistent value
  overall; top 70/77 and bottom 11/11 contain 0; no slot pattern (per-slot
  consistent ranges all include or touch 0; small per-slot n). Glow vs special
  glyph: 56/60 contain 0, consistent [0, +0.15] s. Only 21-23-30 deaths lean
  later (0 fits 17/23, +0.25..+0.40 fits 19/23, each miss is exactly 1 px),
  but the same recording's specials fit 0 (13/13) and the axis-free pixel
  study (`../timing_21-23-30/REPORT.md`) found a median of -1 px, i.e. no
  recording-wide bias. No correction is applied.
- **Transition uncertainty (unknown window, no shift):** the ~1.03 s frame
  bracket plus at most ~0.2 s excess observed in A and B. Handled by
  `unknown` / `near_roster_transition`, with X still to be fixed by review.

## Decision: zero offset frozen

No timing correction. Special glyph time and roster-lamp time stay on the
same zero-offset timeline; the ~1-frame residuals are handled as transition
uncertainty. Special activation timing (A) is considered validated for this
four-recording corpus.

## Next: boundary-case corpus for X

`tools/calibration/review_sync_boundary_sheet.py` (output `boundary/`).
X becomes an output of reviewed visual truth, not a tuned value.

| Corpus | alive->dead | dead->alive |
|---|---|---|
| `flip` (known at X = 0, unknown at X = 0.5) | 13 | 9 |
| `respawn_supplement` (other respawn brackets within 1 s of a special) | - | 29 |

15 of the respawn-supplement cases have the special inside, or on the edge
of, the respawn bracket (distance 0); they cover the closest range. 9 of the 13
flip deaths have no death glyph within 3 s (several sit under a Killer Wail),
so their only basis is the roster frames.

Per case the sheet (`boundary/sheets/Bnn.png`) shows the lane around the
special (special / death-glyph ticks, slot row guides) and the slot's roster
icon in every frame spanning the transition and the special, with a divider at
the special glyph centre. The pipeline's lamp reading is not shown. Labels go
in `boundary/draft_labels.yaml` (save as `reviewed_labels.yaml` with
`status: reviewed`):

```text
reviewed_state_at_special: alive | dead | indeterminate
basis: lane_death_order | roster_frames | both
```

Scoring (`... score --cases boundary/cases.json --labels ...`) compares
reviewed truth with the X = 0 reading by distance to the transition and
reports, per distance bin and transition type, correct / wrong /
indeterminate, plus the largest distance with a wrong or indeterminate case
(the empirical X; 0 if none).

### First review pass (51 cases)

`boundary/reviewed_labels.yaml`, scored in `boundary/score/`: 8 alive,
14 dead, 29 indeterminate. Taken at face value the empirical X would be
0.93 s, but 21 labels conflict with the frames on the sheets: 4 deaths
labelled alive although the X is visible in the frame before the special
(B19, B34, B36, B38), and 17 respawns labelled indeterminate although the
frames on both sides of the special agree (e.g. B01). These 21 are being
re-checked with focused before/after sheets (`boundary/recheck/`) before X is
derived.

### Re-check and final scoring

The 21 conflicting cases were re-labelled from focused before/after sheets
(`boundary/recheck/reviewed_recheck_labels.yaml`); every re-check label now
matches the frames. Merged labels: `boundary/final_labels.yaml` (first-pass
state kept as `first_pass_state`). Score: `boundary/score_final/`.

| Distance to transition (s) | alive->dead | dead->alive |
|---|---|---|
| 0 (special inside / on the bracket) | - | 12 indeterminate, 3 decided by reviewer (X = 0 reading unknown) |
| 0.06-0.1 | 2 correct | - |
| 0.1-0.2 | 2 correct | 1 correct |
| 0.2-0.3 | - | 1 correct |
| 0.3-0.5 | 9 correct | 7 correct |
| 0.5-1.0 | - | 14 correct |

- Every state the X = 0 reading gives (36 cases, closest 0.06 s from the
  transition, death and respawn sides) agrees with reviewed truth; 0 wrong.
- Every indeterminate case is one where the special lies inside the lamp
  bracket, where X = 0 already reports `unknown`.
- B04 / B15: a frame exactly at the special shows the state, so the reviewer
  could decide; X = 0 stays `unknown` there (conservative, not wrong). B16
  is labelled dead but its frame at the special shows alive; a label slip
  (the frame sits above the divider on the sheet). Neither affects X.

**Result: empirical X = 0.** Review resolves roster state at a special
whenever the special is outside the observed lamp-transition bracket; inside
the bracket the state is not decidable, and the existing bracket rule
(`unknown` / `near_roster_transition`) already covers exactly that. No
additional window is justified by the reviewed boundary cases.

Caveat: this is the same four-recording corpus used throughout; the
held-out synchronisation check below must use recordings not seen here.

## Status

| Step | Status |
|---|---|
| A special timing | Validated (no bias; ~1 s bracket) |
| B lamp timing | Validated (no systematic offset; no correction) |
| Zero offset | Frozen (no correction) |
| C synchronisation | Boundary cases reviewed; empirical X = 0 (bracket rule only) |
| Held-out synchronisation check | B + C gate FAIL (two label-protocol failures, no bias; `heldout/sync/REPORT.md`); fresh gate D **PASS** (`heldout2/REPORT.md`, `59415ca`) |
| SPECIAL_USE timing lane | **Frozen** for the validated configuration (zero offset, X = 0, review config sha256 `98c2bf53…5e24`); see `heldout2/REPORT.md` "Decision" |
| D expose to scenarios / CoachInput | Not started; separate assignment; `coach.roster_evidence` stays false |

Open items: the 21-23-30 lamp lag and the clock-49.66 outlier; only one
overtime special exists in this set.
