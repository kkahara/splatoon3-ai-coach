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

## Amendment 1 — Track B exact parameters (2026-10-24)

### What was frozen, what changes, why

Frozen at `ad9edfa`: the Track B hypothesis, the two candidates, the four
fixture classes, the per-fixture metrics, the three absolute qualification
criteria, the five-step selection rule and the forbidden list. They are not
changed.

What changes: the above did not determine an implementation. Development
implementation stopped before any B1/B2 code was written or run. This
amendment freezes every remaining parameter, algorithm step, fixture,
expected label and tie-break. Where it is more specific than the original
Track B text, it controls. Nothing in Track A or Track C changes. The
uncommitted Track A1 working tree is not part of this amendment.

Why: the preregistration requires a committed amendment before affected work
continues. Without one, B1/B2 parameters would be chosen during
implementation.

### State at amendment

- No B1 or B2 code exists. No Track B comparison fixture has been generated
  or run. No candidate outcome has been inspected.
- No fresh held-out media has been acquired, opened, decoded or processed.
  The v3 gate has not been rerun.
- Files read to write this amendment:
  - frozen code and configuration (`gameplay_clock.py`,
    `review_alignment.py`, `config/models.py`, `configs/default.yaml`,
    `coach/game_clock.py`);
  - the alignment contract, v1 `TUNING.md`, v2 `TUNING.md` and the v3
    postmortem.
- Only file hashes and the `gameplay_manifest_sha256` field were read from
  the v2 tuning outputs and the pair manifests listed below. No edges or
  fits were read from them.

### Provenance categories

Every value below cites one category, in priority order:

1. already-frozen existing behavior (v3 code/config/contract);
2. pre-v4 development evidence or convention (v1/v2 tuning records);
3. mathematical or representational consequence of category 1–2 values;
4. deterministic candidate definition (no free numeric parameter);
5. new fixed design choice (justified in place).

No value was chosen because it is expected to resolve pair-009 or any other
v3 pair.

### Notation

- `Δ`: cadence period. `Δ = 1 / vision.hud_cadence_fps = 1 / 2.0 = 0.5 s`.
  Category 1 (`configs/default.yaml`) plus category 3.
- Edge `e`: a `ClockEdge` from frozen `hud_edges`, carrying `t_a < t_b` and
  `remaining = k - 1`. Its raw constraint is the half-open interval
  `I_e = (lo_e, hi_e] = (t_a + remaining, t_b + remaining]` (category 1).
- `J`: jitter half-width, `J = Δ / 2 = 0.25 s` (category 3). Derivation:
  - The contract and v1 `TUNING.md` state that a clean edge interval is one
    cadence period wide, and that this width is the calibration precision.
  - Consider a latent offset that varies only within a band of width `Δ`,
    which is below that stated precision.
  - Every edge interval contains its own true `c` inside that band, so every
    interval widened by `Δ/2` contains the band's center.
  - No smaller widening guarantees this. `J = Δ/2` is the smallest value that
    keeps the model at the precision v3 already claims.
- Widened interval: `W_e = [lo_e - J, hi_e + J]` (closed).
- `G`: continuity gap, `G = alignment.max_edge_gap_seconds = 10.0 s`
  (category 1). It is the gap the frozen Layer 2 bracketing already uses.
- `ρ`: maximum excluded share,
  `ρ = alignment.max_inconsistent_edge_share = 0.1` (category 1).
- `ε = 1e-9`: tolerance on floating-point endpoint comparisons only
  (category 3; binary representation of decimal timestamps). It is not a
  model parameter.
- Deterministic edge order: by `(t_b, t_a, remaining)` ascending
  (category 4).

### Shared preprocessing (identical for B1 and B2; all category 1)

| # | Decision | Frozen value | Source |
|---|---|---|---|
| S1 | Input reads | `build_game_clock` over cadence frames before `MATCH_END`, `min_usable_confidence = vision.timer.min_usable_confidence = 0.50` | `calibrate_gameplay_clock`, `coach/game_clock.py`, `configs/default.yaml` |
| S2 | Edges | frozen `hud_edges` (consecutive reads, drop of exactly 1, `t_b > t_a`) | `gameplay_clock.py` |
| S3 | Minimum evidence | `len(edges) < alignment.min_edges (20)` → `unknown`, `insufficient_gameplay_clock` | `calibrate_from_clock` |
| S4 | Playback rate | frozen `estimate_playback_clock` with `coach.playback_clock`; `INSUFFICIENT_CLOCK_EVIDENCE` → `insufficient_gameplay_clock`; `RATE_MISMATCH` → `inconsistent_gameplay_clock` | `calibrate_from_clock` |
| S5 | Diagnostics | `edge_count`, `raw_edge_remaining_span`, `lowest_hud_read`, `highest_hud_read`, playback fields computed exactly as v3 | `calibrate_from_clock` |
| S6 | Output schema | `GameplayClockCalibration`, unchanged; candidate diagnostics live only in the development harness output | contract |
| S7 | Slope | fixed −1 (`video_t = c - remaining`); no slope fit | contract |
| S8 | Overtime | no special rule; the OVERTIME banner yields no usable reads, so no edges; Layer 2 owns overtime status | `match_end.py`, `check_pair` |
| S9 | Knockout / partial match | no special rule, no extrapolation; edges cover what was observed | contract |
| S10 | Sparse reads | no special rule; missing frames widen edge intervals naturally; insufficiency handled by S3/S4 | `hud_edges` |

### Shared post-selection rules (identical for B1 and B2)

| # | Decision | Frozen value | Category |
|---|---|---|---|
| P1 | Duplicate remaining | If two or more selected edges have the same `remaining`, all of them are excluded with reason `duplicate_remaining`. | 3 + 5. A true monotonic clock produces exactly one `k → k-1` edge per `k`, so at least one duplicate is spurious, and nothing identifies which. Excluding all is the fail-closed choice. |
| P2 | Monotonicity | After P1, the selected edges in edge order must have strictly decreasing `remaining`; otherwise `unknown`, `inconsistent_gameplay_clock`, detail `non_monotonic`. | 3 (`remaining` decreases as video time increases under slope −1) |
| P3 | Excluded share | `share = 1 - |selected after P1| / len(edges)`; `share > ρ` → `unknown`, `inconsistent_gameplay_clock`, detail `excess_excluded` | 1 |
| P4 | `c_interval` | If the raw intersection over selected edges is non-empty (`max lo_e < min hi_e`), return `(max lo_e, min hi_e)`, the v3 half-open semantics. Otherwise return the closed widened intersection `(max lo_e - J, min hi_e + J)`. | 1 + 3. This keeps v3 precision whenever the strict model holds and widens only by the representational bound. |
| P5 | `consistent_remaining` | sorted `remaining` of the selected edges after P1 | 1 |
| P6 | `consistent_edge_count`, `inconsistent_edge_share` | from P1/P3, rounded to 4 places as v3 | 1 |
| P7 | Excluded-edge log | every non-selected edge listed with one reason from a closed vocabulary (below) | 4 |
| P8 | Order of checks | S3/S4 → candidate selection → candidate tie → P1 → P2 → P3 → P4; first failure wins | 4 |

### B1 — continuity-constrained population model (all decisions)

