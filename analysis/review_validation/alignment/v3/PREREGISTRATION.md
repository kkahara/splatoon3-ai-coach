# Review to gameplay alignment v3: preregistered development and held-out gate

This preregistration is committed before production implementation and before
any fresh held-out validation pair is opened or processed. Historical v1/v2
evidence is development evidence only.

## Evidence basis

The frozen alignment contract is
`src/splatoon3_ai_coach/alignment/ALIGNMENT_CONTRACT.md`. Historical records
are the v1/v2 preregistrations and reports under
`analysis/review_validation/alignment/` at commits `a1f447c`, `a66a042`,
`01b0bcf`, `a24b0dd`, and `bdee928`.

v1 failed structural criterion 1 because raw timer misreads caused
`clock_domain_mismatch`. v2 fixed that method issue but failed criterion 2:
`36/55 = 0.655` roster-X intervals contained zero. The v2 diagnosis
establishes a side-assignment failure, not a clock offset: three pairs had
swapped `ally_row` because scenery in the timer-adjacent ROIs was accepted
before roster icons appeared. Correcting side diagnostically restored one
failing pair to `0.83` within one second with median residual `+0.02 s`.
`delta = 0` remains fixed.

## Frozen alignment gate: unchanged verbatim

PASS requires all of:

1. Every held-out pair is `pair_status = consistent` and
   `fit_status = calibrated`.
2. Pooled over all held-out pairs, the share of used roster-X residual
   intervals containing 0 is at least `0.75`.
3. Pooled over all held-out pairs, the median absolute roster-X residual
   midpoint is at most `1.0` s.
4. Every `unknown` mapped death glyph carries a declared mapping reason.

If 1 to 4 hold but fewer than `20` roster-X residuals are used in total, the
verdict is **INCONCLUSIVE** (neither PASS nor FAIL). Any failed criterion is
**FAIL**.

The v3 run retains `delta = 0`, consistent-HUD-edge calibration, association
window `5.0 s`, two-visible-frame persistence, maximum onset bracket `3.0 s`,
one-candidate association, and all existing alignment configuration hashes.
Local DEATH remains reported only. SPECIAL_USE remains forbidden input.

## Separate team-side acceptance gate

Evaluate production side output against an operator-declared ally row used
solely as ground truth. The operator records that declaration at acquisition
and pair-declaration time, before any v3 processing or output inspection. The
declaration is preserved unchanged and sealed in the input manifest. It must
not enter calibration, Review import decisions, alignment fitting,
association, or tuning.

For each pair:

- `declared_side` is the immutable top/bottom ally row recorded before
  processing or output inspection;
- `predicted_side` is production `ally_row`, including `unknown`;
- `known_side` means `predicted_side` is top or bottom;
- `known_side_accuracy = correct known predictions / all known predictions`;
- `known_side_coverage = known predictions / all declared pairs`.

Acceptance thresholds:

- accuracy is exactly `1.00` over known predictions; one wrong known side
  fails the gate;
- coverage is at least `7/8 = 0.875` of declared pairs;
- at least three declared ally rows are top and at least three are bottom;
  both strata are reported separately.

v3 PASS requires both the unchanged alignment gate and this side gate to
PASS. A side-gate failure is FAIL even if alignment metrics pass. No side
label may be changed after opening.

## Fresh held-out set

Use at least eight fresh same-match pairs, with eight as the minimum
evaluation set. Consecutive, protocol-based collection is the primary
selection rule. Do not select or retain pairs by outcome, apparent side
correctness, or recording appearance.

- Each pair contains the own-player live recording and that match's Review
  timeline recording.
- Require reasonable mode diversity and representation of the historically
  high-risk Splat Zones condition, preferably at least two Splat Zones pairs.
- Do not require four Splat Zones pairs if satisfying that target would require
  selecting matches by outcome or selectively retaining recordings.
- At least three declared ally rows are top and at least three are bottom.
  Report the exact side/mode composition.
- Include knockout or overtime matches naturally encountered; do not select
  or exclude on that basis.
- No pair is reused from v1/v2 tuning or held-out corpora, C1–C4, or any
  diagnostic corpus used to choose the fix.

Eight pairs are the minimum defensible size because they double the failed v2
held-out count, permit both side strata and high-risk Splat Zones
representation when naturally available, and make the `7/8` coverage
threshold non-vacuous. This is an engineering acceptance set, not a
population confidence interval. If eight consecutive eligible pairs do not
satisfy the preregistered diversity, Splat Zones representation, and
three-top/three-bottom requirements, continue consecutive collection under
the same rule until they do; do not choose favorable pairs or selectively
retain recordings.

