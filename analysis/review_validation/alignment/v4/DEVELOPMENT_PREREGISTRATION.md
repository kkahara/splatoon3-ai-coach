# Alignment v4: development preregistration

This preregistration is committed before any v4 candidate implementation and
before any fresh v4 held-out pair is acquired, opened, or processed. It
defines the development experiment only.

Companion records:

- design alternatives: [`DESIGN.md`](DESIGN.md) at commit
  `9122c0dd5f3a246380c6bddfa53b00a99a142efc`;
- candidate selection: [`CANDIDATE_SELECTION.md`](CANDIDATE_SELECTION.md);
- frozen v3 FAIL: commit `04a4a5b959b6f74d6839d928a2e4acf597d43250`.

## Evidence basis

Frozen contracts and code paths (reference only; unchanged in this phase):

- `src/splatoon3_ai_coach/alignment/ALIGNMENT_CONTRACT.md`
- `src/splatoon3_ai_coach/vision/match_phase.py` (`MatchPhaseFuser`)
- `src/splatoon3_ai_coach/vision/match_side_effects.py` (Ready gate,
  `maybe_calibrate_team_colors`)
- `src/splatoon3_ai_coach/vision/team_color_calibration.py`
- `src/splatoon3_ai_coach/alignment/gameplay_clock.py` (`max_overlap`)
- `src/splatoon3_ai_coach/alignment/review_alignment.py` (`check_pair`)

v3 postmortem mechanisms (diagnostic, not tuning targets):

- pair-010: pre-match presentation frames latched roster-gated colors;
- pair-009: adjacent half-second HUD-edge populations, max-overlap truncation;
- pair-003/004: fail-closed unknown when Ready? or stage/mode missing;
- negative controls 003×004, 004×005, 007×008: structural compatibility
  without independent media-identity evidence.

## Track A — phase-fused calibration validity (Candidate A1)

### Causal hypothesis under test

Roster visibility plus Ready? window timing is insufficient to establish that
timer-adjacent hue samples represent in-match team-color state. Requiring fused
`match_phase == in_match` before any calibration sample may be accepted should
reject pair-010-like presentation evidence while preserving fail-closed
behavior for pair-003/004-like missing-evidence cases.

### Precise semantics (frozen before implementation)

**Match phase evidence.** Use the existing fused match phase produced by the
vision pipeline (`MatchPhaseFuser` in `vision/match_phase.py`), driven by
usable timer readings and existing lifecycle configuration. Do not re-derive
match phase inside the calibrator or from environment-specific cues.

**Required `in_match` transition.** A frame is phase-eligible for calibration
sampling only when fused `match_phase` is `"in_match"` at that frame's video
time. The transition into `"in_match"` follows current fuser rules: the first
timer observation whose `seconds_remaining` is **not** an opening frozen value
in `LifecycleFusionConfig.opening_clock_seconds` (default `(180, 300)`)
invokes `_enter_in_match`, setting `saw_in_match` and phase `"in_match"`.
Frames while phase is `"opening_countdown"`, `"intro"`, or `"out_of_match"`
are ineligible even if roster icons are visible.

**Non-opening timer transition.** Means any usable timer reading whose
`seconds_remaining` ∉ `opening_clock_seconds`. This is the same predicate the
fuser already uses to leave opening countdown; it is not a new tolerance or
pair-specific rule.

**Roster visibility (unchanged from v3).** Positive `PlayerCountReading`
with `roster_visible=True` on the frame, recorded as
`roster_visibility_basis="player_count.roster_visible"`. No roster evidence
means the frame cannot contribute.

**Ready? participation (narrowed role).** Keep the existing Ready? search
window in `match_side_effects.py`:

- `ready_seen` must be true before calibration sampling begins;
- sampling stops after `opening_clock_ticked` as today;
- finalize still drops colors/map ink when Ready? never appears.

Ready? **does not** satisfy semantic validity alone. A Ready? true reading
with low template confidence on a presentation frame must not bypass the
phase-fused gate. Ready? defines when the search window is open; phase fusion
defines which frames inside that window may latch color.