| # | Decision | Frozen value | Category |
|---|---|---|---|
| B1.1 | Population representation | Distinct maximal member sets from the frozen `max_overlap` sweep, using half-open `I_e`. Each sweep point `p` (each distinct `hi_e`) defines the set `{e : lo_e < p ≤ hi_e}`. Keep only sets that are not a subset of another set. | 1 + 4 |
| B1.2 | Clustering / membership boundary | exactly B1.1; no distance threshold, no k-means, no merging rule | 1 + 4 |
| B1.3 | Overlap between populations | An edge may belong to several populations (shared members, for example transitional edges after a missing frame). | 4 |
| B1.4 | Temporal succession | For consecutive populations `P, Q` in a chain, let `A = P \ Q` and `B = Q \ P`. Require `max t_b(A) < min t_b(B)`. Distinct maximal sets make both non-empty. | 4 |
| B1.5 | Temporal continuity | `min t_b(B) - max t_b(A) ≤ G` | 1 (`G`) |
| B1.6 | Representability | The chain union `U` must satisfy `∩_{e∈U} W_e ≠ ∅` (closed, with `ε`). | 3 (`J`) |
| B1.7 | Monotonicity in chain | P2 applied to `U` after P1 is an admissibility condition; a chain violating it is not admissible. | 3 |
| B1.8 | Chain enumeration | Exhaustive enumeration of every population sequence satisfying B1.4–B1.7. A single population is a chain. Only maximal chains are kept: those that cannot be extended at either end. Populations are ordered by `(min t_b over members, min lo_e)`. | 4. If exhaustive enumeration proves computationally impractical on any declared fixture, stop and amend. Do not add a cap. |
| B1.9 | Regulation-span support | `S_cont(U)`: distinct `remaining` values of `U` after P1, sorted descending and split into runs where consecutive values differ by `≤ G`; `S_cont` is the largest `max - min` over runs | 1 (`G`, same continuity rule as `consistent_edge_span`) + 4 |
| B1.10 | Scoring | lexicographic `(S_cont, |U after P1|)`, larger is better | 4 |
| B1.11 | Weights / normalization | none | 4 |
| B1.12 | Minimum evidence | S3 only; no separate minimum per population | 1 |
| B1.13 | Ambiguity / tie | If two maximal chains have equal best score but different `U`: `unknown`, `inconsistent_gameplay_clock`, detail `competing_chains` | 1 (v3 `max_overlap` tie → unknown) |
| B1.14 | Abstention | P2/P3 after selection; B1.13 | 1 + 3 |
| B1.15 | Rejection | Populations not in the selected chain are listed with reason `not_in_selected_chain`; their edges that are not in `U` are excluded with reason `outside_selected_chain`. | 4 |
| B1.16 | Discontinuities | not special-cased; they break B1.5 or B1.6, so the segments become competing chains judged by B1.10/B1.13 and P3 | 4 |
| B1.17 | Duplicates | P1 | 3 + 5 |
| B1.18 | Returned `c_interval` | P4 over `U` after P1 | 1 + 3 |
| B1.19 | Deterministic ordering | edge order and population order above; chain identity is the sorted member edge list | 4 |

### B2 — robust latent clock fit (all decisions)

| # | Decision | Frozen value | Category |
|---|---|---|---|
| B2.1 | Latent model | one constant `c`, slope −1 | 1 |
| B2.2 | Intercept representation | an interval (P4), never a point estimate | 1 |
| B2.3 | Jitter bound | `J = Δ/2 = 0.25 s` | 3 |
| B2.4 | Robust loss | Truncated 0–1 loss: an edge costs 0 if `c ∈ W_e` and 1 otherwise. Minimizing it is maximum consensus over `W_e`. | 4 (the only parameter is `J`) |
| B2.5 | Inlier / outlier | Inlier set at candidate point `p`: `{e : lo_e - J - ε ≤ p ≤ hi_e + J + ε}`. Candidate points are the distinct `lo_e - J` values, where every maximal closed-interval clique is attained. The best set is the largest inlier set. | 3 + 4 |
| B2.6 | Competing fit / tie | If another candidate point gives an inlier set of the same size but different membership: `unknown`, `inconsistent_gameplay_clock`, detail `competing_fits` | 1 (v3 tie rule) |
| B2.7 | Continuity | No temporal rule at fit level; continuity is P1/P2 plus the frozen Layer 2 bracketing (`G`) | 1 + 3 |
| B2.8 | Monotonicity | P2 | 3 |
| B2.9 | Maximum outlier share | P3 (`ρ = 0.1`) | 1 |
| B2.10 | Minimum evidence | S3 only | 1 |
| B2.11 | Abstention | B2.6, P2, P3 | 1 + 3 |
| B2.12 | Excluded-edge reasons | `outside_jitter_bound`, `duplicate_remaining` | 4 |
| B2.13 | Discontinuities | not special-cased; consensus plus P3 decide | 4 |
| B2.14 | Duplicates | P1 | 3 + 5 |
| B2.15 | Overtime / knockout / partial / sparse | S8–S10 | 1 |
| B2.16 | Returned `c_interval` | P4 over the inlier set after P1 | 1 + 3 |
| B2.17 | Deterministic ordering | candidate points ascending; first best set kept; tie by B2.6 | 4 |

### Closed reason vocabulary (harness only)

- `fit_status` / `reason` use the frozen `FitReason` values.
- The harness-only `detail` is one of `non_monotonic`, `excess_excluded`,
  `competing_chains`, `competing_fits`.
- Excluded-edge reasons are one of `outside_selected_chain`,
  `outside_jitter_bound`, `duplicate_remaining`.

### Fixtures (identical for both candidates)

#### Synthetic generator

The generator reuses the `tests/test_alignment.py` conventions: cadence
`Δ`, timer confidence 0.95, and the display rule
`min(opening, max(0, ceil(c(t) - t)))`.

Generalized: `c(t)` may vary with video time `t`, and frame overrides inject
misreads.

Labels come from generator truth, never from running a candidate:

- An edge's generating offset is `c(t_b)`. Edges involving an overridden
  (misread) frame are tagged non-truth.
- The truth set `T` is the largest set of truth-tagged edges whose generating
  offsets span `≤ Δ`. The earliest such set wins ties.
- **Resolvable** means all of the following:
  - `len(edges) ≥ 20`;
  - the playback check is normal;
  - `1 - |T| / len(edges) ≤ ρ`;
  - no disjoint set of equal size satisfies the span condition.

  Otherwise the fixture is **unknown-labeled**.
- **Expected top / bottom** are the largest `remaining + 1` and the smallest
  `remaining` in `T`.

`C = 320.2` and opening 300 unless stated. Frames run from `t = 10.0` in
`Δ` steps to `t_end = 320.0`, with a clock-zero `MATCH_END`, unless stated.

| ID | Fixture | Label |
|---|---|---|
| F1 | clean (existing) | resolvable |
| F2 | one −1 glitch at 150 (existing) | resolvable |
| F3 | −1 glitches every 6 s, 40–300 (existing) | unknown |
| F4 | `t_end = 30` (existing too-few) | unknown |
| F5 | late start `t_start = 70` (existing) | resolvable |
| F6 | hidden stretch 140–170 (existing) | resolvable |
| F7 | 22-31-53 Turf War synthetic with 381/501 misreads (existing) | resolvable |
| F8 | scattered impossible high reads (existing) | resolvable |
| F9 | 501/500 glitch edge (existing) | resolvable |
| F10 | overtime: `clock_zero=False`, `lowest=1` (existing) | resolvable |
| F11 | knockout: `t_end=200`, `lowest=120`, no clock zero (existing) | resolvable |
| F12 | every other frame dropped for `t ∈ [100, 200)` | resolvable |
| F13 | reads only every `4Δ` (2.0 s) for the whole match | unknown (no edges) |
| A1 | step `+Δ` in `c` at `t = 100` | resolvable |
| A2 | step `−Δ` in `c` at `t = 100` | resolvable |
| A3 | linear drift `+Δ` total over the match | resolvable |
| A4 | linear drift `−Δ` total over the match | resolvable |
| D1 | recording cut: `c` steps −3.0 s at `t = 160` | unknown |
| D2 | recording cut: `c` steps −3.0 s at `t = 300` | resolvable (major segment) |
| D3 | step `+2.0` s at `t = 160` (two comparable fits) | unknown |
| L1 | linear drift `+4Δ` (2.0 s) total over the match | unknown |
| X1 (diagnostic only) | `c` alternates between `C` and `C + Δ` in 20 s blocks | resolvable by rule, but excluded from qualification and selection |

Step sizes `±Δ` and the drift `Δ` are the largest variation the resolvable
rule admits (category 3). The discontinuity size 3.0 s is chosen to exceed
`clock_domain_tolerance_seconds = 2.0` (category 1) by more than `Δ`, so it
is a discontinuity under every frozen tolerance. D3 and L1 use 2.0 s and
`4Δ` to sit clearly outside `Δ` (category 5, chosen as round multiples, not
fitted).