The nine historical v2 tuning pairs and existing calibration/map-ink
diagnostics are development evidence only. The fresh set is untouched
validation and cannot be used for threshold, ROI, feature, side, or
classification decisions.

## Production root fix

In `src/splatoon3_ai_coach/vision/team_color_calibration.py`, accept a
calibration sample only when roster-icon visibility is positively established
in the selected timer-adjacent slot ROIs, reusing validated
roster-visibility/icon-edge evidence rather than relying on `Ready?` timing.
Keep the post-`Ready?`, pre-opening-tick window, separation checks,
corroborating-frame count, and immutable latch unless separately preregistered.
Record calibration frame IDs/times, roster-visibility basis, hue samples,
separation, and accepted-frame count.

In `src/splatoon3_ai_coach/vision/match_side_effects.py`, do not apply
calibration or emit map-ink observations from a frame lacking the roster
gate. In `src/splatoon3_ai_coach/review/team_identity.py`, carry side
provenance and use `unknown` when gameplay calibration is absent, weak, or
not roster-gated. Operator-declared side is evaluation ground truth only,
never a production fallback for v3.

Fail closed: no trustworthy calibration means `ally_row = unknown`,
side-dependent alignment associations are unknown/unusable, and map ink is
disabled or absent for that match. Never guess from scenery, Review row hue
proximity alone, or a held calibration. Missing `Ready?`, stage identity,
roster visibility, or calibration evidence remains a non-error unknown state.

## Development regression gates

- Add tests for early scenery, delayed roster HUD, valid roster-gated latch,
  no-latch, immutable latch, provenance, and map-ink disabling.
- On the historical v2 tuning corpus, compare predicted side with
  operator/development labels without opening the fresh set. No known swapped
  side may remain; unknowns are reported and counted, not relabelled.
- In `src/splatoon3_ai_coach/vision/map_ink.py`, verify that map observations
  occur only after roster-gated calibration, no-calibration matches produce
  none, and ally/opponent fractions agree with the independent roster-color
  reference. Report before/after deltas per sample. Map ink cannot alter
  alignment thresholds.
- A development side or map-ink regression failure stops validation unlock and
  requires an explicit preregistration amendment.

## Top-down research cross-check

If a same-match `top_down_1x` recording exists, run it only as a separately
labelled research cross-check after primary v3 outputs are frozen. It is not
an alignment or side-gate criterion, cannot tune thresholds or side labels,
and cannot rescue FAIL. Report intervals, side agreement, and limitations
separately. Do not introduce top-down processing into the frozen timeline
contract.

## Provenance and exact opening point

Before any held-out file is opened, decoded, imported, thumbnailed, or
inspected:

1. Commit this preregistration, the root-fix implementation, tests, and frozen
   configuration/method hashes.
2. Acquire consecutive pairs under the fixed protocol-based collection rule.
3. At acquisition/pair-declaration time, before v3 processing or output
   inspection, record pair membership, recording protocol, and the operator
   ally-row label. Exclusions are only protocol violations declared before
   processing.
4. Write and commit an input manifest containing the immutable operator side
   declarations, package IDs, artifact identities, source video hashes,
   Review dataset hashes, gameplay analysis/vision-manifest hashes, and exact
   production commit/config hashes.

The held-out set may first be opened only after step 4 is committed. Hashing
for that manifest is the sole permitted pre-opening read. No decode, Review
import, diagnostics, map-ink analysis, side comparison, or frame inspection
is permitted earlier. Capture artifacts remain outside the repository and
read-only.

## Stop conditions

- Any frozen hash, contract, Review evidence, or input-manifest mismatch:
  stop and do not open/process.
- Any development regression failure: stop before validation.
- Any protocol violation not declared before processing: retain the pair; do
  not exclude it.
- Any side-gate failure or unchanged alignment criterion failure: record v3
  FAIL; do not rescue, tag, bridge, or begin Stage 3.
- Fewer than 20 usable roster-X residuals with all other alignment criteria
  satisfied records alignment INCONCLUSIVE; combined v3 cannot PASS.
- Unknown side counts against coverage; it is never silently converted to a
  side.
- Only if both gates PASS may a separate decision consider an alignment-v3
  tag and later Stage 3 work.
