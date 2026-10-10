# Held-out batch D: SPECIAL_USE timing gate

## Gate D criteria (written before labelling)

Written and committed before any compact sheet of batch D was opened and before
any label exists. Batch D is a **single confirmatory gate** of the unchanged
system (zero synchronisation offset, X = 0). It is not an opportunity to choose an
offset, threshold, template, scale or dedupe setting: X = 0 either survives this
gate or it does not. Batches B and C remain sealed and are not part of D.

### 1. Frozen system

- `configs/default.yaml` sha256 `3ef4b496441774aee840880c4ff3ce6e2bb6c02980f1b253fc36e8a2400e503f`
  (identical to the B/C gate run).
- `review` block sha256 (canonical JSON)
  `98c2bf537caee518ce609812eea509e53fea1e7f4239a72120631e4230325e24`, identical
  to B/C. A different value at gate time makes the gate invalid; the run stops.
- No commit touched `src/`, `configs/` or `calibration/` since `4b8ad88`.
- Gate tooling: `tools/calibration/review_sync_boundary_sheet.py` at `f4872da`
  (adds `recheck --compact`, decoy-mixed rechecks and an exact recheck-coverage
  check; tests show they change no case selection, X = 0 state, label or gate
  arithmetic, and the sealed B/C gate recomputes byte-identically).

### 2. Frozen corpus

Recordings (`~/Movies/en-preview-timeline-2026-10-10 <id>.mov`), imported with
the default config into `heldout2/<id>/`:

| Recording | Video sha256 | review_timeline.json sha256 | Samples | Specials | Deaths | s/px | Cases | Challenge |
|---|---|---|---|---|---|---|---|---|
| 09-53-27 | `14f9f49b…2b991` | `8dd355ae…09b14` | 931 | 31 | 29 | 0.214 | 21 | 10 |
| 09-54-34 | `3fdb8032…e6e15` | `fa156763…0530d` | 896 | 27 | 58 | 0.196 | 30 | 19 |
| 09-55-33 | `e123f950…52d3c` | `d330c0cb…b8a28` | 666 | 15 | 29 | 0.146 | 9 | 2 |
| 09-56-35 | `540c8bb9…44eb7` | `74ba208c…661c6` | 864 | 25 | 44 | 0.189 | 18 | 7 |
| 09-57-43 | `1525cb2b…732f1` | `9bafaa24…286b3` | 555 | 16 | 13 | 0.126 | 10 | 5 |
| 09-59-27 | `ca71926f…7ce15` | `5315586b…f4ec9` | 870 | 30 | 52 | 0.189 | 20 | 13 |

Full hashes: video
`14f9f49be30985b54f797803cf559db3a2d065f25c751bdb861b360e10b32991`,
`3fdb803288b08eebc8f147fcaa2fdfe51823190b7afd885cd11e7edbc48c6e15`,
`e123f950cf3d16c41e2b832eeda09e31613e6f850d7888a436287d1db0952d3c`,
`540c8bb9bd62a04a635a38314bd02244b99791b8ecd65686b4fde29038544eb7`,
`1525cb2bf7bc29b6d9b03554be661cee71908ba8cb8136cdf8d181e2784732f1`,
`ca71926f196a024f855cf8084402f4c15f0801fe7c6f684a559c6256e837ce15`;
import
`8dd355ae5724bfd654ae729190489a053631a4ba94692a158905c0453b109b14`,
`fa156763241694f05664cdc13ef62fb2da18732ce20049aff54cd53fe100530d`,
`d330c0cbb50e4d37cd2ff9040c64e8b5c35b91cc160e9b98ca1166a5b6bb8a28`,
`74ba208c8e8b243778b733cc9f1f86b51a3d13bd1f3ffbbb06f17decbfd661c6`,
`9bafaa24ac279b4142a7106020f5dde406caa6e3e4ee3241f6c1167f209286b3`,
`5315586bacedd7bb70853bc5485733e81c2bf385112d74da0eea9923757f4ec9`
(same order).