X1 is diagnostic only for two reasons:

- No pre-v4 evidence shows interleaved offsets. The v3 postmortem shows two
  contiguous blocks. The committed v1 `TUNING.md` reports all edges
  consistent with one 0.5 s `c` interval on five of the nine reference
  pairs.
- B1 abstains on it by definition (B1.4).

Including X1 would decide the comparison by a fixture with no evidential
basis (category 5). It is reported and does not affect selection.

#### Reference fixtures (fixture class 4, inclusion clarified)

The original text named "clock edge exports where already committed". No
raw edge export is committed; committed v2 outputs contain fits only. This
amendment includes instead the nine v2 tuning gameplay manifests, still on
local disk. Each is hash-verified against the `gameplay_manifest_sha256`
committed in
`analysis/review_validation/alignment/v2/tuning/pairs/<id>/review_gameplay_alignment.json`.

The harness consumes only timer readings and `MATCH_END` events. It decodes
no media. A hash mismatch stops the run.

| Pair | Path (under `analysis/`) | sha256 |
|---|---|---|
| 23-43-31 | `en-live-playerview-2026-10-10 23-43-31/vision_manifest.json` | `fe72a9e1175d1fa306a75d0525ad7b6a34461c70b21e5cdb23756aa88c5dda1c` |
| 23-56-41 | `en-live-playerview-2026-10-10 23-56-41/vision_manifest.json` | `9167636cfa7157358877cc5d493a6d328a9cd18db6796bf9da40841bb3675e42` |
| 18-18-00 | `_rerun_backup_2026-10-15/en-live-playerview-2026-10-11 18-18-00/vision_manifest.json` | `f864e817c6322e56aff77b5d5564fa64ab58906041ea66abc541bfc0ca6e3158` |
| 18-25-16 | `_rerun_backup_2026-10-15/en-live-playerview-2026-10-11 18-25-16/vision_manifest.json` | `309392f12c30e8529f166c88457a2ac38ef915393d69468d48bfcd84b12f439d` |
| 20-33-31 | `_rerun_backup_2026-10-15/en-live-playerview-2026-10-11 20-33-31/vision_manifest.json` | `d730c79b4cd08f2c176ac456ecfe8bdd5e889c286d2e1262fc6d020ad64cc191` |
| 22-20-42 | `_rerun_backup_2026-10-15/en-live-playerview-2026-10-11 22-20-42/vision_manifest.json` | `f38ba0f0abb395b2fa9b29438fd5671ae4e75965affcb6b5489b3a32074c685c` |
| 22-31-53 | `_rerun_backup_2026-10-15/en-live-playerview-2026-10-11 22-31-53/vision_manifest.json` | `c17e8650e5e4e55038627570d299781f7a2634a748ab46a7b7bdf8af4ff30396` |
| 22-37-47 | `_rerun_backup_2026-10-15/en-live-playerview-2026-10-11 22-37-47/vision_manifest.json` | `970a67b04205334e26aaff80df7a264a59a2695e8883a65494c78e084ea6e728` |
| 22-57-30 | `_rerun_backup_2026-10-15/en-live-playerview-2026-10-11 22-57-30/vision_manifest.json` | `4a5ad7da9f358af98d2e8ce6b6cae70ddd2b63f6230989f94d892ff4b4000f4d` |

Labels for these nine come from committed v2 records (category 2). All are
resolvable:

- the expected top is the committed v2 consistent clock top (180 for
  18-25-16 and 22-31-53, 300 otherwise);
- the expected `c` reference is the committed v2 `c_interval`;
- the expected Review regulation label is the committed v2 Review top.

The v2 fits are a regression reference, not ground truth.

#### Pair-009 (cross-track regression fixture; reported only)

The fixture is
`analysis/alignment-v3-heldout/pair-009/vision_manifest.json`, sha256
`7261d2c97b862772ca7a66337926f733d314dc9bd2c547077639c27904c0d82e`. This
hash matches the committed v3 `heldout/RESULTS_MANIFEST.json`.

It is reported for both candidates and is excluded from qualification and
selection, per the original forbidden list.

- The regression expectation passes if the candidate either:
  - returns `unknown`; or
  - returns `calibrated` with a consistent top within 2.0 s of the Review
    regulation top 300.
- A `calibrated` result with any other top is a regression failure. It is a
  stop condition before any v4 freeze, not a selection input.

### Qualification (the three frozen criteria, made exact)

A calibrated result is **correct** when all three conditions hold:

1. `|consistent top - expected top| ≤ clock_domain_tolerance_seconds = 2.0`
   (category 1).
2. Every generating offset of an edge in `T` is within `J` of the returned
   `c_interval` (synthetic fixtures). For reference fixtures, the returned
   `c_interval` is within `J` of the committed v2 `c_interval`, where
   interval distance is 0 if the intervals overlap (category 3).
3. Against the fixture's Review regulation label, `check_pair` would not
   assert `clock_domain_mismatch` (category 1). For synthetic fixtures, the
   regulation label is the opening value, except F5 and F11, where it is
   the expected top.

The frozen criteria then apply as follows:

- **"Calibrate every non-ambiguous reference fixture"**: on every resolvable
  fixture (F1, F2, F5–F12, A1–A4, D2, the nine reference manifests), the
  result is `calibrated` and correct.
- **"Return `unknown` for true discontinuity, insufficient evidence,
  unresolved competing fits"**: on every unknown-labeled fixture (F3, F4,
  F13, D1, D3, L1), the result is `unknown`.
- **"No confident fit from inadequate evidence"**: no `calibrated` result
  that is not correct on any fixture.

Any single violation eliminates the candidate (step 1).

### Selection steps made exact

- **Step 2 (regulation-span coverage):**
  - Compute `S_cont` (B1.9 definition) of the returned `consistent_remaining`
    on A1–A4, summed per candidate. An `unknown` result contributes 0.
  - A difference greater than `clock_domain_tolerance_seconds = 2.0` s is
    material, and the larger sum wins (category 1). Otherwise the step is
    tied.
- **Step 3 (excluded share):**
  - Compute the total excluded-edge count over A1–A4. Counts are integers
    over identical edge sets, which avoids float ties.
  - Any difference is material, and the lower count wins (category 4).
- **Step 5 (simplicity), documented now, before any comparison:** B2 is the
  simpler candidate. It has one tolerance (`J`), one sweep, and no
  population graph or chain enumeration. B1 adds populations, succession,
  continuity gap, representability and exhaustive chain enumeration. If
  steps 2 and 3 tie and both candidates qualify, B2 is selected.
- **Step 4** is unchanged: if neither candidate qualifies, the result is
  **neither**.

### Disclosures

- Once specified, these definitions make many outcomes analytically
  predictable. That is a property of a frozen deterministic method, not
  evidence for either candidate.
- The pair-009 mechanism was known from the postmortem before this amendment
  was written.
  - The two pair-009 populations lie exactly `Δ` apart, because clean edge
    intervals sit on the cadence grid.
  - `J = Δ/2` is derived from cadence and the precision v3 already claims,
    not from pair-009. It nevertheless admits two-block structures one
    cadence apart by construction.
  - Pair-009 is therefore reported only and cannot select a candidate.
- Track B parameters were not compared against the ten v3 pairs, and no v3
  aggregate was computed.
- The nine reference manifests were located and hash-checked for this
  amendment. Their edges and fits were not read.
- Any further implementation-time ambiguity stops work for another
  amendment. No value here may be adjusted after a fixture result is seen.

## Amendment 2 — Track A1 bounded post-`in_match` calibration window (2026-10-24)

### What was frozen, what changes, why

**Frozen at `ad9edfa` (Track A, "Precise semantics"):**

- A frame is phase-eligible only when fused `match_phase` is `"in_match"`.
- `in_match` is entered on the first non-opening timer observation.
- The Ready? search window keeps "sampling stops after `opening_clock_ticked`
  as today".