**Color sampling and latch (unchanged knobs unless amended).** Within the
intersection of (Ready? window open) and (phase-eligible), apply existing
`TeamColorCalibrator` rules unchanged in this preregistration:

- timer-adjacent ally-4 / opponent-1 slot geometry;
- `MapInkAnalyzerConfig` window, `min_accepted_frames` (default 3),
  `min_hue_separation`, S/V gates, within-side spread;
- immutable latch after success.

**When color calibration may latch.** Only after at least
`min_accepted_frames` phase-eligible, roster-visible frames pass pixel and
separation checks. The latch timestamp is the last accepted frame time among
those phase-eligible frames.

**Unknown and fail-closed behavior.**

- No phase-eligible accepted frames → no calibration; `ally_row = unknown`;
- missing stage identity → map ink disabled as today;
- opening clock ticks without Ready? → existing drop path;
- never infer side from Review row hue alone or operator declaration.

**Map ink.** Unchanged contract: observations only after roster-gated
calibration latched; no calibration → no map ink for that match.

**Provenance extensions (required in implementation).** For each accepted
calibration frame, persist fused `match_phase`, whether the frame was
opening-countdown or pre-`in_match`, and explicit rejection reasons for
phase-ineligible attempts. Operator-declared ally row remains evaluation-only.

### Escalation to Candidate A2

If A1 passes regression fixtures but development shows residual
presentation-sensitive latches (color label jumps across accepted frames),
authorize a separate amendment to add A2 multi-frame semantic latch **without**
removing the A1 phase gate. A2 is not part of the initial implementation
authorization.

### Track A development data

| Corpus | Role | May tune thresholds? |
|---|---|---|
| Seven recoverable historical v2 replays (`18-18-00`, …, `22-57-30`) | Side consistency diagnostic (no operator GT) | No |
| Committed v3 derived artifacts for pair-010, 003, 004 | Mechanism regression fixtures | No |
| New synthetic/unit fixtures (presentation + roster visible, delayed in_match) | Positive/negative control | N/A |
| Fresh v4 held-out (future) | Generalization only | No |

Historical replays do not provide immutable operator ally-row labels; report
historical-side consistency only, never as formal accuracy.

### Track A development metrics

Report per recording/fixture:

- phase-ineligible attempts vs accepted frames;
- calibration latched (yes/no) and latch time;
- predicted `ally_row` or `unknown`;
- map-ink sample count;
- whether pair-010-like presentation sequence would have been rejected.

### Track A development success (required to keep A1 for production path)

All must hold on the declared development corpus before Track B implementation
competes:

1. **pair-010 regression:** presentation/transition evidence is rejected as
   authoritative calibration evidence, while later calibration from
   independently qualifying `in_match` evidence remains permitted. If no such
   evidence appears, the result remains fail-closed. Apply A1 rules to the
   committed v3 pair-010 evidence (frame_results / match_identity /
   postmortem chain).
2. **pair-003/004 regression:** remain `unknown` when Ready? or stage/mode
   evidence is absent; no fail-open latch from diagnostic bypass paths.
3. **Historical seven replays:** historical-side consistency `7/7` or
   documented unknown with fail-closed semantics; no new wrong-side swaps
   relative to persisted v2 side output.
4. **Map ink:** no observations without latched calibration; no regression
   on the seven-replay map-ink comparison method from v3 Amendment 1.
5. **Tests:** targeted team-color suite passes; new phase-gate tests pass.

Failure of any criterion stops escalation to fresh held-out work until an
amendment selects A2/A3 or revises semantics.

### Track A tests (to add at implementation)

- presentation-frame roster visible + Ready? low confidence → no latch;
- opening_countdown timer frozen values → no latch;
- first non-opening tick → phase `in_match` → latch allowed when colors valid;
- missing Ready? → unknown end state;
- missing stage → map ink disabled;
- provenance includes phase at accepted frames;
- immutable latch after success.