Freshness: ordered special sequences compared with all 45 earlier imports and
with each other; highest similarity 0.18 (re-imports of one match score about
1.0). No recording excluded.

Corpus (`boundary/`, `sheet --id-prefix D --compact`, flip window 0.5 s, respawn
supplement 1.0 s, unchanged rules):

- Cases `D01`-`D108` (108): flip 28 dead->alive + 26 alive->dead, respawn
  supplement 54.
- X = 0 gives a state: 77; in-bracket `unknown`: 31.
- **Challenge cases** (X = 0 state, <= 0.6 s from a transition): **56**
  (target >= 40 met), 23 within 0.25 s.
- `boundary/cases.json` sha256
  `06b46292303cb56a9733da9872c53a260a16a386a82c76e489fd18cdca4f52f0`.
- Compact sheets: 108 JPEGs, per-file hashes in `boundary/sheets_manifest.txt`;
  manifest sha256 `8de68f9859e9cd0632a102252eca67243221d4dd1324554ae5275f03e67b5613`.
- No case is added, removed or reselected after this commit.

### 3. Labelling rule (AT frame)

Label file: `boundary/labels.yaml` (template sha256
`c8c200188806399c94f93c24054c1a25188378d3229948d4fdc58b188518d06a`). It lists
only case ID, recording and slot; the generated `draft_labels.yaml` was removed
because it showed the pipeline's transition type and bracket. Sheets show only
the slot icon in the BEFORE / AT / AFTER frames with `dx` (px from the special
glyph centre), `dt` and clock; never the lamp reading, X = 0 state or
transition.

Question (also in the label file): the state of the yellow-box slot **at the
special**:

1. **AT frame decides.** If an AT frame is shown (nearest frame with
   |dx| <= 0.5 px, `AT_PX`; at most 0.107 s at D's 0.126-0.214 s/px), its icon
   is the label: full-colour icon with weapon = `alive`; X over the icon =
   `dead`. Basis `at_frame`.
2. **Otherwise BEFORE / AFTER agreement.** With no AT frame, if the BEFORE and
   AFTER icons are legible and show the same state, label that state. Basis
   `before_after_agree`.
3. **Otherwise `indeterminate`.** BEFORE and AFTER disagree with no AT frame, or
   the deciding icon is illegible, blank, occluded or a disconnected silhouette.
   Basis `rule_3_indeterminate`.

Alive and dead both appearing on the sheet is **not** by itself grounds for
`indeterminate`. No other frame counts as AT, however close it looks. The
reviewer uses only the sheet.

### 4. Scoring and treatment of `indeterminate`

Copied from the B/C pre-registration (`heldout/sync/REPORT.md`, `8828b46`),
unchanged:

- **Timing failure:** X = 0 gives a state (`alive` / `dead`) and the final label
  is the opposite state **or `indeterminate`**. An `indeterminate` label on an
  X = 0-known case is always a timing failure (direction `none`).
- **Not a failure:** X = 0 reports `unknown` (any label, including
  `indeterminate`); this is expected boundary uncertainty.
- **Non-timing failure:** only cases listed in `gate/known_failures.yaml`.
- **Direction:** `lag` (X = 0 before-state, label after-state, bracket after
  the special); `lead` (X = 0 after-state, label before-state, bracket before);
  otherwise `none`.
- **Systematic bias:** >= 3 timing failures and >= 80% in one of lag / lead.
- **PASS:** (1) zero timing failures after the recheck merge, and (2) zero
  timing failures among timing-attributable cases with a determinable label
  (redundant with 1; reported, not a separate statistic).
- **FAIL with bias:** no tuning on D; a correction hypothesis only, validated on
  yet another fresh set. **FAIL without bias:** implementation unchanged.

### 5. Disagreement handling (blind recheck with decoys)

- **Targets:** exactly the raw timing failures (X = 0 state and raw label
  opposite or `indeterminate`, known failures excluded); `gate` refuses to run
  unless the recheck covers exactly this set. No recheck if there are none.
- **Decoys:** as many cases whose raw label equals the X = 0 state, drawn with
  seed **20261010** (`recheck --decoy-seed 20261010`), shuffled together with the
  targets and renamed `R01`, `R02`, ... The reviewer sees only the compact
  BEFORE / AT / AFTER sheet and the same rule text; never the case ID, raw
  label, X = 0 state, why a sheet was chosen, or whether it is a target.
  `recheck_key.json` (mapping) is not opened by the reviewer.
- **Merge:** for targets the recheck label is the final label; for all other
  cases the raw label is final. Raw and recheck labels are both kept
  (`raw_reviewed`, `recheck_reviewed`, `label_source`). The gate uses the
  merged final labels; raw results are reported alongside.
- **Decoy labels are never scored.** They are reported as a recheck
  consistency check (how many decoys changed label).

### 6. Known failures (disconnects)

Checked before any sheet was viewed, from lamp-state statistics (long
single-slot `unknown` runs) and end-of-match roster crops
(`disconnect_check_all.png`, `disconnect_check_09-54-34_bottom.png`):

- 09-54-34 `bottom_1` disconnects: last visible icon x 1302, silhouette from
  x 1307 to the end (a stray `alive` lamp read at x 1500).
- D cases on that slot: D39 (glyph x 1237.5) and D45 (x 1082.5), both before
  the disconnect. No case falls after it.
- `gate/known_failures.yaml`: **empty**. Nothing is added after labels are
  seen; any other detector, label or Review-cursor problem is reported as
  post-gate diagnosis and leaves the mechanical result unchanged.

### 7. Run order

1. User labels `boundary/labels.yaml` from `boundary/sheets_compact/` only,
   sets `status: reviewed`.
2. `recheck heldout2/<ids> --cases boundary/cases.json --labels boundary/labels.yaml --known-failures gate/known_failures.yaml --compact --decoy-seed 20261010 --out recheck`.
3. User labels `recheck/recheck_labels.yaml` (neutral IDs), `status: reviewed`.
4. `gate --batch boundary/cases.json boundary/labels.yaml --recheck recheck/recheck_labels.yaml --recheck-key recheck/recheck_key.json --known-failures gate/known_failures.yaml --preregistration-sha <this commit> --out gate`
   (without `--recheck` / `--recheck-key` if there are no targets).
5. Commit `gate.json` / `gate.md` before inspecting any case; then diagnose,
   report, and freeze only on PASS.

## Final gate D

Mechanical result: `gate/gate.json`, `gate/gate.md`, committed `59415ca` before
any case was inspected.

| Provenance | |
|---|---|
| `preregistration_git_sha` | `878696e22e8bda15b41e9ad3f3bfcbf65b26949d` |
| Labels committed (as submitted) | `6d9e926` |
| `gate_run_git_sha` | `6d9e926e77bb2aadc40ccba244296f4765bb5fb6` (tool / config clean) |
| `configs/default.yaml` sha256 | `3ef4b496441774aee840880c4ff3ce6e2bb6c02980f1b253fc36e8a2400e503f` |
| `review` block sha256 | `98c2bf537caee518ce609812eea509e53fea1e7f4239a72120631e4230325e24` (= B/C) |

**Verdict: PASS** (`no_timing_failures`).

### Labels and score (108 cases, one blind pass)

- Labels: 49 alive / 36 dead / 23 indeterminate. Basis: `at_frame` 22,
  `before_after_agree` 63, `rule_3_indeterminate` 23. A format check before the
  gate confirmed every `at_frame` basis sits on a sheet with an AT frame and
  every `before_after_agree` on one without; two basis / state inconsistencies
  (D57, D106) were corrected by the reviewer before the labels were committed.
- X = 0 gives a state: 77 cases, **77 correct, 0 wrong, 0 indeterminate**.
  Challenge cases (<= 0.6 s from a transition): **56 / 56 correct**; within
  0.25 s: **23 / 23**.
- X = 0 `unknown` (all 31 in bracket, none out of bracket): labels 23
  indeterminate, 7 alive, 1 dead (decided by the reviewer, mostly via AT
  frames; X = 0 stays conservatively `unknown`).
- Every `indeterminate` label is on an in-bracket case.

### Recheck

Zero raw timing failures, so zero recheck targets and no decoys
(`recheck --decoy-seed 20261010` wrote an empty `recheck/`); the gate ran
without `--recheck`, as pre-registered. Raw and final labels are identical.

### Gate conditions

1. Timing failures: **0**.
2. Timing failures among 77 timing-attributable cases with an X = 0 state:
   **0** (redundant with 1). Timing-attributable empirical X: **0.0 s**.

Bias test: 0 timing failures (lag 0, lead 0, none 0); no systematic bias.
Known failures: none pre-registered; none triggered. Non-timing failures: 0.

### Failures

None, timing or non-timing; nothing to diagnose.

### Observed defects (outside this gate)

- Disconnected-slot false-alive read, now seen on three recordings: 06-43-43
  `bottom_2`, 07-54-48 `top_4` (held-out B/C) and 09-54-34 `bottom_1` (D; a
  stray `alive` lamp on the silhouette at x 1500). No D case fell on the
  disconnected stretch, so the gate did not exercise it. Unfixed; design on
  the tuning set, re-check on fresh data.

### Known precision limitations

- Roster lamps about 1 px (0.19 s) late on 21-23-30 and 07-54-48; zero offset
  remains the best fit. Not corrected.
- Glow-end timing fits +0.10 s marginally better than 0 s on B/C (77 vs 76
  brackets), within glyph localisation. Descriptive only.

### Validation coverage gaps

- This gate validates synchronisation / timing of roster state at Review
  specials. Special **identity** on held-out recordings is still unverified
  (no special labels for B, C or D).
- Five special types never label-confirmed (reefslider, splattercolor_screen,
  super_chump, tenta_missiles, zipcaster); Big Bubbler and Trizooka had no
  B/C held-out detections. Coverage gaps, not failures.

## Decision

**Decision: SPECIAL_USE synchronization/timing lane frozen for the validated
configuration.**

Frozen (validated by gate D, `59415ca`):

- Synchronisation offset between special glyph and roster lamps: **0** (no
  correction).
- Reliability window **X = 0**: the slot state at a special is the agreeing
  state of the last lamp frame at x <= glyph centre and the first frame after
  it; otherwise `unknown`.
- Special glyph timestamp: `event_timestamp_anchor: center`.
- Special templates: `special_template_dir: ../calibration/templates/review/specials`
  (22 files, manifest sha256
  `c3fd73b160a6ab2678a5214699c29adc5460e46b363da9b9c1850550dd3fa590`, last
  changed `4b8ad88`); `event_glyph_representation: grayscale`.
- Scale: `special_template_scales: [1.0]`.
- Matching: `special_discovery_floor: 0.45`, `special_match_floor: 0.5`,
  `special_match_margin: 0.15`, `event_glyph_min_std: 0.04`.
- Dedupe: `special_deduplication_tolerance: 0.006`,
  `cursor_exclusion_half_width: 0.004`.
- Whole `review` block sha256 `98c2bf53…5e24`; `configs/default.yaml` sha256
  `3ef4b496…e503f`; code at `6d9e926` (no `src/` change since `4b8ad88`).

The freeze covers the validated synchronisation / timing configuration only.
It does not claim special identity or every SPECIAL_USE detection is correct,
and a future change to any frozen value requires a new held-out gate. The
disconnected-slot defect, the lamp precision limitation and the coverage gaps
above stay open. `coach.roster_evidence` remains `false`; scenario /
CoachInput integration is a separate assignment.