- Calibration is allowed only in the intersection of (Ready? window open) and
  (phase-eligible).

**Proven contradiction (read-only reachability check; no media, fixture or
outcome used):**

- In the production update `update_ready_gate_from_detections`, the same
  non-opening timer observation does two things on the same pass:
  - it steps `MatchPhaseFuser`, which calls `_enter_in_match` and sets
    `match_phase = "in_match"`;
  - it sets `opening_clock_ticked = True`.
- Both read the same opening set, `vision.lifecycle.opening_clock_seconds`.
- The fuser is never stepped again once the tick is set.
- `cadence_scan` runs that update before `maybe_calibrate_team_colors` on
  every frame.
- So `match_phase == "in_match"` implies `opening_clock_ticked`, and the
  conjunction `match_phase == "in_match" && !opening_clock_ticked` is
  unreachable.
- A1 eligibility was therefore empty for every input. The preregistered test
  "first non-opening tick → phase `in_match` → latch allowed" was
  unreachable.
- The contradiction lies in the frozen semantics themselves, not only in the
  implementation.

**What changes:** only the end of the calibration window. Phase semantics,
`in_match` entry, Ready? semantics and calibrator knobs are unchanged.

**Why:** to preserve the original safety intent while making A1 reachable.
That intent is that calibration must never use Ready?/pre-match evidence as
though gameplay has begun.

The uncommitted A1 working-tree changes predate this amendment. They must not
be resumed or extended under these semantics until separately authorized.

### Preserved (normative)

- `in_match` entry must follow the frozen `MatchPhaseFuser` semantics
  exactly. Phase semantics must not be redefined to make calibration
  reachable.
- `opening_clock_ticked` keeps its meaning: the opening frozen-clock period
  has ended. It still stops the Ready? search, and it still triggers the
  existing drop path when Ready? was not seen.
- `ready_seen` must be true for any calibration sample.
- Calibration must never sample in `intro`, `opening_countdown`,
  `out_of_match`, or any other pre-`in_match` phase.
- `TeamColorCalibrator` rules are unchanged: geometry, window,
  `min_accepted_frames`, hue separation, S/V gates, spread, and the immutable
  latch.

### Qualifying A1 entry observation (normative)

The qualifying A1 entry observation is the raw, unsmoothed timer detection
on a frame that meets all of these:

- it is encountered in scan order;
- its confidence is `>= vision.timer.min_usable_confidence` (0.50);
- its timer value satisfies the frozen `MatchPhaseFuser` condition for
  legitimate `in_match` entry.

Smoothed, held, synthesized, or below-floor timer values cannot themselves
qualify as the A1 entry observation, and cannot open the A1 calibration
window. The post-scan fused phase in `vision/state.py` must not be consulted
for A1 calibration.

### Bounded post-`in_match` calibration window (normative)

Let `t0` be the video time of the frame carrying the qualifying A1 entry
observation.

- The window opens only after legitimate production-path entry into
  `in_match`.
- It is the closed interval `[t0, t0 + 3.0]` seconds of scan/video time. The
  entry frame is eligible, and so is a frame at exactly `t0 + 3.0`.
- A frame is a calibration candidate only if all of these hold:
  - `ready_seen`;
  - the window is open;
  - the frame time lies in `[t0, t0 + 3.0]`;
  - roster-visible evidence is present on the frame;
  - it meets the existing calibration evidence requirements.
- `opening_clock_ticked` no longer ends eligibility.
- The window closes immediately on a successful calibration latch.
- It closes when the 3.0 s bound expires, meaning the first frame with time
  `> t0 + 3.0`.
- It must not reopen during that match, in particular:
  - on later non-opening timer reads;
  - after timer gaps;
  - after any fuser return to `in_match`.
- 3.0 s is a fixed design value (provenance category 5). It was supplied by
  the reviewer and was not chosen or tuned from any pair outcome. At the
  frozen 2 fps cadence, the window contains at most 7 cadence frames.

### Fail-closed behavior (normative)

- If no qualifying calibration latches within the window, the match has no
  calibration: `ally_row = unknown` and no map ink.
- Existing downstream behavior for unavailable calibration is unchanged and
  stays fail-closed.

### Evidence exclusion (normative)

- Tests may construct state combinations that production cannot reach. An
  example is manually setting `match_phase="in_match"` while
  `opening_clock_ticked` is false, as in the existing hand-built context
  test.
- Such tests may exercise a local function only. They must not be counted as
  evidence of production reachability or of A1 success.

### Reachability criterion (normative; added to Track A development success)

A1 cannot pass solely because all relevant cases remain unknown. Both of the
following are required:

1. A synthetic frame sequence driven through the real production call order
   must latch after legitimate `in_match` entry, with no hand-set phase or
   tick state. The call order is the `cadence_scan` per-frame order: the
   Ready-gate update with the real `MatchPhaseFuser`, then calibration.
2. At least one successful calibration latch must occur among the complete,
   already-declared seven-recording Track A development corpus.

No particular recording is designated in advance or selected retrospectively
as the required successful case. All seven are evaluated under the same
frozen amended semantics. Individual recordings may remain
unknown/fail-closed, but zero latches across all seven fails A1
reachability.

### Track A success language (amended)

- **Criterion 3 ("Historical seven replays")** is amended:
  - "Documented unknown with fail-closed semantics" remains an acceptable
    result for an individual recording.
  - An implementation that never reaches calibration is not an A1 success.
  - Criterion 3 is satisfied only together with the reachability criterion
    above.
- **Criterion 6 (new)** is the reachability criterion above.
- Criteria 1, 2, 4 and 5 are unchanged.

### Regression requirements (normative; replace the A1 items in "Track A tests")

Tests must cover at minimum:

1. Ready?/pre-match roster evidence is rejected.
2. Opening/frozen-clock frames are rejected.
3. The legitimate first non-opening timer observation, qualifying as defined
   above, establishes `in_match`.
4. A qualifying roster-visible frame within the following 3.0 s window can
   latch calibration through the production call path.
5. A qualifying frame after the 3.0 s window cannot latch.
6. The window cannot reopen.
7. A timer misread or smoothing artifact must not create an alternate
   calibration path inconsistent with the legitimate production `in_match`
   transition. This includes:
   - below-floor non-opening reads;
   - smoothed, held or synthesized values;
   - any post-scan fused phase.
8. Failure to calibrate remains fail-closed for map-ink use.

The remaining "Track A tests" items stay in force:

- missing Ready? → unknown end state;
- missing stage → map ink disabled;
- provenance includes phase at accepted frames;
- immutable latch after success.

Provenance must additionally record:

- `t0`;
- the window bounds;
- the window close reason (`latched` or `expired`);
- phase-ineligible and out-of-window rejection reasons.

### Disclosures

- This amendment does not change phase semantics. Under the frozen fuser, a
  single usable non-opening timer misread during the frozen clock still
  enters `in_match` and would open the window. This residual risk is
  disclosed, not addressed here.
- No development or held-out media, pair outcome, fixture or candidate
  result was inspected while drafting. No parameter was chosen or tuned from
  pair outcomes.
- Track B (Amendment 1) is unchanged.

## Amendment 3 — Track A criterion 4 correctness reference (2026-10-24)

### What was frozen, what changes, why

**Frozen at `ad9edfa` (Track A development success, criterion 4):**

> 4. **Map ink:** no observations without latched calibration; no regression
>    on the seven-replay map-ink comparison method from v3 Amendment 1.