Existing suite anchor: `tests/test_vision_team_color_calibration.py`.

## Track B — clock population selection comparison (B1 vs B2)

### Causal hypothesis under test

Adjacent half-second edge populations can make maximum-overlap selection
choose a shorter consistent span, producing false `clock_domain_mismatch`.
B1 and B2 must discriminate populations on continuity and robust fit without
pair-specific tolerances.

### Candidates under comparison

**B1 — continuity-constrained population model.** Partition HUD edges into
candidate `c` populations; score by interval overlap, temporal continuity,
monotonic remaining coverage, and regulation-span support; abstain when
multiple populations remain comparably supported.

**B2 — robust latent clock fit.** Single monotonic `video_t = c - remaining`
with preregistered bounded edge jitter; outliers excluded only under fixed
robust-loss and continuity rules with logged reasons.

**Not in initial comparison:** B3 endpoint-anchored span (reserved if B1 and B2
both fail development criteria).

### Frozen comparison protocol (before inspecting outcomes)

Apply **identical** fixture sets to both candidates:

1. Existing synthetic/manifest fixtures in `tests/test_alignment.py`;
2. New synthetic fixtures: half-second adjacent populations (pair-009-like
   **structure**, not tuned to v3 pair-009 bytes);
3. True discontinuity, sparse edges, overtime/knockout unknown cases;
4. v1/v2 tuning-derived clock edge exports where already committed under
   `analysis/review_validation/alignment/` (read-only).

**Per-fixture metrics (both candidates):**

- `fit_status`, `reason`, `c_interval`;
- `consistent_remaining` span top vs Review regulation label when provided;
- `inconsistent_edge_share`;
- whether `clock_domain_mismatch` would be asserted in `check_pair`;
- ambiguous-fit flag (explicit abstention);
- list of excluded edges (B2) or rejected populations (B1).

**Absolute qualification criteria (fixed before comparison):**

- A candidate must calibrate every non-ambiguous reference fixture without
  pair-specific slack.
- A candidate must return `unknown` for true discontinuity, insufficient
  evidence, and unresolved competing fits.
- A candidate must not produce a confident fit when its continuity/domain
  evidence is inadequate.

**Selection rule (fixed now):**

1. Eliminate any candidate that fails an absolute qualification criterion.
2. If both candidates qualify, prefer higher regulation-span coverage on
   pair-009-**like**
   adjacent-population fixtures (span top closer to true regulation endpoint
   in the fixture definition).
3. If still tied, prefer lower `inconsistent_edge_share` on those fixtures.
4. If neither candidate qualifies, select **neither** and require amendment
   before either enters production.
5. If both qualify without a material difference, use the preregistered
   tie/simplicity rule: choose the simpler candidate only if simplicity was
   independently documented before comparison; otherwise select **neither** and
   require amendment.

**Forbidden:** choosing B1 or B2 because it reproduces v3 pair-009 pass/fail;
adjusting jitter, population penalties, or overlap weights after seeing v3
held-out aggregate.

### Track B development success

- Pair-009-like fixtures: select the supported population when the
  preregistered continuity/domain evidence is sufficient; otherwise return
  ambiguous/`unknown`, never a confident fit from inadequately supported
  evidence.
- v1/v2 reference fixtures: no regression vs documented tuning expectations
  except where this preregistration explicitly documents a intended fix.
- Unit tests pin population scoring, ties, discontinuity, and abstention.

Existing suite anchor: `tests/test_alignment.py`,
`tests/test_review_validation.py`.

## Track C — integrity and identity reporting (not core gate change)

### Independent evidence dimensions (required in future reporting)

| Dimension | Support/pass | Contradiction/fail | Unknown/not available |
|---|---|---|---|
| `provenance_integrity` | Declared pair membership, package/review-code binding, and sealed artifact hashes are consistent | Binding or artifact identity conflicts | Required provenance is missing |
| `empirical_media_identity` | Independent media-derived evidence supports the same-match claim | Independent evidence contradicts the same-match claim | Evidence is insufficient, unavailable, or ambiguous |
| `structural_compatibility` | Existing mode/overtime/coverage/clock-domain checks are compatible | Existing structural checks report incompatibility | Required structural inputs are missing or unusable |

