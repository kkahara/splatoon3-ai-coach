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