The referenced v3 method (`v3/PREREGISTRATION.md`) reads: "verify that map
observations occur only after roster-gated calibration, no-calibration
matches produce none, and ally/opponent fractions agree with the independent
roster-color reference. Report before/after deltas per sample." v3
Amendment 1 applied it as a before/after identity comparison ("Before/after
outputs were exactly identical for every case").

**Specification defect:**

- The "independent roster-color reference" was never defined or
  implemented. No frames, hue procedure or tolerance exist.
- Its only sketch (`v2/diagnosis/MAP_INK_EXPOSURE.md`) uses in-match
  roster-icon hues. That is the evidence A1 itself samples, so it is not
  independent of A1.
- A1 changes calibration timing by design, so a before/after identity
  comparison cannot distinguish a regression from a correction.
- The A1 development evaluation therefore reported criterion 4 as safety
  PASS, correctness NOT EVALUABLE, overall NOT EVALUABLE.

**What changes:** criterion 4 is split into a safety part (unchanged) and a
correctness part defined by this amendment. The v3 Amendment 1 before/after
comparison is retained as descriptive coverage reporting only.

**Why:** to repair the specification prospectively. This amendment does not
reinterpret any already-observed A1 result and cannot convert the existing
NOT EVALUABLE into PASS without new, sealed evidence.

### Criterion 4 (amended, normative; replaces criterion 4)

4. **Map ink:**
   - **4a safety (unchanged):** no map-ink observations without a latched
     calibration; a match without calibration produces none.
   - **4b correctness:** the verdict of the evaluation defined in this
     amendment.
   - Criterion 4 is PASS only if 4a and 4b are both PASS; FAIL if either is
     FAIL; otherwise NOT EVALUABLE.

The question 4b answers: did A1 preserve or improve the correctness of
map-ink classification, relative to the v3-replay calibration, despite
intentionally changing calibration timing? Coverage changes (which map-open
moments produce observations) are not correctness and are never counted as
regression or improvement.

This supersedes Amendment 2's statement that criterion 4 is unchanged.
Criteria 1, 2, 3 (as amended by Amendment 2), 5 and 6 are unchanged, and so
is the rule "Failure of any criterion stops escalation to fresh held-out
work". Track A requires all criteria to hold, so criterion 4 holds only when
PASS; NOT EVALUABLE does not satisfy it.

The team-identity and point annotations defined below serve 4b only. They
are not operator ally-row declarations and not ground truth for criterion 3
or any other criterion; the historical replays still provide no operator
ground truth for those.

### Preservation semantics (normative)

Any finite sample showing no difference remains consistent with a small
regression, and no externally justified margin exists (`map.ink` has no
coaching consumer). 4b therefore recognizes exactly two positive claims:

- **Identity:** A1 and v3 assign identical labels to every candidate pixel
  of the recording's eligible population (defined below). This is
  deterministic and label-free.
- **Demonstrated improvement:** A1 is correct significantly more often than
  v3 on points where they disagree, under the test below.

Calibrations that differ but are not resolved by either claim are
INCONCLUSIVE, never PASS. A non-significant result is never evidence of
preservation.

### Corpus and applicability (normative)

The corpus is the declared seven-recording Track A development corpus.

| Recording | 4b status | Reason |
|---|---|---|
| 18-18-00 | correctness-applicable | — |
| 20-33-31 | correctness-applicable | — |
| 22-31-53 | correctness-applicable | — |
| 22-37-47 | correctness-applicable | — |
| 22-57-30 | correctness-applicable | — |
| 22-20-42 | safety/coverage only | A1 produced no calibration (accepted fail-closed); correctness undefined |
| 18-25-16 | safety/coverage only | Production map ink never classifies: `stage_id` resolves to `ealtail_alley`, which has no stage geometry (identical in v3 and A1) |

The media of 22-20-42 and 18-25-16 must not be opened under this amendment.
Recording is the verdict unit; pixels are never pooled across recordings for
a verdict.

### Pinned inputs (normative)

Source videos (SHA-256 of the file `en-live-playerview-2026-10-11 <id>.mov`;
identity is the hash, and the path used is recorded at extraction):

| Recording | Video SHA-256 |
|---|---|
| 18-18-00 | `04e9c2799ec385e1c24bcdb9387ad30e75c0b61489d8f74c150568fc4b921f3f` |
| 20-33-31 | `9390fa05c32bc25fbf957b26f7986ce68dfd3d29f1b678bd5a798128b8cafc4a` |
| 22-31-53 | `9435ceba6f19beeb1e7d8854926c8af9af2ce610531919b526cc28ebf2a414e6` |
| 22-37-47 | `7d069efc4d4798bb6d9d0a92c38320d0cd1d97aaf98b4ac5fc3b51e6e098956c` |
| 22-57-30 | `ffb7636db643aa817e600c0597e0ef71bcf36eb7cd61df370eae89e00f4b9e36` |

Frame-selection metadata: v3-replay `vision_manifest.json` in
`analysis/en-live-playerview-2026-10-11 <id>/`. These predate A1. Their raw
`map_overlay` readings were verified identical to the A1 development-run
manifests.

v3 baseline calibration: v3-replay `match_identity.json`, same directories.
A1 calibration: A1 development-run `match_identity.json` in
`analysis/_a1_amendment2_dev/<id>/` (evaluation summary `evaluation.json`,
SHA-256 `8c769dc4bde8a4b37d240f5fb8019bd513b92045a110d0f1269238344f6a79b6`).

| Recording | v3-replay `vision_manifest.json` | v3-replay `match_identity.json` | A1 `match_identity.json` |
|---|---|---|---|
| 18-18-00 | `fcd5b55aaa89b5acfcd028d2a140d1ee45a44011acb55e83e9e856b87dea6429` | `ccb17a920c6bf010def1ede8b64dcb99782d0cc4b9d52b5111a4177c42ee4e57` | `deb3c7dbd65039a48f3b636f29823b019b40b1d59cbcac932705510ec7818193` |
| 20-33-31 | `783295e27da1f98402d9b91c89e0afabf3a77d54658fbe3a4affbf85bc77ca41` | `0ed0de2707ee19644d1c684e1e1a75b5a52168da524db349856ab0888eea0323` | `d13486ec81bcf4b16dfd0e6d986bbbe8c40238f90b3a96a0253e1f88866ee27d` |
| 22-31-53 | `c5e5770da8fa1804e4f1be70290765d6e8cb433db89e758513fa19a908b4eeda` | `fee58b967c0f8e914793c0c7d58cf7979d38bcafd4dc52354544d5bf6acf1bf4` | `6adb291f4b77cdd0770387baeefc230b1d87f47656a446b44e4e8f29c196e8c8` |
| 22-37-47 | `ee913984c71cf53b19ca821fffa4e4122294c5dcc30c862b3bfa95539441acee` | `9b8fdd4fd963c2ee162f0e383cc1f001fcf867fd7bad214e7b4577c5fb3467f2` | `37a853e9e81bdb7d28563412e5915f2354ff2f97fbc5357cd6863ceab7bb853e` |
| 22-57-30 | `1c68c81f8dd76f4fa3d73ae5ac248592fe294fac086697455c4a6f49cd5b776f` | `a30a6da97053b719ab7e1dc1e220cc9d33f50b60927ba1d50731ab4cb1c0c028` | `ad47138732baa35df2ec5afa80c47258f4b9e1baa8b04dac686d91efabd6c3d3` |

Coverage-only recordings (descriptive inputs, not opened as media):

| Recording | v3-replay `vision_manifest.json` | v3-replay `match_identity.json` | A1 `match_identity.json` |
|---|---|---|---|
| 18-25-16 | `7fcabed3387883fd01ec08793843858d22a920d8d1f65112b8bd87a6c34f9002` | `27c3687ddd6ec2f808f2078a5e751b0bd3609917f718e74c7e5d469ada575a3c` | `f3b9a1c15285ddf5d000e7de04d2d835fd01aaea0800d8c0791cb06a48ef157f` |
| 22-20-42 | `a29a467b3b7d484b21fd6991e1eac410752bf3c87685ab8041de27aedf78f9ef` | `82c11f9b9b907296cfb4398b91df5672b219d7cb70af0e2f458fbbeff65a40c7` | `3b2b1ff2b5a5fad77f8c9b9f33cdda6e773659e4125d7607d59cdb68e769da5c` |

Classifier configuration: `configs/default.yaml` (SHA-256
`e0b96fb6ac4d747a74e33a835ecba1effdf32317428125421c7c68799c01945a`;
`vision.map_ink`: `h_tolerance` 15, `s_min` 70, `v_min` 40) and
`src/splatoon3_ai_coach/vision/map_ink.py` (SHA-256
`50f0dba3e0e004dc17096cff6d5825a496cd917d03517de00c08402150070ab3`).

Stage geometry (`configs/stage_maps/`):

- `makomart/default.yaml` `ffb3f11d39bec2abb8e976c370c3fea66ab40b3adf1e852ea0c80f7275148ad2`
- `shipshape_cargo_co/default.yaml` `64ee7e24d9824db005adf0cda55c45345ff706b015095028f9fd0bfd62a781e5`
- `manta_maria/default.yaml` `4d192e69d981c88d4b0ee9abb9e8869884baeaadcde38a7a34b4364eeeb5b913`
- `manta_maria/stage_mask.yaml` `f4a4d7a88b65fda9b05aabdb2d0fb50f162f1278997b46810ee54f980b184621`
- `wahoo_world/default.yaml` `ec9940b9151e8f8574347df6de089df1311fb44c6d0bf374221b5a09eded853a`

A1 implementation working-tree digests at drafting (A1 is not yet
committed):

- `vision/calibration_window.py` `1ec3929969b073adefd2f11923d88fb06cd716009dbdd0d098eed3649457ebef`
- `vision/match_side_effects.py` `41b158549eb37bf8b7f084a7ced7ab9da781afd9b6d79abebfe1a95f9c98db08`
- `vision/team_color_calibration.py` `ab87d07df313044e5d0194b4d0452c0ceca01df2da66643a478d59a18f28ea4d`
- `vision/cadence_scan.py` `e34adc361a8bd81b224d3fe8be39231f136425be97f516d6b245ee4a680402fd`

Any mismatch with a pinned hash: STOP before opening or processing.

### Frame population and selection (normative)

For each correctness-applicable recording, from its pinned v3-replay
manifest, a cadence frame is **eligible** when all of these hold:

- its raw `map_overlay` reading has `present == true`;
- its raw `player_count` reading has `roster_visible == false` (this makes
  the population disjoint from every A1 and v3 calibration frame and drops
  map transitions);
- its fused snapshot has `match_phase == "in_match"`;
- its time is before the `MATCH_END` event `start_time`, if one exists.

**Intervals:** maximal runs of eligible frames whose consecutive timestamps
differ by exactly one cadence step (0.5 s). The **eligible population** is
all frames of all intervals.

**Ordering:** intervals with 2 or more frames first, by start time; then
single-frame intervals, by start time.

**Stage 1:** K = 8. With M_long long intervals, take K' = min(K, M_long)
long intervals at indices `floor((j + 0.5) * M_long / K')`, j = 0 … K'−1,
over the long-interval list. If K' < K, take K'' = K − K' single-frame
intervals at indices `floor((j + 0.5) * M_single / K'')`, j = 0 … K''−1,
over the single-frame list of M_single intervals. From each selected
interval take the frame at index `floor((len − 1) / 2)`.

**Stage 2 (only where triggered, see below):** the remaining unselected
intervals, ordered the same way, selected by the same two-list procedure
with K = min(8, remaining). Capacity known from metadata at drafting:
18-18-00 4, 20-33-31 8, 22-31-53 8, 22-37-47 2, 22-57-30 8.

No frame is selected, replaced or skipped because of its image, labels, A1
output or v3 output.

### Extraction (normative)

- Decode with the pipeline `VideoLoader`, locate each selected frame by
  `source_pts`, and verify `source_frame_index`.
- Write a lossless PNG per frame. Record the video SHA-256, the PNG
  SHA-256, the decoded-array SHA-256 and the decoder library versions.
- PNGs are the frozen inputs from then on; frames are never re-decoded for
  the comparison.
- The extraction tool must not load any calibration or apply any
  calibrated classifier.

### Point sampling (normative)

- **Domain:** the production active sample mask (`vision.map_ink._sample_mask`
  with `resolve_stage_map_geometry(geometry_dir, stage_id, battle_mode_id)`
  and `resolve_stage_mask(geometry_dir, stage_id)` for the pinned
  `stage_id` / `battle_mode_id`) intersected with the production candidate
  gate: OpenCV HSV `S >= 70` and `V >= 40` on the extracted PNG.
- **Seed:** the first 8 bytes, big-endian, of
  SHA-256(`"amendment3-points|" + video_identity + "|" + str(source_pts)`),
  where `video_identity` is the manifest field. Generator:
  `numpy.random.Generator(numpy.random.PCG64(seed))`.
- **Draw:** shuffle the row-major flat indices of the domain
  (`numpy.flatnonzero`) with `Generator.permutation`. Accept points in that
  order if their Euclidean distance to every accepted point is at least 32 px.
  Stop at P = 9 accepted points or when the shuffled list is exhausted.
- **Duplicates:** `ceil(0.10 * N_points)` of the stage's points, chosen
  without replacement with a seed from SHA-256(`"amendment3-dups|" +
  point-manifest SHA-256`), each re-presented at least 50 items after its
  first presentation (at the end of the queue if the queue is shorter).
  The duplicate count is fixed by this rule and must not be increased.

### Swatches, team identity and consistency (normative; evaluation only)

Three separate concepts. Team identity is an evaluation-side annotation. It
never enters production calibration, `resolve_team_identity`, Review
association or A1 processing. No operator-declared ally row is used.

**Neutral swatches (colour reference, no team meaning):**

- At the start of the Stage 1 session, before any point is labelled, the
  annotator selects one representative pixel from each of the two visually
  distinct ink colours on that recording's selected map frames.
- Each selected pixel must meet the frozen swatch-quality requirement of
  this reference procedure: OpenCV HSV `S >= 70` and `V >= 40`. These are
  literal constants of this amendment. They are not read from, inherited
  from or tied to the production classifier configuration, and no later
  configuration change alters them. The tool rejects a pixel that fails
  them, and the annotator selects again. The fixed 9×9 neighbourhood of the
  pixel is used for display only and is never a gate.
- The two pixels are assigned the identities A and B by a random draw. The
  draw and its seed are recorded. Once assigned, a recording's A/B identity
  is immutable for all stages.
- If the annotator cannot find two visually distinct ink colours, the answer
  is `cant_tell`. That recording is then NOT EVALUABLE, and its points are
  never presented.

**Team identity (categorical):**

- The question is: "Which swatch is my team's colour?" The answers are `A`,
  `B` or `cant_tell`.
- Cue material: that session's selected map frames and, for each selected
  interval, the first later cadence frame whose fused
  `player_lifecycle == "alive"`.
- Cue order: (1) teammate and own-player icons on the map; (2) the colour
  the player is visibly firing in the gameplay frames; (3) otherwise
  `cant_tell`.
- Each time the question is asked, the on-screen positions of A and B are
  re-randomized and the draw recorded. Only positions change; A/B identity
  does not.

**Consistency (no hue comparison, no threshold):**

- Stage 1: the question is asked at the session start (J1), before any
  point is labelled, and at the session end (J2), after the last point is
  labelled.
- Stage 2, if the recording enters it: asked at the Stage 2 session start
  (J3) and end (J4).
- No team-identity question may display any earlier answer (J1–J3) or the
  derived A/B → ally/opponent mapping. Neither may any point-labelling
  screen. The annotator must not consult the committed records holding
  them.
- Team identity is consistent iff every answer given for the recording
  (J1 and J2; plus J3 and J4 if Stage 2 runs) independently selects the same
  swatch. Any `cant_tell`, or any disagreement, makes the recording NOT
  EVALUABLE. At Stage 1 this is final and there is no Stage 2. At Stage 2
  it makes the recording final NOT EVALUABLE.

### Reference labels, blinding and display (normative)

- **Vocabulary:** `ink_A` (ink of swatch A's colour), `ink_B` (ink of
  swatch B's colour), `not_ink`, `cant_tell`. Labels carry no team meaning.
- **Mapping:** applied only by the comparison tool, after the stage's labels,
  team-identity answers and gate are sealed. The consistently selected
  own-team swatch's label maps to ally, the other swatch's label to
  opponent, and `not_ink` to other.
- **Blinding:** recordings are shown only as random opaque tokens (key
  sealed separately). Points of all recordings and frames are presented in
  one shuffled queue. The annotator never sees timestamps, recording names,
  hue values, classifier overlays, production labels, team-identity answers
  or the A/B mapping.
- **Display:** a C×C crop centred on the point (default C = 96), padded with
  neutral grey (128,128,128) at image borders, shown with integer
  nearest-neighbour zoom Z (default 4); a crosshair that leaves the centre
  pixel uncovered. The recording's swatches A and B are shown beside it in
  fixed positions (A left, B right), each as the 9×9 neighbourhood of its
  pixel at the same zoom. No brightness, contrast, gamma or colour
  alteration.
- **Annotator:** the operator, as the single blinded annotator.

### Judgeability (normative)

- **Point:** a recorded label other than `cant_tell`. A duplicated point
  counts only if both copies carry the same non-`cant_tell` label; it then
  counts once. Otherwise, including a missing label, it is unjudgeable.
- **Frame:** at least 6 of its 9 points judgeable (a frame with fewer than 6
  drawn points is unjudgeable).
- **Stage 1 recording:** a valid swatch pair, consistent team identity
  (J1 = J2) and at least 6 of its 8 Stage 1 frames judgeable. Otherwise
  final NOT EVALUABLE; no Stage 2.
- **Stage 2:** the recording needs consistent team identity across J1–J4.
  Stage 2 is usable only if it adds at least 2 judgeable frames. The final
  combined sample must have at least 6 judgeable frames; otherwise final NOT
  EVALUABLE. If Stage 2 adds fewer than 2, the Stage 1 INCONCLUSIVE result is
  final.
- **Overall:** at least 4 of the 5 correctness-applicable recordings must be
  evaluable (not NOT EVALUABLE).

### Duplicate admissibility gate (normative)

The gate tests whether the reference process is admissible. It is not an
outcome metric. It is computed from recorded labels alone, before any
comparison of that stage is run.

**Population, frozen before annotation.** No later discretion over
eligibility or denominator membership is allowed.

- D_s, the duplicate pairs of stage s: every pair produced by the duplicate
  rule from the stage s point manifest, excluding only pairs of recordings
  without a valid swatch pair (their points are never presented). That
  exclusion is fixed when swatches are recorded, before any point label
  exists.
- Membership in D_s does not depend on team-identity answers or their
  consistency (labels are A/B-neutral). Nor does it depend on frame or
  recording judgeability, recording evaluability, or any comparison result.

**Classification of each pair in D_s, from its two recorded copies:**

| Copies | Numerator (disagreements) | Denominator |
|---|---|---|
| same label, not `cant_tell` | no | yes |
| both `cant_tell` | no | no (uninformative abstention) |
| one `cant_tell`, one other label | yes | yes |
| two different non-`cant_tell` labels | yes | yes |
| either copy has no recorded label | yes | yes |

**Gates:**

- **Stage 1 gate**, over D_1: the upper one-sided 95% Clopper–Pearson bound
  on numerator/denominator must be at most **0.10**. Otherwise 4b is NOT
  EVALUABLE and no comparison is computed. A zero denominator fails the gate.
- **Stage 2 gate**, if Stage 2 runs and before any Stage 2 comparison: the
  same bound over D_1 ∪ D_2 must be at most 0.10. D_1 pairs keep their
  Stage 1 classification. If it fails, 4b is NOT EVALUABLE, and every
  per-recording result already computed at Stage 1, including PRESERVED, is
  void.
- Rationale: reliability concerns the reference process, not when its labels
  were collected. Because the Stage 2 recheck is fixed here in advance, a
  Stage 2 failure is not retrospective invalidation based on A1 performance.
- The gate does not estimate a human error rate and enters no threshold or
  null hypothesis.
- **Strict practical consequence:** a denominator of at least 29 with zero
  disagreements is needed even to pass. With about 36 Stage 1 duplicates,
  one disagreement is expected to fail the gate.

### Comparison (normative)

- For each judgeable point, apply `MapInkClassifier(config.vision.map_ink)`
  with `apply_calibration(TeamColorCalibrationResult.from_dict(...))` from
  the pinned A1 and v3 `match_identity.json` respectively, then
  `classify_bgr` on the PNG at the point.
- A calibration is correct at a point iff its production class equals the
  point's mapped reference class (see Mapping).
- Clusters are judgeable frames (one per selected interval). Per cluster i:
  b_i = points where v3 is correct and A1 wrong; c_i = points where A1 is
  correct and v3 wrong; d_i = c_i − b_i.
- **Identity:** q_all = the fraction of candidate pixels (domain as in point
  sampling), over all frames of the recording's eligible population, where
  the A1 and v3 labels differ. Identity holds iff q_all = 0, and holds by
  construction iff the two calibrations' `ally_h` and `opponent_h` are
  equal. q_all is computed only after the stage's labels and gate are
  sealed, and is unaffected by staging.
- **Sign-flip test:** over the F_nz clusters with d_i ≠ 0, T = Σ d_i;
  enumerate all 2^F_nz sign vectors e, T* = Σ e_i d_i.
  p_reg = Pr(T* ≤ T); p_imp = Pr(T* ≥ T); ties included. With F_nz = 0,
  p_reg = p_imp = 1.

### Assumptions and limitations (normative disclosure)

- **Exchangeability:** selected intervals are assumed separate enough that
  the signs of their d_i are exchangeable and symmetric about 0 under the
  null. Spatial correlation within an interval is why the interval, not the
  pixel, is the test unit.
- **Selection:** frame and point selection, exclusions, vocabulary,
  classifier configuration and stopping rules are frozen here, before any
  label or comparison result exists; otherwise the randomization
  distribution is invalid.
- **Reference validity:** annotation that passes the gate is treated as
  sufficiently accurate to distinguish the classifiers. Duplicate agreement
  and consistent team identity are not proof of correctness and cannot
  exclude shared systematic error.
- **Resolution:** with at most 16 intervals per recording, small or diffuse
  differences may be unresolvable.
- **Scope:** conclusions apply only to each recording's sampled map-open
  population. They do not establish correctness on other maps, setups, game
  states or fresh v4 held-out media.
- **Identity:** q_all = 0 proves A1 and v3 label every candidate pixel of
  the eligible population identically. It does not prove those shared
  labels correct.
- Significance levels below are planned conventions conditional on these
  assumptions, not unconditional false-PASS or false-FAIL guarantees.

### Thresholds, stages and verdicts (normative)

- Regression: one-sided α_R = 0.01 per recording, 0.005 at each look.
- Improvement: one-sided α_I = 0.05 per recording, 0.025 at each look.
- Smallest attainable p is 2^−F_nz: regression needs F_nz ≥ 8, improvement
  F_nz ≥ 6, at a look. Fewer discordant clusters can only be resolved by
  identity.

Per recording, at each look, in precedence order:

1. applicable judgeability minimums not met: NOT EVALUABLE;
2. identity: PRESERVED;
3. p_reg ≤ 0.005: REGRESSED;
4. p_imp ≤ 0.025: PRESERVED (improved);
5. otherwise: INCONCLUSIVE.

**Stage 2:** only for Stage 1 INCONCLUSIVE recordings. After Stage 2 labels,
team-identity answers and the pooled gate are sealed (and only if that gate
passes), recompute b_i, c_i and d_i over the union of all Stage 1 and Stage 2
judgeable clusters and apply the precedence once more at the same per-look
levels. That result is final. There is no third stage.

**Finality:** if no recording is Stage 1 INCONCLUSIVE, Stage 1 results are
final. Otherwise all Stage 1 results stay provisional until the Stage 2 gate
passes, and become void if it fails.

**4b verdict:**

- NOT EVALUABLE if the Stage 1 gate fails or the Stage 2 gate fails;
- otherwise FAIL if any recording is final REGRESSED;
- PASS if none is REGRESSED, at least 4 recordings are evaluable, and every
  evaluable recording is final PRESERVED;
- NOT EVALUABLE otherwise (including any final INCONCLUSIVE recording).

### Descriptive reporting (never part of a verdict)

- pooled pixel accuracies of A1 and v3;
- per recording: T/N, q_all, F_nz, p_reg, p_imp, judgeable counts;
- per recording, for A1 and v3: the inversion rate (judgeable points whose
  mapped reference class is ally or opponent, classified as the opposite
  team, among those classified as either team). An inversion shared by
  both is reported as an absolute correctness defect outside criterion 4;
  an inversion new to A1 surfaces through the regression test;
- coverage for all seven recordings: eligible frames and intervals, and the
  stored v3-replay and A1 map-ink observation counts.

### Pilot and firewall (normative)

**Pilot material:** two recordings chosen by this rule before any pilot
media is decoded (hashing excepted): analysis directories directly under
`analysis/` that contain `vision_manifest.json`, excluding `_`-prefixed
directories, `analysis/alignment-v3-heldout/`, `analysis/review_validation/`,
any directory whose `video_identity` equals one of the seven corpus
recordings, and any capture package; requiring stage geometry, at least 8
eligible intervals under the frame rule, and a local source video whose
identity matches the manifest. Take the two with the lexicographically
smallest `video_identity`. If a chosen recording is later found to belong to
any declared corpus or held-out set: STOP.

**Pilot procedure:** the full Stage 1 procedure (selection, extraction,
points, swatches, team identity, labels, duplicates, gate). No calibrated classifier
is applied to pilot frames and no pilot comparison is computed.

**May change after the pilot (complete list):**

1. crop size C, within 64–160 px, and zoom Z (integer);
2. marker shape, colour and thickness; whether a context thumbnail is shown;
3. layout, key bindings, button order, progress display, break length;
4. instruction wording and examples, drawn only from pilot material;
5. wording of the team-cue instructions, not the cue order;
6. interface bug fixes that leave frames, points and label meanings
   unchanged, each with a test.

**May not change:** everything else in this amendment, including corpus and
applicability, pinned inputs, frame population, interval definition, K, the
ordering and selection formulas, the middle-frame rule, the point domain,
P, spacing, seeds, duplicate rate and placement, label vocabulary and
mapping, the swatch procedure (swatch-quality requirement, A/B draw, immutability,
9×9 display neighbourhood), the team-identity question, cue material, cue
order and consistency rule, judgeability minimums, the 4-of-5 rule, the gate
population, classification and 0.10 cap, the classifier and its
configuration, the comparison, the test, α levels, Stage 2 rules, verdict
precedence and finality, blinding, display (no pixel alteration) and
sealing order.

If the pilot shows that a frozen element is unworkable (for example, no
usable map-icon cue): STOP. A new amendment and a fresh pilot on different
non-corpus material are required. Corpus material stays untouched.

### Tool authorization (normative)

- This section defines the permitted scope; starting implementation still
  requires separate authorization.
- New code only under `tools/diagnostics/map_ink_reference/` (frame
  selection, extraction, point sampling, annotation interface, gate,
  comparison, report) with tests under `tests/`.
- It may call production functions read-only, including module-private
  helpers named above. It must not modify `src/`, `configs/`, A1 code,
  A1 tests or any pinned artifact.
- Tests must show that the extraction and annotation tools neither load nor
  accept as input any of the following: A1 or v3 calibration hues;
  `match_identity.json` calibration values; map-ink observations
  (`map_observations.json`); classifier labels or calibrated classifier
  output; comparison outcomes. The tests are a static import/AST check of
  those modules, plus a check of their command-line and public function
  parameters.
- The annotation tool also must not display or accept, as input to any
  question or labelling screen, earlier team-identity answers or the A/B
  mapping.
- Only the comparison and report tools may read calibrations or derive the
  mapping, and only after the stage's labels, team-identity answers and gate
  are sealed.

### Sealing order and provenance (normative)

Committed records go in `analysis/review_validation/alignment/v4/amendment3/`;
PNGs and raw labels live in gitignored `analysis/_amendment3/`, identified by
SHA-256 in the committed records.

1. Preconditions: this amendment committed; the A1 implementation committed,
   with its commit recorded and its files matching the working-tree digests
   above (otherwise STOP).
2. Tools and tests committed; tool commit recorded.
3. Pilot run; a pilot addendum committed listing only changes from the
   allowed list (or "none").
4. Stage 1 frame/point manifest committed before any corpus labelling.
5. Stage 1 session record committed (swatch pixels, A/B draw, position
   draws, J1, J2, point labels). Token key committed afterwards. Stage 1
   gate result over D_1 committed before any comparison.
6. Stage 1 comparison (mapping, q_all, test) with the committed tool;
   results committed.
7. Stage 2, if triggered: Stage 2 manifest committed; Stage 2 session record
   (position draws, J3, J4, labels) committed; pooled gate over D_1 ∪ D_2
   committed before any Stage 2 comparison; then, only if it passes, the
   combined comparison.
8. Final report with the 4b and criterion-4 verdicts.

### Prohibitions and stop conditions (normative)

- No A1, classifier, configuration or threshold change in response to any
  Amendment 3 outcome; Amendment 3 outcomes are never used to tune A1.
- No re-extraction, re-labelling, relabelling of sealed items, extra
  duplicates, extra stages or alternative tests after sealing.
- No media of 22-20-42 or 18-25-16; no v3 held-out material; pair-009 stays
  closed; no fresh v4 held-out material.
- STOP on any hash mismatch; on a tool defect found after sealing (report;
  a fix requires an amendment); or on a pilot finding that a frozen element
  is unworkable.

### Disclosures

- Drafted after A1 development map-ink outcomes were known (calibrations,
  sample counts, per-sample deltas, and the 22-37-47 hue change). The method
  was chosen on principle (direct pixel ground truth); no frame, point,
  recording, threshold or tolerance was chosen from those outcomes.
- During design only metadata was read: raw `map_overlay` and `player_count`
  readings, fused `match_phase` / `player_lifecycle`, `MATCH_END`, frame
  identifiers and dimensions from the v3-replay manifests (A1-run manifests
  only to confirm identical `map_overlay` readings), and `stage_id` /
  `battle_mode_id`. No media was decoded; no label exists.
- The v3 held-out final FAIL and its postmortem predate this
  preregistration, as for Amendments 1 and 2. No v3 ten-pair aggregate
  result, and no v3 held-out pair output, was used in drafting. The
  criterion 4 change addresses the undefined reference, not v3 performance.
- Newly created team-identity and point labels are evaluation-only
  annotations created after A1 outcomes were observed. They must never be
  described as preregistered labels.
- The single annotator knows aggregate A1 results; blinding limits but does
  not eliminate this risk. wahoo_world appears in two recordings.
- Stage 2 team identity (J3, J4) is asked without displaying any earlier
  answer or the mapping, but the annotator may remember J1/J2, and the
  Stage 1 comparison records exist by then. Repeated judgements by one
  person are not statistically independent replications.
- Revised 2026-10-24 before any label existed. Swatches became neutral A/B
  colour references. Team identity became a separate categorical question
  with no hue threshold. The Stage 2 gate became pooled and voids Stage 1
  results on failure. The gate population was frozen explicitly. No
  classifier tolerance enters any admissibility rule. The swatch-quality
  values are frozen literals of this procedure.
- Recorded, not fixed: the English intro template
  `calibration/templates/match_intro/en/stages/en-ealtail_alley.png` is
  misspelled, so English Eeltail Alley matches resolve a `stage_id` with no
  geometry and receive no map ink.
- Recorded, not fixed: the operator `--ally-row` fallback in
  `review/team_identity.py` is a frozen-boundary implementation defect /
  latent contamination path with no observed contamination demonstrated.
  This amendment does not use it.
- Tracks B and C, Amendments 1 and 2, and all other Track A criteria are
  unchanged.