**Rules:**

- Intact provenance **does not** imply same-match identity.
- Missing provenance → `provenance_integrity = unknown`; not automatic reject.
- Same-mode, clock-compatible unrelated pairs (007×008 class) may remain
  `structural_compatibility = support` while
  `empirical_media_identity = unknown`.
- Missing gameplay mode/stage metadata → explicit unknown/unusable for identity
  fingerprints; never silently consistent.

### Track C development scope for v4 initial release

Implement **reporting-only** C1 checks in development tooling or extended
alignment metadata; do **not** add C2/C3 to PASS/FAIL alignment gate until a
separate amendment and development study demonstrates discrimination value.

Development corpus for C (labels from operator-declared pairing, not v3 gate):

- true same-match pairs from tuning/historical sets;
- deliberate cross-pair controls (003×004, 007×008 class);
- missing mode metadata cases;
- intact provenance with wrong declared pairing (simulated hash/package mismatch).

Metrics: false acceptance, false rejection, and unknown rates **reported
separately** for provenance vs empirical identity.

## Cross-track development regression fixtures (v3 postmortem)

These committed v3 artifacts may be used as **regression fixtures** only:

| Fixture | Mechanism tested | Success expectation under development |
|---|---|---|
| pair-010 | A: phase validity | Presentation evidence rejected as authoritative; later independently qualifying in-match evidence remains usable |
| pair-009 | B: clock populations | Correct span or abstain on replay of edges |
| pair-003, pair-004 | A: fail-closed | Remain unknown without Ready?/identity |
| 003×004, 004×005, 007×008 controls | C: reporting | Structural vs identity statuses documented |

Do not re-run the v3 gate to convert FAIL to PASS. Do not use the v3 ten-pair
aggregate as Track A/B/C selection evidence.

## No-adaptation rules during development

After development implementation begins:

- do not change thresholds, ROIs, or gate criteria based on v3 held-out
  aggregate results;
- do not optimize candidate parameters, thresholds, tolerances, weights, or
  selection rules against aggregate performance across the ten v3 pairs;
- do not open or acquire fresh v4 held-out media during development;
- do not substitute pairs or operator declarations;
- if a bug is found, preserve evidence and stop; fix only under a new
  amendment with frozen hashes before continuing.

## Future v4 held-out validation (not authorized here)

When separately authorized, v4 validation will require:

- new consecutive same-match pairs (minimum eight) with immutable operator
  ally-row declarations sealed before opening;
- frozen v4 implementation and config hashes recorded in a v4 input manifest;
- alignment and side gates at least as strict as v3 unless a future validation
  preregistration amendment documents a change before acquisition;
- one mechanical processing run; no post-hoc pair exclusion or threshold
  edits.

The v3 ten-pair set remains excluded from that fresh set.

## Implementation authorization boundary

This commit authorizes **documentation only**. The next authorization after
review is:

1. implement Track A1 per this document;
2. implement Track B1 and B2 behind a comparison harness;
3. implement Track C1 reporting fields only;
4. run the development experiment and report metrics;
5. amend or freeze winners before any fresh held-out acquisition.

## Amendment procedure

Any change to candidate choice, comparison selection rules, success criteria,
thresholds, fixture inclusion, or gate semantics requires a committed amendment
to this file **before** the affected development or validation work continues.
Amendments must state what was frozen, what changed, and why, without altering
v3 results or the sealed v3 input manifest.

## Stop conditions

- v3 artifacts, gates, or reports modified: stop;
- fresh held-out media opened without separate authorization: stop;
- development success criteria changed after inspecting v3 aggregate: stop;
- Track C provenance conflated with empirical identity: stop;
- winner selected for Track B without the preregistered comparison rule: stop.
