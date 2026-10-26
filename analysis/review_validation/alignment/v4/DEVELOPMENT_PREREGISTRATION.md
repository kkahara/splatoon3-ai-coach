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

## Amendment 4 — Amendment 3 clarifications (2026-10-24)

### What was frozen, what changes, why

**Frozen:** Amendment 3 as committed at `228e037`.

Implementing the Amendment 3 tooling (uncommitted, synthetic tests only, no
media) showed points where the sealed text does not uniquely determine
behaviour. Two were left fail-closed: A3-AMB-1 (source of `stage_id` /
`battle_mode_id`) and A3-AMB-2 (pilot source video scope). Five
implementation interpretations were reviewed for any effect on sampling,
eligibility, statistics or verdicts.

**What changes:** three sealed input records, rulings on those
interpretations, a non-exposure requirement for annotation state, and
sealing steps. Amendment 3's corpus, applicability, pinned inputs, frame
population, selection, point sampling, swatches, team-identity question,
vocabulary, mapping, judgeability, gate, test, α levels, Stage 2 rules,
verdict precedence, finality and 4b verdict rules are unchanged. Where
Amendment 3 is internally inconsistent (identity), this amendment adopts the
reading given by Amendment 3's own normative definition.

**Why:** no experimental result may depend on an implementation choice.
Implementation choices are not authority; where the tooling disagrees with
this amendment, the tooling is changed, with tests, before Amendment 3
sealing step 2.

### A3-AMB-1: blind-input manifest (normative)

The blind-input manifest is the sole source of `stage_id` and
`battle_mode_id` for the point-sampling domain and for the pilot geometry
requirement.

**Prohibited sources:** pixels or decoded media; filenames or directory
names; any `match_identity.json` (v3 or A1); `evaluation.json` or any other
calibration artifact; `map_observations.json`; classifier output; labels;
comparison results.

**Derivation rule `amendment4-intro-latch-v1`:**

1. Read the pinned v3-replay `vision_manifest.json` and verify its hash.
2. Take every stored detection whose reading has `kind == "match_intro"`,
   parsed as the production `MatchIntroReading`, in ascending frame
   `timestamp` order (ties in stored order).
3. Construct the production `MatchIdentityTracker` with
   `intro_deadline_seconds` equal to
   `vision.match_intro.intro_deadline_seconds` of the pinned
   `configs/default.yaml` (90.0), and call
   `update(reading, video_time=timestamp)` for each reading, read-only.
4. `stage_id` and `battle_mode_id` are the tracker's resulting identity.

Production code is the authority for the latch semantics (the same replay as
`score_fusion.resolve_battle_mode`). No frame is decoded.

**Pinned code identity.** The rule is reproducible only with this code
(SHA-256 of the file bytes; all unmodified at commit `228e037`):

- `src/splatoon3_ai_coach/vision/match_intro.py`
  `f84363f90bdd666d652c52065f481039cd004598a9d9ff86270f17529f51df83`
  (`MatchIdentityTracker`, `MatchIdentity`);
- `src/splatoon3_ai_coach/vision/models.py`
  `2a8d0c4df2271268c0b801e5274a01f6acb4c23c93f508f30f43c45a2954aa37`
  (`MatchIntroReading` and the manifest models that parse stored readings);
- `src/splatoon3_ai_coach/config/models.py`
  `bb1b690ef807a06b3455a39bceb07b0138757ac54d33b6c381b11233a05a9f8d`
  (configuration schema that loads `intro_deadline_seconds`);
- `configs/default.yaml` as pinned in Amendment 3.

The generator records the repository commit, these file digests, and the
Python and pydantic versions. A digest mismatch at generation: STOP. These
files are pinned as-is and must not be modified before the step 2a manifests
are committed.

**Stop conditions:** a pin or code-digest mismatch; a correctness-applicable
recording that latches no `stage_id`; a latched `stage_id` without a pinned
geometry file. A null `battle_mode_id` is allowed and uses the stage default
geometry, as production does.

**Reproduction check:** the comparison tool re-executes the derivation from
the pinned v3 manifests with code matching the pinned digests before any
comparison. Inability to re-execute it, or any difference from the sealed
values: STOP.

**Schema `amendment3-blind-input/v1`** (corpus instance
`amendment3/blind_input_manifest.json`; pilot instance
`amendment3/pilot_blind_input_manifest.json`):

- top level: `schema`, `scope` (`corpus` or `pilot`), `amendment4_commit`,
  `tool_commit`, `rule` (`amendment4-intro-latch-v1`), `code_commit`,
  `code_sha256` (path → digest for the pinned code above), `config_sha256`,
  `python_version`, `pydantic_version`, and for the pilot instance only
  `pilot_source_manifest_sha256`;
- per recording: `recording`, `video_identity`, `video_sha256`,
  `vision_manifest_path`, `vision_manifest_sha256`, `stage_id`,
  `battle_mode_id`, `stage_latched_at`, `battle_mode_latched_at` (or null),
  `intro_reading_count`;
- nothing else: no template scores, hues, calibration values, observations,
  labels, outcomes or video paths.

**Scope:** the corpus instance covers the five correctness-applicable
recordings. Coverage-only recordings need no stage identity and are not
included.

**Blind exposure:** extraction, point-sampling and annotation tools may read
only this manifest's fields, after verifying its committed hash. The
annotation interface never displays it; recordings stay token-blinded.

**Sealing point:** generated by the committed tool after Amendment 3 step 2
and committed before step 3 (step 2a below). Never regenerated; a change
requires an amendment.

**Post-seal discrepancy report (descriptive only):** after a stage's labels,
team-identity answers and gate are sealed, the comparison tool checks whether
`stage_id` / `battle_mode_id` equal the values in the pinned v3 and A1
`match_identity.json`. Any disagreement is reported prominently as a post-seal
discrepancy, with both values and the source hashes. It does not alter the
manifest, domain, labels or any verdict, and is not a stop condition. Pin or
integrity failures and failure of the reproduction check remain STOP.

### A3-AMB-2: pilot-source manifest (normative)

No tool ever searches the filesystem for videos; tools open only declared
paths.

`amendment3/pilot_source_manifest.json` (schema
`amendment3-pilot-sources/v1`) is authored by the operator and committed
before any pilot media is decoded or hashed for processing. Each entry:
`analysis_dir` (repo-relative), `video_path`, `video_sha256` (SHA-256 of the
file bytes), `video_identity` (production `media.video_identity` value),
`authorization` (literal: "development/pilot media; not evaluation, held-out
or corpus media") and `declared_by`.

**Prohibited entries:** the seven corpus recordings (by `video_sha256` and by
`video_identity`); any v3 held-out or fresh v4 held-out media; any capture
package; `_`-prefixed directories, `alignment-v3-heldout`,
`review_validation`; the recording made on 2026-10-24 for the short-match /
timeline observation (not authorized). Held-out exclusion rests on the
operator's declaration; tools do not open held-out records to check it.

**Checks before any decode:** the file at `video_path` matches both
`video_sha256` and `video_identity`; the `vision_manifest.json` in
`analysis_dir` has the same `video_identity`; corpus and directory exclusions
hold. Any failure: STOP, never a silent skip.

**Selection:** Amendment 3's pilot rule is applied to the declared entries
only (stage geometry via the pilot blind-input manifest built by
`amendment4-intro-latch-v1`; at least 8 eligible intervals; verified source
video). Take the two smallest `video_identity` values. Fewer than two: STOP.

### Rulings on implementation interpretations (normative)

1. **Stage 2 trigger.** With the Stage 1 comparison results, the comparison
   tool writes `amendment3/stage2_trigger.json` containing only the SHA-256
   of the Stage 1 comparison record and the sorted ids of the Stage 1
   INCONCLUSIVE recordings. This is the only comparison-derived input any
   blind tool may accept. Blind tools verify its committed hash and require
   the Stage 2 recording set to equal it exactly. Amendment 3's prohibition
   on other comparison outcomes is unchanged.
2. **Observation counts (descriptive only):** the number of top-level
   entries in a directory's `map_observations.json`, reported with the file's
   SHA-256. Files are pinned below; a mismatch means STOP. Blind tools never
   read these files.
3. **Identity:** let N be the number of candidate pixels over all frames of
   the eligible population. If N > 0, `q_all` = differing pixels / N, and
   identity holds iff the count of differing pixels is 0. Hue equality is a
   sufficient condition and a consistency check, not a second necessary
   condition: if the hues differ and no pixel differs, identity holds
   (PRESERVED at precedence 2). If N = 0, `q_all` is reported as
   `undefined`, identity does not hold, and this is not a tool defect; such a
   recording has no judgeable points and is NOT EVALUABLE at precedence 1.
4. **Equal hues with differing pixels:** if the stored `ally_h` and
   `opponent_h` are both equal, N > 0 and any pixel differs, this is a tool
   defect: STOP and report.
5. **Criterion 4a:** inputs for all seven recordings are the A1
   `match_identity.json` (pinned in Amendment 3) and the A1
   `map_observations.json` (pinned below). Per recording: if
   `team_color_calibration` is null, pass iff the file has zero entries;
   otherwise pass iff every entry has `round(video_time, 3) >=
   team_color_calibration.calibrated_at`. 4a is PASS iff all seven pass,
   otherwise FAIL. Pin mismatch or unparsable input: STOP. The report tool
   computes 4a after step 5 and before step 6 (it reads calibration fields,
   so not earlier) and commits `amendment3/criterion_4a.json` (per recording:
   calibration present, `calibrated_at`, entry count, earliest observation
   time, pass/fail). The final report takes 4a only from that record; no
   operator-supplied 4a is accepted.

Pinned `map_observations.json` (SHA-256):

| Recording | A1 run (`analysis/_a1_amendment2_dev/<id>/`) | v3 replay (`analysis/en-live-playerview-2026-10-11 <id>/`) |
|---|---|---|
| 18-18-00 | `eb58f65c4fde629682bc382fba3723eb5de7b6049722fde33a079e4233c04045` | `d770b3dc4bf9357a2ce1b9b0ede673e2b3fabbffa5b870f62236077e200a7df2` |
| 20-33-31 | `779dd9f05dfb02e1df3dbcd8aaf8100fd9c627a8faf76a84d7708fe01af37353` | `6df10e0e1bacba78db7f495b44c1215c5db620f5284c9a08e7fbe337f7a3f133` |
| 22-31-53 | `bc647070c5ced03afe75e98457e170f87e1d64ce293e20697bc9a837128d39b3` | `bc647070c5ced03afe75e98457e170f87e1d64ce293e20697bc9a837128d39b3` |
| 22-37-47 | `add5223303f9beeb94e0f666c856cba569a928748119aacfdadb17477451bb46` | `ccba8e6a40b57641c65015f8ff4d475c04ed8d184d8ca9a6a273b91ddec637aa` |
| 22-57-30 | `4aaffcdc6ece0ebdeb17146f95d10300bd99b0791cf65b3a443b57036c12b363` | `5dd58be6d9df9efc96538d1b96a9d581f63033254c0c600ff0c4792fb2db1698` |
| 18-25-16 | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| 22-20-42 | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` | `0a442f25aff9dd80dc9b85d12d84d683e216bdbbcf3ab4793e61c835a9cf1e32` |

### Annotation state non-exposure (normative)

- Team-identity answers (J1–J4) and the A/B mapping are never returned by any
  annotation route or API response, rendered, or sent to the browser, at any
  time, including on session resume.
- Persistent progress state records, per question, only whether it was
  answered and its position draw. Answers go to a separate answer record that
  the annotation server writes but never reads back into a response; they
  enter the committed session record only at the sealing step (steps 5 and 7).
- Where practical, the stored answer is an opaque internal form (for example
  the chosen on-screen position plus its draw id), resolved to `A` / `B` /
  `cant_tell` only at sealing. No hashing or encryption is required.
- Tests must show that no route response contains an answer or the mapping.

### Sealing order additions (normative)

- Amendment 3 step 1 additionally requires this amendment committed.
- **2a** (after step 2, before step 3), in this order: pilot-source manifest,
  pilot blind-input manifest, corpus blind-input manifest.
- **5a** (after the step 5 gate record, before step 6): `criterion_4a.json`.
- **6a** (with the step 6 results, before step 7): `stage2_trigger.json`, if
  any recording is INCONCLUSIVE.
- Step 8 reads 4a from `criterion_4a.json` and lists any post-seal
  stage-identity discrepancy.

### Disclosures

- Drafted after the Amendment 3 tooling was implemented (uncommitted,
  synthetic tests only). No media was opened, no label exists, no comparison
  was computed and A1 was not compared.
- During drafting: the SHA-256 of the fourteen `map_observations.json` files
  above and of the pinned code files was computed without reading the
  observation contents; the presence of stored `match_intro` readings in the
  five pinned v3 manifests was counted without reading their values;
  production source was read (`match_intro.py`, `match_side_effects.py`,
  `map_ink.py`, `team_color_calibration.py`, `score_fusion.py`).
- The 4a rule was written after the A1 development evaluation reported 4a
  safety PASS, so its result is foreseeable. It operationalizes the sealed
  4a wording and has no tunable parameter.
- The identity ruling was made knowing that A1 changed the 22-37-47
  calibration hue. No `q_all` has been computed for any recording; the ruling
  follows Amendment 3's normative definition (label identity on every
  candidate pixel).
- The Stage 2 trigger lets the annotator infer which recordings were
  INCONCLUSIVE at Stage 1.
- Tracks B and C, Amendments 1–3 and all other Track A criteria are
  unchanged.

## Amendment 5 — Pilot source preparation (2026-10-24)

### What was frozen, what changes, why

**Frozen:** Amendments 1–4 as committed at `570f188`.

A newly recorded pilot source has no `vision_manifest.json`, `video_sha256`
or `video_identity` until it is analyzed and hashed, and the pilot-source
manifest requires all three. Amendment 4 requires that manifest to be
committed "before any pilot media is decoded or hashed for processing", and
Amendment 3 chooses pilot material "before any pilot media is decoded
(hashing excepted)". Together these leave no permitted way to declare a new
source.

**What changes:** a source-preparation phase before the pilot-source
manifest, its boundary with pilot execution, a candidate pool fixed before
preparation, closed technical exclusion conditions, two committed records,
and the three superseded sentences listed below. Nothing else changes:
pinned stages and inputs, eligibility, the ≥8 eligible-interval
requirement, selection of the two smallest `video_identity` values,
prohibited entries and exclusions, the pilot procedure, annotation,
comparison, thresholds, criteria, STOP conditions, blinding and
non-exposure, and the sealing order from the pilot-source manifest onward
are all as in Amendments 3 and 4.

**Why:** a source can only be declared after it is analyzed and hashed.
Preparation must not become a chance to choose sources by their
experimental behaviour.

### Source preparation and pilot execution (normative)

**Source preparation** consists of exactly these operations on a pool
candidate, and nothing else:

1. **Hashing:** SHA-256 of the video file bytes, and the production
   `media.video_identity` value (byte read, no decode).
2. **Production analysis:** one run of
   `s3-coach analyze <video_path> --out <analysis_dir>` with the pinned
   `configs/default.yaml`, no other options, at a recorded commit whose
   `src/` and `configs/` are unmodified, creating the source's
   `vision_manifest.json`. The command, code, configuration and options are
   identical for every candidate.

These operations are preparation, not execution of the Amendment 3/4 pilot.

**Pilot execution** is everything from the pilot-source manifest onward,
including: pilot-specific frame extraction; eligible-interval computation;
stage-identity derivation (`blind-inputs`); pilot selection
(`pilot-select`); point sampling; annotation and any use of the annotation
interface; any A1, v3 or reference comparison; criterion 4a; Stage 1 and
Stage 2.

**Prohibited during preparation** (until the pilot-source manifest is
committed): every pilot-execution operation above, and inspecting or using
any analysis output beyond the read allowance below.

**Read allowance during preparation:** only the analysis exit status;
whether `vision_manifest.json` exists; its top-level `video_identity`; and
its SHA-256. Console output goes to a log file, which is read only after a
non-zero exit, and only for the failure message. These stay unread until
the pilot-source manifest is committed:

- every other part of the vision manifest (readings including
  `match_intro`, latched stage or mode, snapshots, events, frame results);
- `match_identity.json` and any calibration value;
- `map_observations.json`;
- scenarios and scenario contexts;
- debug frames;
- viewer output.

No frame is displayed and the video is not played.

**One analysis per candidate.** A run that exits 0 and writes
`vision_manifest.json` is final; it is never re-run, replaced or edited. A
run that did not finish for an environmental reason unrelated to the
source (process killed, disk full, out of memory) may be repeated with the
same command after its partial output is deleted; every attempt and its
reason are recorded. No setting may change between attempts or candidates.

### Candidate pool (normative)

The candidates are fixed by acquisition-time facts only, before any
preparation operation touches any of them.

`amendment3/pilot_candidate_pool.json` (schema
`amendment5-pilot-candidate-pool/v1`) is authored by the operator and
committed after this amendment and before any preparation operation.
Top level: `amendment5_commit`, `declared_by`. Each entry:

- `candidate_id`;
- `video_path` (repo-relative);
- `analysis_dir` (repo-relative, directly under `analysis/`, not yet
  existing);
- `recorded_stage` (the stage the operator played, as known at
  acquisition);
- `recorded_on`;
- `authorization` (the Amendment 4 literal).

Nothing else, and no value derived from opening the file.

Amendment 4's prohibited entries apply to the pool unchanged. In
particular, the recording made on 2026-10-24 for the short-match / timeline
observation is not a candidate. The pool closes at its commit. Adding a
candidate requires a new amendment.

**No outcome-dependent removal.** Every pool candidate ends in exactly one of
two states:

- declared in the pilot-source manifest; or
- excluded under a technical exclusion condition below.

No other removal is allowed: not for operator preference, apparent quality,
duration, gameplay, or any expected or observed experimental behaviour.
Eligibility (stage latch, ≥8 eligible intervals, verified source) is
applied only at pilot selection, to declared entries, as in Amendment 4.

### Technical exclusion conditions (normative, closed list)

A candidate is excluded before declaration only for:

- **T1 unreadable source:** the file at `video_path` does not exist or
  cannot be read to the end for hashing.
- **T2 no manifest:** production analysis does not exit 0 with a
  `vision_manifest.json` (after any environmental repeats allowed above).
- **T3 unpinned recorded stage:** `recorded_stage` in the committed pool is
  not one of the stages with pinned geometry (`makomart`,
  `shipshape_cargo_co`, `manta_maria`, `wahoo_world`).

Each exclusion is recorded with the condition, the failing operation, the
exit status or error message, the SHA-256 of the log file (T2), the commit
and the time. The excluded candidate's outputs are kept and never used.

These findings during preparation are STOP, not exclusion:

- `vision_manifest.json` `video_identity` differs from the independently
  computed `video_identity`;
- two candidates have the same `video_sha256` or `video_identity`;
- a candidate matches a corpus recording, is a capture package or lies
  inside one, or falls under any other Amendment 4 prohibited entry.

Stage identity derived from the stored intro readings is not examined
during preparation. After declaration, a latched `stage_id` without pinned
geometry remains a STOP of pilot blind-input generation (Amendment 4,
unchanged), and a source that latches no stage is ineligible.

### Preparation record (normative)

`amendment3/pilot_source_preparation.json` (schema
`amendment5-pilot-source-preparation/v1`) is committed after preparation
and before the pilot-source manifest.

Top level:

- `pool_sha256`;
- `code_commit`;
- `config_sha256`;
- `analyze_command` (template);
- `python_version`.

Per candidate:

- `candidate_id`;
- `status` (`prepared` or `excluded`);
- `video_sha256`, `video_identity`, `vision_manifest_sha256` and
  `manifest_video_identity` (each null where not produced);
- `attempts` (exit status and any environmental reason);
- `exclusion` (as above, or null).

Nothing else: no readings, stage, mode, counts, hues, observations, timings
or outcomes.

**Consistency (operator-verified, any failure STOP):**

- The pilot-source manifest declares exactly the `prepared` candidates, with
  their `video_path`, `analysis_dir`, `video_sha256` and `video_identity`
  equal to this record.
- The pilot blind-input manifest's `vision_manifest_sha256` equals this
  record.

### Superseded sentences (normative)

1. Amendment 3, Pilot material: "before any pilot media is decoded (hashing
   excepted)". This now reads: before any pilot-execution operation, with
   Amendment 5 source preparation (hashing and one production analysis)
   excepted.
2. Amendment 4, A3-AMB-2: "committed before any pilot media is decoded or
   hashed for processing". This now reads: committed after the Amendment 5
   preparation record and before any pilot-execution operation.
3. Amendment 4, A3-AMB-2: "Checks before any decode". This now reads:
   checks before any pilot-execution decode. The checks themselves and
   their STOP are unchanged.

Amendment 4's "No tool ever searches the filesystem for videos; tools open
only declared paths" is unchanged. Preparation opens only paths in the
committed pool.

### Sealing order additions (normative)

Within Amendment 4 step 2a, before the pilot-source manifest, in this order:

1. this amendment committed;
2. candidate pool committed;
3. source preparation;
4. preparation record committed.

Step 2a then continues unchanged with the pilot-source manifest, pilot
blind-input manifest and corpus blind-input manifest.

### Disclosures

- Drafted after the Amendment 3/4 tooling was committed (`f636a6e`) and
  before any candidate recording was opened, hashed, decoded or analyzed.
- At drafting, the operator had stated five candidate recordings by
  acquisition facts only: three MakoMart, one Manta Maria and one Wahoo
  World. No other property of them was known to the drafter.
- The operator played the matches and so has gameplay knowledge from
  acquisition. The pool is fixed by acquisition facts for that reason.
- Production analysis writes A1 calibration and map-ink outputs into each
  `analysis_dir`. They are not read before the pilot-source manifest is
  committed, and blind tools never read them (Amendment 3).
- No tool or test was changed for this amendment. Tracks B and C,
  Amendments 1–4 and all other Track A criteria are unchanged except for
  the three sentences superseded above.

## Amendment 6 — Preregistration identity and execution chain (2026-10-25)

### What was frozen, what changes, why

**Frozen:** Amendments 1–5 as committed at `ca4e088`, and the candidate
pool committed at `29f9f5a`.

The committed Amendment 3/4 tooling (`f636a6e`) pins the SHA-256 of the
whole preregistration file to its Amendment 4 value. Appending Amendment 5
grew the file, so the tool's precondition check now fails. Every real
step 2a command runs that check. This was found after sealing, before any
Amendment 5 preparation operation; no candidate was accessed. Amendment 3
requires a tool defect found after sealing to be fixed only by an
amendment.

**What changes:** the meaning of the preregistration pin, and one
authorized tooling repair. Nothing else changes: Amendments 1–5, every
other pin, schema, record, rule, threshold, criterion, STOP condition and
blinding requirement stay as committed.

**Why:** an additive, sealed amendment does not change what an earlier
amendment said. The tooling should be able to prove that, and should still
refuse to run under text that nobody authorized for execution.

### Historical identity (normative)

Each sealed amendment is identified by its commit and by the SHA-256 of
the preregistration file bytes at that commit. These identities are
immutable and verifiable on their own from the repository:

| Record | Commit | File SHA-256 |
|---|---|---|
| Preregistration through Amendment 4 | `570f188c3a2322c20065b095d740a6ad2538c57a` | `4ffeeaf899185d84beea1e97c59b0baa46798cb7390cf294fae3343804e757e1` |
| Preregistration through Amendment 5 | `ca4e08821ca9c80f45125c168fe3caf3af536a3e` | `2a0a23ba0d4db8484eb7752861384546855dc65374d975580c7d11a77df6cb57` |
| `amendment3/pilot_candidate_pool.json` | `29f9f5a8066e531f9bf3d58d3b0585ea5c2617ec` | `5ff846ad71fb7f782b8fd6fffa16dea5ae13f585a248d37382a5838195827a12` |

A later amendment that only appends text leaves every earlier identity
intact: the earlier file is a byte prefix of the later one, and the earlier
commit and digest still verify. Growth of the file is not a change to an
earlier amendment.

Amendment 4's `amendment4_commit` field in the blind-input manifests keeps
its meaning and value (`570f188…`).

### Execution authorization (normative)

Historical identity does not authorize execution. A run is authorized only
under an explicit **authorized execution chain**: an ordered list, held in
the tooling, of (amendment, commit, file SHA-256). For this experiment the
chain is:

1. Amendment 4: `570f188…`, `4ffeeaf8…`;
2. Amendment 5: `ca4e088…`, `2a0a23ba…`;
3. Amendment 6: its sealing commit and the file SHA-256 at that commit,
   recorded in the tooling by the repair below.

Before any real run, the tooling verifies all of the following. Any
failure: STOP.

- Each chain commit exists and is an ancestor of `HEAD`, each strictly
  after the previous one.
- The preregistration file bytes at each chain commit have the listed
  SHA-256.
- Each chain entry's file is a byte prefix of the next entry's file
  (additive only).
- The working-tree file is committed, unmodified, and has exactly the
  SHA-256 of the last chain entry.

`HEAD` need not be the last chain commit. It may be any descendant of it,
for example the authorized tool-repair commit, provided the current
preregistration file is byte-for-byte identical to the file at the last
chain commit.

So a later amendment, whether committed or not, makes the current file
differ from the last chain entry, and every real run STOPs. It is not
authorized because it is appended, committed, or later in history.

A later amendment joins the chain only if:

- it says so explicitly;
- it is sealed; and
- the tooling is then changed, under that amendment's authorization, to
  list its commit and digest.

"Accept the latest file" and "accept any descendant commit" are not
authorization rules.

The candidate pool's identity above is checked by the operator, as are
Amendment 5's other consistency checks, before the first preparation
operation. A difference: STOP.

### Tool repair authorization (normative)

After this amendment is committed, one tooling commit may make exactly
these changes under `tools/diagnostics/map_ink_reference/` and
`tests/map_ink_reference/`:

- `constants.py`: replace the single preregistration pin with the
  authorized execution chain above, keeping `AMENDMENT4_COMMIT` and its
  value.
- `seal.py`: `verify_preconditions` verifies the chain as specified above
  instead of a single whole-file digest. The A1 implementation pins and all
  other checks are unchanged.
- Tests that show:
  - the Amendment 4 historical identity still verifies;
  - the chain verifies at the repaired commit;
  - appended uncommitted text STOPs;
  - a committed amendment that is not in the chain STOPs;
  - an edit to earlier text (not a byte prefix) STOPs;
  - a wrong digest or out-of-order commit STOPs.

  Existing tests that set the old single pin are updated to the chain.

Nothing else may change, including:

- `src/` and `configs/`;
- A1 code and tests;
- pinned artifacts and committed records;
- the blind-input and pilot-source schemas;
- pilot selection, eligibility, extraction, sampling, annotation,
  comparison and report logic.

The full map-ink reference test suite must pass before the repair is
committed. The repair commit is recorded as the tool commit for every
later real run. `f636a6e` stays the step 2 tool commit of record.

### Sealing order additions (normative)

After the candidate pool (Amendment 5 sub-step 2), in this order:

1. this amendment committed;
2. the tool repair committed, with its commit and the Amendment 6 chain
   entry reported;
3. `preconditions` passes at the repaired commit.

Amendment 5 sub-step 3 (source preparation) and everything after it
follow, unchanged.

### Disclosures

- Drafted after `preconditions` failed on the whole-file pin and before any
  candidate video was hashed, opened, decoded or analyzed. No
  `analysis_dir` exists for any candidate.
- During drafting, the preregistration file was read from commits
  `570f188` and `ca4e088` to confirm the digests above and that the
  Amendment 4 file is a byte prefix of the Amendment 5 file.
- Amendment 5's drafter did not foresee that appending an amendment breaks
  the tool's whole-file pin.
- No tool or test was changed for this amendment. Tracks B and C,
  Amendments 1–5 and all other Track A criteria are unchanged.

## Amendment 7 — Pilot execution path and execution guards (2026-10-25)

### What was frozen, what changes, why

**Frozen:** Amendments 1–6 as committed at `85d1104`, the Amendment 6 tool
repair `a350578`, and the sealed records: candidate pool (`29f9f5a`),
preparation record (`acd6428`), pilot-source manifest (`53a8f58`), pilot
blind-input manifest (`c16601f`) and corpus blind-input manifest
(`a1ea2fd`).

Amendment 3 step 3 is "the full Stage 1 procedure (selection, extraction,
points, swatches, team identity, labels, duplicates, gate)" on the two
pilot recordings. The committed tooling implements only pilot selection
(`pilot-select`). Its `plan`, `extract`, `points`, `annotate`, `export` and
`gate` commands accept only the five correctness-applicable recordings, are
keyed to the Amendment 3 media pins, and write the corpus Stage 1 record
paths. Step 3 therefore cannot be completed with the sealed tooling. This
was found in a read-only procedural review after step 2a was sealed and
before any step 3 operation. Amendment 3 requires a tool defect found after
sealing to be fixed only by an amendment.

The same review found two smaller gaps:

- Only `preconditions` and `blind-inputs` run the sealed precondition
  verifier, although Amendment 6 says "Before any real run, the tooling
  verifies all of the following".
- Nothing in the tooling stops corpus Stage 1 work before the step 3 pilot
  addendum is committed.

**What changes:** a pilot-scoped execution path for step 3 built from the
existing Stage 1 functions; a separate pilot record namespace; a
mandatory STOP on a failing pilot gate; at most one post-pilot interface
commit; a pilot addendum record and its verification; one execution guard
on every command; a tool identity rule; production dependency pins; the
meaning of Amendment 3's interface bug fixes; this amendment's entry in the
authorized execution chain; one authorized tooling repair; and one
superseded sentence.

**Unchanged:** the five-source pool and every sealed record; the
`amendment4-intro-latch-v1` rule; the pilot eligibility rule, the minimum
of 8 eligible intervals and selection of the two smallest `video_identity`
values among eligible declared sources; stage geometry; frame population,
intervals and selection; extraction; point sampling, seeds and the
duplicate rule; swatches, the team-identity question, cue material and cue
order; vocabulary, blinding and display rules; the gate population,
classification and 0.10 cap; no calibrated classifier on pilot frames and
no pilot comparison; the allowed post-pilot change list; the
unworkable-frozen-element STOP; and all corpus Stage 1 and Stage 2,
comparison, criterion, threshold, verdict and non-exposure rules.

**Why:** to make the already-preregistered pilot executable before any
pilot information is observed, without redesigning it.

### Pilot record namespace (normative)

- Committed pilot records: `analysis/review_validation/alignment/v4/amendment3/pilot/`
  (P below).
- Working pilot output (gitignored): `analysis/_amendment3/pilot/` (W
  below): PNGs in `W/frames/<recording>/`, session state in
  `W/session_stage1/`.
- Corpus records and working output keep their Amendment 3/4 paths. No
  pilot command writes outside P and W. No corpus command reads or writes
  inside them.
- Every pilot record carries `"scope": "pilot"`. Pilot readers require it;
  corpus readers reject any record that carries it. Corpus record formats
  are unchanged.
- A pilot recording id is its analysis directory name, as in the pilot
  blind-input manifest. Corpus commands keep their fixed recording choices
  and exact-set checks.
- No pilot record is regenerated. A pilot command whose output already
  exists: STOP.
- Committing a pilot record does not authorize inspecting its substantive
  contents. Contents are read only by the tooling, at the steps that need
  them. The operator sees only what the exposure lines below allow.

### Shared semantics (normative)

Every scientific step of the pilot calls the function the corpus Stage 1
path calls: `select_pilot`; `build_frame_plan` (with `purpose="pilot"`);
`extract_recording`; `sample_stage_points` and `duplicate_point_ids`;
`AnnotationSession`, `serve` and the annotation page;
`export_session_records`; and the `stage_pairs` / `evaluate_gate` logic. The
repair may move the scope-specific parts of `pipeline_blind` (records
directory, working directory, recording set, source identity, stage
identity source, prerequisites) into a scope object. Corpus behaviour stays
unchanged.

Complete list of pilot–corpus differences:

1. **Recordings:** exactly the two `chosen` recordings of the sealed pilot
   selection, in that order (corpus: the five correctness-applicable
   recordings).
2. **Source identity:** `vision_manifest_path`, `vision_manifest_sha256`,
   `video_sha256` and `video_identity` from the sealed pilot-source and
   pilot blind-input manifests, which equal the preparation record (corpus:
   Amendment 3 media pins).
3. **Video path:** the declared `video_path` of the sealed pilot-source
   manifest; there is no `--video` option (corpus: an operator-supplied
   path verified against the pin).
4. **Stage identity:** the sealed pilot blind-input manifest (corpus: the
   corpus blind-input manifest).
5. **Stage 1 only:** no Stage 2 or trigger; no judgeability or evaluability
   computation; no mapping, classifier, comparison, `q_all`, criterion 4a,
   verdict or report. The plan's `stage2_interval_indices` are computed by
   the shared function and never read.
6. **Plan purpose:** `"pilot"`.
7. **Paths and the `scope` field,** as above.
8. **Batching:** `pilot-plan` and `pilot-extract` take no recording
   argument and process both chosen recordings in selection order (corpus:
   one command per recording).
9. **Pilot selection record:** committed (previously working output).
10. **Pilot gate consequence:** a failing pilot gate is a mandatory STOP,
    with no correctness or criterion verdict consequence (below).
11. **Pilot addendum:** as below.

### Pilot execution chain (normative)

Commands run as `../../.venv/bin/python -m map_ink_reference <command>`
from `tools/diagnostics/`. Every command first passes the execution guard.
"Sealed" means committed and unmodified. Commit groups G1–G6 are defined in
the sealing order below.

1. **Selection:** `pilot-select` (no options).
   - Inputs: the three step 2a records; each declared source's
     `vision_manifest.json` and video (hashing only, no decode); the seven
     corpus manifests (identity only); exactly as now.
   - Output: `P/pilot_selection.json`, the existing selection payload
     (`pilot_source_manifest_sha256`, `declared`, `chosen`, `tool`) plus
     `scope`. Committed in G1.
   - Prerequisite: step 2a sealed in order.
   - STOP: the existing declared-source, hash and binding checks; fewer
     than two eligible sources.
   - Exposure: the file holds, per declared source, stage, mode, eligible
     interval count and eligibility, and the two chosen recordings. The
     console shows only the output path and its SHA-256.
2. **Frame plans:** `pilot-plan` (no options).
   - Inputs: the sealed selection; each chosen recording's vision manifest,
     verified against its pilot blind-input `vision_manifest_sha256`.
   - Output: `P/frame_plans/<recording>.json`. Committed in G2.
   - Prerequisite: G1 sealed strictly after the corpus blind-input
     manifest.
   - STOP: hash mismatch; the existing plan checks.
   - Exposure: frame and interval metadata in the files; the console shows
     paths only. No decode.
3. **Extraction:** `pilot-extract` (no options).
   - Inputs: both plans; the declared `video_path`, `video_sha256` and
     `video_identity`.
   - Output: PNGs in `W/frames/<recording>/` (working); provenance in
     `P/extraction/<recording>.json` (committed in G2).
   - Prerequisite: G1 sealed; a plan for each chosen recording, bound to
     that recording's verified vision manifest digest. Before decoding, the
     Amendment 4 checks (as amended by Amendment 5) are re-run for both
     chosen sources (`check_declared_source`, `verify_source_video`).
   - STOP: any failed check; the existing extraction checks (video hash and
     identity, frame index, size, lossless round trip, missing frames).
   - Exposure: decodes the two chosen pilot videos only. Nothing is
     displayed.
4. **Points and duplicates:** `pilot-points` (no options).
   - Inputs: plans, extraction provenance, hash-verified PNGs, stage
     identities from the sealed pilot blind-input manifest, and pinned
     geometry.
   - Output: `P/points_stage1.json`, the corpus structure with
     `pilot_blind_input_manifest_sha256` in place of
     `blind_input_manifest_sha256`, plus `P/duplicates_stage1.json`. Both
     committed in G2.
   - STOP: PNG hash mismatch; geometry pin failure; a chosen recording
     without a latched stage.
   - Exposure: point coordinates in the files.
5. **Annotation (swatches, team identity, labels, duplicate copies):**
   `pilot-annotate [--port]`.
   - Inputs: sealed G2 records and hash-verified PNGs.
   - Output: `W/session_stage1/` (state and append-only answer record).
   - Semantics: the unchanged Stage 1 session (swatches, J1, one shuffled
     queue with the rule's duplicates, J2), with the pre-pilot display
     defaults.
   - Prerequisite: G2 sealed strictly after G1.
   - Exposure: only the blinded interface: pilot frames, crops, swatches,
     tokens and progress. No recording names, timestamps, stage, hues,
     classifier output, answers or mapping.
6. **Export:** `pilot-export` (no options).
   - Output: `P/labels_stage1.json`, `P/team_identity_stage1.json`,
     `P/queue_stage1.json` and `P/swatches.json` (G3); `P/token_key.json`
     and `P/queue_key_stage1.json` (G4).
   - Prerequisite: a complete session; G2 sealed.
   - Exposure: the files hold the pilot labels and resolved J1/J2 answers.
     The console shows file names only.
7. **Gate:** `pilot-gate` (no options).
   - Inputs: G2–G4, with the existing order checks (labels before keys).
   - Output: `P/gate_stage1.json` (the frozen Stage 1 gate result). Committed
     in G5 whether the gate passes or fails.
   - Exposure: numerator, denominator, bound and pass/fail, in the file
     and on the console. This is the preregistered pilot decision point.
   - A failing gate: STOP after G5 (see Pilot gate).
8. **Post-pilot interface change (optional):** no command. At most one
   commit, only after a sealed passing G5, and only for the allowed-change
   items 1–6 of Amendment 3. If no allowed change is needed, there is no
   such commit and the addendum records `interface_commit: null`.

   **Permitted file set (exact):**
   - `tools/diagnostics/map_ink_reference/constants.py`: only the values
     of `DEFAULT_CROP_SIZE` and `DEFAULT_ZOOM`, within the frozen limits.
     The module's syntax tree, with those two values masked, must equal
     its syntax tree at the G5 commit;
   - `tools/diagnostics/map_ink_reference/render.py`;
   - `tools/diagnostics/map_ink_reference/server.py`;
   - `tools/diagnostics/map_ink_reference/annotate.html`;
   - `tools/diagnostics/map_ink_reference/session.py`, only within the
     allowlist below;
   - new test files under `tests/map_ink_reference/`. Existing test files
     are not modified or deleted.

   Any other path, any sealed record or any file outside these
   directories: STOP.

   **`session.py` allowlist.** Only two regions may change:
   1. the names imported by the single
      `from map_ink_reference.render import (...)` statement, where names
      may only be added;
   2. the dict literal returned by `AnnotationSession._label_task`, where
      key/value entries may only be appended after the existing ones.

   Each appended entry is one of:
   - a string key whose value is a literal constant (string, number,
     boolean, None, or a list or tuple of these), for presentation
     settings covered by items 2, 3 or 6;
   - a string key whose value has exactly the form
     `self._handle(lambda: encode_png(F(self._load(asset), point.x,
     point.y, settings)))`, where `F` is a function imported from
     `render.py` (the context thumbnail of item 2, or an item 6
     presentation fix).

   Machine verification:
   - Masking those added import names and appended entries, the syntax
     tree of `session.py` equals its syntax tree at the G5 commit.
   - Existing entries of the returned dict, every other statement,
     method, class, constant and import, the state machine,
     submissions, draws, the answer record and saved state are all
     unchanged.

   `session.py` may not be used to change annotation semantics, data or
   provenance structures, gate behaviour, sampling, selection, extraction,
   or any other frozen scientific behaviour.

   **Item → file map:**
   - `crop_zoom` (1): `constants.py`;
   - `marker` (2), covering marker shape, colour and thickness and whether
     a context thumbnail is shown: `render.py`, `session.py`
     (allowlist only), `annotate.html`;
   - `layout` (3): `annotate.html`, `session.py` (allowlist only);
   - `instructions` (4), `team_cue_wording` (5): `annotate.html`;
   - `interface_fix` (6): `annotate.html`, `server.py`, `render.py`,
     `session.py` (allowlist only).

   Every changed non-test file must be covered by a listed item.
   `crop_zoom` is listed if and only if either value differs from 96 / 4.
   `interface_fix` requires at least one added test file. The full map-ink
   reference suite, `ruff check` and `ruff format --check` pass at the
   interface commit.

   **The interface commit may not:**
   - repair a failed gate;
   - repair a newly discovered tooling defect;
   - alter frozen scientific semantics;
   - alter selection, eligibility, extraction, sampling, annotation,
     display (no pixel alteration, grey padding, integer nearest-neighbour
     zoom, uncovered centre pixel) or gate semantics;
   - alter sealed pilot records.

   Item 6 is limited as stated in "Interface bug fixes" below. Any other
   need: STOP, and the applicable amendment process.
9. **Pilot addendum:**
   `pilot-addendum (--none | --item ITEM [--item ITEM ...] --interface-commit SHA)`.
   - Output: `P/pilot_addendum.json`. Committed alone in G6.
   - Prerequisite: G1–G5 sealed in order, with a passing G5. With items,
     the interface-commit conditions hold. A failing G5: STOP, and no
     addendum is written.
   - Exposure: nothing new.

### Pilot addendum (normative)

`P/pilot_addendum.json`, schema `amendment7-pilot-addendum/v1`, has exactly
these keys:

- `schema`, `scope` (`"pilot"`), `amendment7_commit`, `tool_commit` (HEAD
  at generation);
- `pilot_records`: for exactly the records of G1–G5, path relative to P →
  SHA-256;
- `changes`: allowed-change item ids from this closed list, unique and in
  this order: `crop_zoom` (1), `marker` (2), `layout` (3), `instructions`
  (4), `team_cue_wording` (5), `interface_fix` (6). The empty list
  represents "none";
- `interface_commit`: null if and only if `changes` is empty; otherwise the
  40-hex commit of the single post-pilot interface commit.

`"changes": []` with `"interface_commit": null` is the only representation
of "none". A non-empty `changes` requires the interface commit, and the
listed items must agree with that commit under the item → file map of
operation 8.

Nothing else: no labels, answers, counts, gate values or pass/fail,
recording names, eligibility, timings or free text. The addendum is a JSON
record, not text appended to this preregistration, which stays
byte-identical to the last chain entry.

**Verification** (before every corpus command; any failure STOP):

1. The addendum is sealed and has exactly the schema above.
2. Every listed record is sealed with the listed digest. The listed set
   equals the required G1–G5 set for the two sealed chosen recordings, and
   every listed record has `"scope": "pilot"`.
3. G1 to G6 are each committed strictly after the previous group, by
   ancestry. Records of one group share one commit.
4. `amendment7_commit` equals this amendment's chain entry.
5. The sealed G5 gate record is a passing gate.
6. `changes` is empty if and only if `interface_commit` is null.
7. If `interface_commit` is set, it is strictly after G5 and an ancestor
   of the G6 commit, and it meets every condition of operation 8: the
   permitted file set, the `constants.py` and `session.py` syntax-tree
   checks, the item → file map, and the added test for `interface_fix`.
8. If `crop_zoom` is absent, `DEFAULT_CROP_SIZE` is 96 and `DEFAULT_ZOOM`
   is 4.

### Pilot gate (normative)

The pilot gate is computed and recorded with the frozen Stage 1 gate
procedure: population D_1 of the pilot point manifest, the frozen pair
classification, and the 0.10 cap on the upper one-sided 95%
Clopper–Pearson bound, where a zero denominator fails.

- **PASS:** step 3 may continue to the optional interface commit and G6.
  A passing gate is required for both.
- **FAIL:** a mandatory STOP. The gate record is still committed as G5.
  No interface commit and no addendum follow, and step 3 does not
  complete, unless a new authorization or amendment explicitly resolves
  the failure. The failure has no correctness or criterion verdict
  consequence. It means the frozen pilot procedure has shown a
  workability problem, to be resolved separately under the applicable
  amendment process and Amendment 3's unworkable-frozen-element rule.

Amendment 3's unworkable-frozen-element STOP also applies after a passing
gate. Pilot labels, answers and gate values never enter corpus records,
comparison or verdicts.

**No classifier, no comparison.** In either branch, no calibrated
classifier is applied to any pilot frame and no pilot comparison is
computed. No pilot command, record or verification invokes or reads the
classifier, a calibration, map-ink observations, mapping, `q_all`,
criterion 4a or any comparison or report output.

**Effective sequences:**

- **Success:** G1 selection, then G2 plans, extraction and points, then
  annotation, then G3 export, then G4 token and queue keys, then G5 gate
  PASS, then the optional single authorized interface commit, then G6
  pilot addendum, then STOP. Step 3 is complete.
- **Failure:** G5 gate FAIL, the gate record sealed, then STOP.

### Execution guard and tool identity (normative)

The command dispatcher applies one common guard to every command before
anything else, so no command can be registered without it:

1. `verify_preconditions`: the authorized execution chain (now including
   this amendment) and the A1 implementation pins;
2. clean tooling;
3. the tool identity rule below;
4. the production dependency pins below.

In addition:

- Pilot commands: STOP if `P/pilot_addendum.json` exists, or if a sealed
  G5 gate record is a failing gate.
- `plan`, `extract`, `points`, `annotate`, `export`, `gate`,
  `criterion-4a`, `compare` and `report`: the addendum verification above
  must pass. `plan` is the earliest corpus Stage 1 entry point.
- Running `preconditions` separately is not a substitute for the guard.

**Tool identity rule.** Protected paths are
`tools/diagnostics/map_ink_reference/` and `tests/map_ink_reference/`.
The rule applies from C7 until the step 8 final report is committed.

*Bootstrap (no commit contains its own hash):*

- C7 is this amendment's sealing commit. The repair records it as chain
  entry 4. The repair comes after C7, so it can contain C7's hash.
- C7 changes only the preregistration file.
- Between C7 and the repair, the committed tooling still holds the
  Amendment 6 chain. The current preregistration differs from that
  chain's last entry, so every command STOPs. No run is possible in that
  interval.
- The commits from C7 (exclusive) to HEAD must form a linear history with
  no merge commits.
- Let T be the commits in that range that change any protected path,
  oldest first.
- The repair is T's first element. No other recognition is needed. It
  must:
  - change only the files of the tool repair authorization;
  - have, in its own tree, a chain whose entry 4 names C7 and C7's
    preregistration digest;
  - be committed strictly before G1.

*Allowed history:*

- T is exactly the repair; or
- T is the repair followed by one interface commit I, which:
  - is strictly after a sealed passing G5;
  - meets operation 8;
  - once G6 is sealed, equals the addendum's `interface_commit`.
- With a null `interface_commit`, T stays exactly the repair.
- Any other history, including a third commit or a commit after G6 that
  touches a protected path: STOP.

Records keep recording HEAD as `tool_commit`.

### Production dependency pins (normative)

Frame selection, extraction and point sampling, for both pilot and corpus,
call production code that no earlier amendment verifies at every command.
The guard verifies these seven files at every command. For each, the
committed file and the working-tree file must both equal its pinned
SHA-256:

| File | SHA-256 | Source of the pin |
|---|---|---|
| `src/splatoon3_ai_coach/vision/map_ink.py` | `50f0dba3e0e004dc17096cff6d5825a496cd917d03517de00c08402150070ab3` | Amendment 3 (unchanged) |
| `src/splatoon3_ai_coach/config/models.py` | `bb1b690ef807a06b3455a39bceb07b0138757ac54d33b6c381b11233a05a9f8d` | Amendment 4 (unchanged) |
| `src/splatoon3_ai_coach/vision/stage_maps.py` | `f443daff9fe4a142a3b22821f613a5d2c01949644c23a20fa0ca923a08adc895` | this amendment |
| `src/splatoon3_ai_coach/vision/stage_mask.py` | `8d756f99368c109dd69d6847680ba54c68b269a298c3312c8a798731c4a00406` | this amendment |
| `src/splatoon3_ai_coach/media/video.py` | `6285c206ae2af3f20df2398e6b65307ce664ec4d29506ee41a1721d79a238b87` | this amendment |
| `src/splatoon3_ai_coach/media/video_identity.py` | `895794741d8ba5698cfbda4d5da98f588e46a1b74358262d713e336d0b52017b` | this amendment |
| `src/splatoon3_ai_coach/types.py` | `ad6f423fc8a5422a5febc66972143e3d5c322c5a19210bdc29071188569983ea` | this amendment |

- The Amendment 3 and Amendment 4 pins are not replaced. They are also
  verified by this guard, in addition to their existing checks.
- The five new values were computed from the committed tree at
  `a1ea2fddcd520e62ccf265d7eb8b3364cabecb33`. They identify both the
  versions in C7's parent and those in C7's tree.
- C7 changes only the preregistration file and none of these seven files.
  If C7's parent holds different bytes for any of them: STOP before
  sealing.
- A mismatch at any command: STOP. A mismatched file is never restored or
  repaired automatically.
- The repair may add verification of these pins. It may not modify any of
  the seven files. The pins are defined as literals in `execution.py`.
  Tests show that the `map_ink.py` value equals the Amendment 3 pin in
  `unblinded_pins.py`, and the `config/models.py` value equals the
  Amendment 4 pin in `pins.py`.

**Dependency-closure boundary.** The guard covers the sealed first-party
files that Amendments 3–7 identify as directly affecting successful-path:

- source and video identity;
- media decoding used by the frozen procedure;
- stage and map geometry;
- frame, interval and point derivation;
- point-sampling semantics;
- relevant sealed configuration-model semantics.

It does not recursively pin every imported Python module. Exception-only
and support modules that do not affect successful-path experimental
values are outside this pin set. In particular,
`src/splatoon3_ai_coach/exceptions.py` (imported by `media/video.py`) was
reviewed: it contributes exception definitions only and is outside the
pin set. Third-party libraries and the environment are treated exactly as
Amendment 3 specifies. This amendment adds no third-party version
requirement. The boundary removes no pin required by Amendments 3–7.

### Interface bug fixes (normative)

Amendment 3's "interface bug fixes" (allowed-change item 6) means only
defects in the presentation or interaction of the human annotation
workflow. It does not authorize changes to:

- scientific semantics;
- source, interval, frame or point derivation;
- extraction or sampling;
- team-identity meaning, labels or duplicate semantics;
- gate logic or thresholds;
- comparison behaviour;
- provenance or execution guards.

A newly discovered defect outside this allowance is a tooling or
specification defect: STOP, and the applicable amendment process.

### Authorized execution chain (normative)

This amendment explicitly joins the Amendment 6 authorized execution chain
as entry 4: its sealing commit and the file SHA-256 at that commit,
recorded in the tooling by the repair below. Every Amendment 6 rule
applies unchanged.

### Tool repair authorization (normative)

After this amendment is committed, one tooling commit may make exactly
these changes:

- `constants.py`: add the chain entry and the pilot namespace, schema and
  allowed-item constants. Existing values unchanged.
- `cli.py`: the dispatcher guard; the pilot commands with only the options
  above; the pilot selection output path and console line. Corpus command
  options unchanged.
- `seal.py`: the tool identity rule. Existing functions keep their
  behaviour.
- `pipeline_blind.py`: the scope object. Corpus behaviour unchanged.
- `records.py`: an optional `scope` field for pilot records. Corpus output
  unchanged.
- New modules `execution.py` (guard), `pilot_pipeline.py` and
  `pilot_addendum.py`, each assigned to an isolation-test module group, and
  the module list in `__init__.py`.
- Under `tests/map_ink_reference/`: a new `test_mir_amendment7.py`,
  synthetic fixtures and helpers. Existing tests change only where the
  chain, module groups, command list or options, or the selection output
  path change.

Nothing else may change, including:

- `src/` and `configs/`;
- A1 code and tests;
- pinned artifacts, sealed records and this preregistration;
- `population.py`, `plan.py`, `points.py`, `domain.py`, `extraction.py`,
  `render.py`, `queueing.py`, `session.py`, `server.py`, `gate.py`,
  `annotate.html`, `trigger.py`, `intro_latch.py`, `stage_identity.py`,
  `pins.py` and `pilot.py`;
- the unblinded modules (`unblinded_pins.py`, `mapping.py`,
  `comparison.py`, `statistics.py`, `verdicts.py`,
  `pipeline_unblinded.py`, `criterion_4a.py`, `report.py`).

The repair's machine-checked file set is exactly:

- these paths under `tools/diagnostics/map_ink_reference/`:
  `constants.py`, `cli.py`, `seal.py`, `pipeline_blind.py`, `records.py`,
  `__init__.py`, and the new `execution.py`, `pilot_pipeline.py` and
  `pilot_addendum.py`;
- files under `tests/map_ink_reference/`.

The full map-ink reference test suite, `ruff check` and
`ruff format --check` must pass before the repair is committed.

Amendment 6's "Nothing else may change" governs the Amendment 6 repair
only. It does not restrict this repair, which is governed by this section.

### Testing requirements (normative)

Synthetic fixtures only. Neither the five prepared pilot sources nor any
corpus recording may be used. Tests show that:

1. pilot commands take no recording option, and pilot operations reject
   any recording outside the sealed selection;
2. corpus commands reject pilot recordings and records carrying
   `"scope": "pilot"`;
3. pilot and corpus artifact paths cannot collide; pilot writes stay inside
   P and W; an existing pilot output STOPs;
4. pilot selection is unchanged: identical output on fixtures, the two
   smallest eligible `video_identity` values, and STOP with fewer than two;
5. pilot plans, extraction, points, duplicates, session records and gate
   equal the shared functions' output on the same synthetic input;
6. no pilot path reaches comparison or a classifier: the new pilot modules
   pass the blind isolation checks; `compare`, `criterion-4a` and `report`
   never read P; there is no pilot comparison command;
7. every parser subcommand STOPs when the chain, an A1 pin, clean tooling or
   the tool identity rule fails (enumerated over the parser, so a new
   command without the guard fails the test);
8. every guarded corpus command STOPs without a sealed valid addendum and
   proceeds with one;
9. missing, modified, extra or wrongly ordered pilot records; a split
   commit group; an invalid `changes` or `interface_commit`; an interface
   commit outside the allowed files; a second post-repair tool commit; and
   any pilot command after the addendum: each STOPs;
10. existing corpus behaviour is unchanged: existing corpus tests pass, and
    corpus records are byte-identical on fixtures;
11. after a sealed failing G5: `pilot-addendum`, every other pilot command
    and every corpus command STOP, and any interface commit fails the tool
    identity rule;
12. an interface commit STOPs if:
    - it touches a path outside the permitted set, modifies or deletes an
      existing test, or changes `constants.py` beyond the two values;
    - it changes `session.py` outside the allowlist: removes or reorders
      an existing import name or returned-dict entry, changes an existing
      entry, adds an entry of another form, or changes any other
      statement;
    - its changed files are not covered by the listed items;
    - `interface_fix` is listed without an added test;
    - `crop_zoom` disagrees with the values;
    - it precedes a passing G5, or a second one exists;
13. the tool identity rule recognizes the repair as the first commit after
    C7 that touches a protected path. It STOPs on a merge commit; on a
    repair outside its file set, without the C7 chain entry, or after G1;
    on a C7 that changes anything besides the preregistration; and on any
    protected-path commit after G6;
14. a mismatch of each of the seven production dependency pins,
    independently (including `types.py` and `config/models.py`), makes every
    command STOP; an uncommitted modification of a pinned file STOPs; the
    guard never rewrites a pinned file; the `map_ink.py` value equals the
    Amendment 3 pin; and the `config/models.py` value equals the Amendment 4
    pin.

### Sealing order additions (normative)

Before Amendment 3 step 3, in this order:

1. this amendment committed;
2. the tool repair implemented, tested with synthetic fixtures only, and
   reviewed;
3. the repair committed alone, with its commit and the Amendment 7 chain
   entry reported;
4. `preconditions` passes at the repaired commit;
5. STOP for explicit authorization.

Amendment 3 step 3 then consists of, in this order, each group committed
strictly after the previous one and containing only its records:

- G1: pilot selection;
- G2: frame plans, extraction provenance, points and duplicates (before
  any annotation);
- annotation (working output);
- G3: labels, team identity, queue and swatches;
- G4: token key and queue key;
- G5: gate, committed whether it passes or fails;
- on PASS only: the optional single post-pilot interface commit;
- on PASS only: G6, the pilot addendum; then STOP, and step 3 is
  complete.

On a failing G5: STOP after G5. No interface commit and no G6 follow
without a new authorization or amendment that explicitly resolves the
failure.

Step 4 and everything after it follow, unchanged, behind the addendum
verification. No calibrated classifier and no pilot comparison are
invoked in any branch.

### Superseded sentences (normative)

1. Amendment 6, Tool repair authorization: "The repair commit is recorded
   as the tool commit for every later real run." This now reads: until the
   Amendment 7 repair commit; from then on, the tool identity rule of
   Amendment 7 applies. `f636a6e` stays the step 2 tool commit of record.

### Disclosures

- Drafted after step 2a was sealed (`a1ea2fd`) and a read-only procedural
  review, before any step 3 operation. `pilot-select` has not run. No pilot
  eligibility count, selection, plan, extracted frame, point or label
  exists or is known.
- Generating the pilot and corpus blind-input manifests showed each
  recording's latched stage, battle mode and latch time on the console
  (tracker INFO logs). The drafter saw them in that form. They were not
  used.
- The operator played the pilot matches (Amendment 5).
- Revised before commit under the operator's rulings: pilot records are
  committed; a failing pilot gate is a mandatory STOP; there is at most one
  post-pilot interface commit with an exact file set; the history-based
  tool identity rule applies; the addendum is closed; and the console and
  command interface are as drafted.
- Revised again before commit: the production dependency pins were added;
  the meaning of interface bug fixes was stated; `session.py` was added to
  the interface-commit file set under a syntax-tree allowlist; and the
  `types.py` pin, the guard check of the Amendment 4 `config/models.py`
  pin and the dependency-closure boundary were added. The five new pins
  were computed from the committed source files at `a1ea2fd` (code only;
  no experimental input).
- During drafting only this preregistration, tool and test source, and the
  seven pinned production source files (to hash them and check their
  imports) were read. No media, vision manifest, analysis output or sealed record content
  was opened.
- No tool or test was changed for this amendment. Tracks B and C,
  Amendments 1–6 and all other Track A criteria are unchanged except for
  the sentence superseded above.

## Amendment 8 — Pilot duplicate rule (2026-10-25)

### What was frozen, what changes, why

**Frozen:** the preregistration through Amendment 7 as committed at C7,
`0a62f699894db5296b43102f1e77f7240a6f10de` (file SHA-256
`26ed135efbfb2127523601447f0edb1f4cef132daff4d1afc1bfb902fe6fb5a8`,
143,553 bytes). That file is a byte prefix of this one. C7 is the commit
that added Amendment 7. Each earlier amendment keeps its own sealing
identity, the preregistration file at the commit that added it:

| Amendment | Commit | File SHA-256 at that commit |
|---|---|---|
| 1 | `7b5c11121b3ecb4ac8df72016dcac712e4a0011f` | `0eea5c1f6559636f4f7b3abc94549d31befafb5853a5b4558dac33e628f2a906` |
| 2 | `6a9d705df5585a4e6e1c5c18eda38ebb2e736f18` | `2106b0120b49e3ada6b1924e5b934016fb99562d097f0d824a3534a363b78d3b` |
| 3 | `228e03774c2d5e9737f35698ea42cc11a0937a87` | `24abb418bf755de13085b47c2465bf0b864ea088011b669214a5cac4570e9c56` |
| 4 (chain entry 1) | `570f188c3a2322c20065b095d740a6ad2538c57a` | `4ffeeaf899185d84beea1e97c59b0baa46798cb7390cf294fae3343804e757e1` |
| 5 (chain entry 2) | `ca4e08821ca9c80f45125c168fe3caf3af536a3e` | `2a0a23ba0d4db8484eb7752861384546855dc65374d975580c7d11a77df6cb57` |
| 6 (chain entry 3) | `85d1104c1c969d3c9bdf7b179dda675d9c7748d5` | `e978e63f4da562f85eb4d7c42ff918df0f769b0e85ff012e2670307d71ee88df` |
| 7 (chain entry 4, C7) | `0a62f699894db5296b43102f1e77f7240a6f10de` | `26ed135efbfb2127523601447f0edb1f4cef132daff4d1afc1bfb902fe6fb5a8` |

Also frozen: the Amendment 6 tool repair
`a350578ebee9ba8728001157c0832b19323b08d2`; the Amendment 7 tool repair
`3883dd7bd14877705f61fe44b3b46a2c46a47afc` (parent C7), which this
amendment neither modifies nor replaces; and the sealed records named in
Amendment 7: candidate pool `29f9f5a8066e531f9bf3d58d3b0585ea5c2617ec`,
preparation record `acd64282dbfdf71a64b4faa9f180ed9eb2250d3b`, pilot-source
manifest `53a8f58bd8bef8775ae0557c234004a95238d2d2`, pilot blind-input
manifest `c16601f4e12fd93b7b313a06f8fba0a81c2e4d64` and corpus blind-input
manifest `a1ea2fddcd520e62ccf265d7eb8b3364cabecb33`.

**Finding:** the frozen pilot gate cannot pass. Amendment 3 step 3 runs the
Stage 1 procedure on two pilot recordings. Stage 1 selects 8 frames per
recording and at most P = 9 points per frame, so the pilot has at most
N = 2 × 8 × 9 = 144 points. The frozen duplicate rule `ceil(0.10 * N)`
gives at most 15 duplicate pairs. With 0 disagreements in a denominator of
15, the upper one-sided 95% Clopper–Pearson bound is 0.18104
(1 − 0.05^(1/15)), above the 0.10 cap. A smaller N gives no more pairs. The
pilot gate therefore fails for every possible set of labels, and the
Amendment 7 failing-G5 STOP would be reached by construction. This is a
frozen element that is unworkable by arithmetic alone.

It was found after the Amendment 7 repair was committed and before any
Amendment 3 step 3 operation. `pilot-select` has not run. No pilot
selection, plan, extracted frame, point, label or gate value exists or is
known.

**What changes:** the pilot duplicate count; this amendment's entry in the
authorized execution chain; the tool identity rule, to admit one tool
repair; one authorized tool repair; and the sentences superseded below.

**Unchanged:** Amendments 1–7 except the superseded sentences; the five
prepared pilot sources and every sealed record; pilot eligibility and
selection; frame population, intervals and selection; extraction; the point
domain, P, spacing and point seeds; the duplicate selection mechanism and
seed text; duplicate placement; the label vocabulary; swatches and team
identity; blinding and display; the gate population, the frozen pair
classification, the upper one-sided 95% Clopper–Pearson calculation and
the 0.10 cap, with a zero denominator failing; the mandatory STOP on a
failing G5; the pilot namespace, commit groups G1–G6 and the pilot
addendum; the allowed post-pilot change list; the production dependency
pins; and the corpus duplicate rule and all corpus Stage 1 and Stage 2,
comparison, criterion, threshold, verdict and non-exposure rules.

**Why:** to make the preregistered pilot gate passable with a margin for
disagreements and for excluded pairs, before any pilot information is
observed, without changing the gate itself or the corpus.

### Pilot duplicate rule (normative)

- For the Amendment 3 step 3 pilot only, the duplicate count is
  `ceil(2 * N / 3)` of the pilot's N Stage 1 points, computed in exact
  integer arithmetic as `-(-2 * N // 3)`. At the maximum N = 144 it is 96.
- Selection is otherwise the frozen rule: without replacement, with the
  seed from SHA-256(`"amendment3-dups|" + point-manifest SHA-256`) and
  `numpy.random.Generator(numpy.random.PCG64(seed))`, taking
  `choice(N, size=count, replace=False)`.
- Placement is the frozen rule: each duplicate is re-presented at least 50
  items after its first presentation, at the end of the queue if the queue
  is shorter.
- Excluded pairs are never topped up or replaced. The duplicate count is
  fixed by this rule before annotation and must not be increased.
- The corpus duplicate rule stays `ceil(0.10 * N_points)` for Stage 1 and
  Stage 2.

### Statistical rationale (informative)

Every value in this section was computed with the repository's frozen
`clopper_pearson_upper` (`tools/diagnostics/map_ink_reference/gate.py`) or
exact rational arithmetic, on synthetic inputs only. No pilot or corpus
information was used.

**Minimum denominator to pass, by number of disagreements x** (upper
one-sided 95% bound at most 0.10; for each x every larger denominator also
passes):

| x | Minimum n | Bound at n | Bound at n − 1 |
|---|---|---|---|
| 0 | 29 | 0.098145 | 0.101466 |
| 1 | 46 | 0.099024 | 0.101134 |
| 2 | 61 | 0.099635 | 0.101236 |
| 3 | 76 | 0.098870 | 0.100146 |
| 4 | 89 | 0.099899 | 0.101000 |

**At the maximum pilot population (N = 144, 96 duplicate pairs).** The
denominator is 96 minus the pairs excluded as both `cant_tell` (e).

- With e = 0, up to x = 4 disagreements can pass.
- The largest e at which x can still pass: 50 for x = 1, 35 for x = 2,
  20 for x = 3 and 7 for x = 4.

**One-third exclusion, as a stress scenario.** As a prospective conservative
stress scenario only, the design is evaluated with one third of duplicate
pairs excluded. One third was chosen as a deliberately substantial
denominator-loss scenario before any pilot information exists. The
preregistration separately permits up to 3 unjudgeable points out of 9 per
frame, but that individual-point tolerance does not imply a one-third
both-`cant_tell` duplicate-pair exclusion rate. No empirical exclusion rate
is assumed. Under this scenario, e = 32 and n = 64, so x = 2 can pass
(bound 0.095122) and x = 3 cannot (bound 0.116717).

**Lost-recording scenarios.** Two different situations:

1. *One recording in the population before selection.* If the point
   population held only one recording, N would be at most 72 and the rule
   would select at most `ceil(2 * 72 / 3)` = 48 pairs directly. With 48
   pairs, x = 1 can pass only if e ≤ 2, and x = 2 cannot pass. This cannot
   arise in the sealed procedure, because both chosen recordings are
   always in the pilot point manifest. It is stated only to separate it
   from the next case.
2. *Selection from the combined population, then one recording lost.* The
   duplicates are drawn from the combined pilot point manifest before any
   annotation. A recording without a valid swatch pair is fixed when
   swatches are recorded, before any point label exists, and its pairs
   leave D_1. Model, at the maximum population (exactly 72 points per
   recording, N = 144, 96 pairs): `choice(144, size=96, replace=False)` is
   a uniformly random 96-subset of the 144 points. Let S be the number of
   those 96 that belong to the surviving recording. Then S follows the
   hypergeometric distribution with population 144, 72 success states and
   96 draws:
   P(S = s) = C(72, s) × C(72, 96 − s) / C(144, 96), for 24 ≤ s ≤ 72
   (at least 96 − 72 = 24 must come from the survivor).
   - Expectation 96 × 72 / 144 = 48; variance
     96 × (1/2) × (1/2) × (144 − 96)/(144 − 1) = 1152/143 ≈ 8.0559
     (standard deviation ≈ 2.838).
   - Exact, from the pmf in rational arithmetic (authoritative):
     P(S ≥ 46) = 188068003494954700881733 / 231735263138871447416450
     ≈ 0.8115640276.
   - P(S ≥ 29) = 1 − P(S ≤ 28), with P(S ≤ 28) ≈ 1.787811 × 10^−13:
     extremely close to 1, not equal to 1.
   - P(S ≥ 61) ≈ 3.431458 × 10^−6. P(S ≥ 76) = 0, since S ≤ 72.
   - S counts surviving pairs before exclusions; the denominator is S − e.
     With fewer than 72 points in a recording, the hypergeometric
     parameters change accordingly. A Monte Carlo run of the frozen
     generator class over synthetic seeds agreed with the exact value; it
     is a non-normative cross-check only.

### Queue placement (normative) and tail concentration (informative)

This amendment deliberately keeps the frozen placement rule (at least 50
items later, or at the queue end) unchanged. A higher duplicate count makes
more first presentations fall within 50 items of the queue end. Their
repeats are appended at the end, so the queue ends in a block of repeats.

Informative supporting calculation, not a requirement of this amendment:
with the frozen `build_queue` on synthetic identifiers at the maximum
population (N = 144, 96 pairs, a queue of 240 items), the final run of
consecutive repeats averaged 43.55 items (median 44, range 21–66) over
3,000 synthetic draws. Under the corpus-rate count of 15 pairs it averaged
6.02. These figures illustrate the tradeoff. They are not values the
procedure must reproduce.

This is a disclosed memory and carryover tradeoff of the frozen placement
rule at a higher duplicate rate. It is not evidence for changing the
placement mechanism, and no placement change is made or authorized.

### Corpus (normative)

The corpus duplicate rule, records and behaviour are unchanged. Corpus
records produced by the repaired tooling must be byte-identical to those of
the Amendment 7 repair on the same input.

Disclosure: at the maximum corpus Stage 1 population (N = 360, 36 pairs),
the corpus gate can pass only with zero disagreements and at most 7
excluded pairs. This is consistent with Amendment 3's "strict practical
consequence" and is stated here only as arithmetic. This amendment does
not change it. Any change would need its own
amendment before corpus Stage 1 labelling.

### Pilot gate meaning (normative)

The pilot gate remains the frozen Stage 1 gate procedure over the pilot's
D_1, under this amendment's pilot duplicate rule. A pass or fail is a
workability result for the reference process on pilot material. It is not
a rehearsal or prediction of the corpus gate, whose duplicate count is
smaller. A failing G5 remains a mandatory STOP.

### Pilot sources and selection (normative)

Amendment 3's requirement of "a new amendment and a fresh pilot on
different non-corpus material" applies when the pilot shows a frozen
element is unworkable. Here the element was shown unworkable by arithmetic
on the frozen constants, before any pilot operation. No pilot information
exists, so the requirement is not triggered. The five prepared pilot
sources, the sealed step 2a records and the pilot selection rule stay in
use unchanged.

### Duplicate rate after the pilot (normative)

Amendment 3's post-pilot list stays in force. Once the pilot begins, neither
the pilot duplicate rule of this amendment nor the unchanged corpus duplicate
rule and its placement may subsequently be changed based on pilot results.

### Authorized execution chain (normative)

This amendment explicitly joins the Amendment 6 authorized execution chain
as entry 5: its sealing commit (C8 below) and the file SHA-256 at that
commit, recorded in the tooling by the repair below. Entries 1–4 are
unchanged. Every Amendment 6 rule applies unchanged.

### Tool identity rule (normative)

This replaces the "Allowed history" of the Amendment 7 tool identity rule.
Everything else in that rule stays: protected paths, the period of
application, a linear history with no merge commits from C7 (exclusive) to
HEAD, T as the protected-path commits in that range oldest first, and
records recording HEAD as `tool_commit`.

*Bootstrap:*

- C8 is this amendment's sealing commit. It changes only the
  preregistration file.
- Between C8 and repair 8, the committed tooling holds the chain through
  entry 4. The current preregistration differs from that entry, so every
  command STOPs. No run is possible in that interval.

*Allowed history:*

- T[0] is the Amendment 7 repair `3883dd7bd14877705f61fe44b3b46a2c46a47afc`,
  meeting every Amendment 7 requirement for it.
- T[1] is repair 8. It must:
  - be strictly after C8;
  - change only files of the tool repair authorization below;
  - have, in its own tree, a chain whose entries 1–4 are unchanged and
    whose entry 5 names C8 and C8's preregistration digest;
  - be committed strictly before G1.
- T is exactly the two repairs; or the two repairs followed by one
  interface commit I that meets every Amendment 7 condition (strictly after
  a sealed passing G5, operation 8, and once G6 is sealed equal to the
  addendum's `interface_commit`).
- With a null `interface_commit`, T stays exactly the two repairs.
- Any other history, including a fourth commit or a commit after G6 that
  touches a protected path: STOP.

The pilot addendum schema and its verification are unchanged.

### Tool repair authorization (normative)

After this amendment is committed, one tooling commit may make exactly
these changes:

- `constants.py`: chain entry 5 and the pilot duplicate-rule constants.
  Existing values unchanged.
- `points.py`: an optional, keyword-only count argument to
  `duplicate_point_ids`. Without it, the function's output is unchanged.
- `pilot_pipeline.py`: the pilot points and sealed-points steps use the
  pilot duplicate count.
- `execution.py`, `pilot_addendum.py` and `seal.py`: the tool identity rule
  above. Existing functions keep their behaviour otherwise.
- Under `tests/map_ink_reference/`: a new `test_mir_amendment8.py`,
  synthetic fixtures and helpers. Existing tests change only where the
  chain or the tool identity rule changes.

The repair's machine-checked file set is exactly these paths under
`tools/diagnostics/map_ink_reference/` (`constants.py`, `points.py`,
`pilot_pipeline.py`, `execution.py`, `pilot_addendum.py`, `seal.py`) and
files under `tests/map_ink_reference/`.

Nothing else may change, including: `src/` and `configs/`; A1 code and
tests; pinned artifacts, sealed records and this preregistration;
`gate.py`, `queueing.py`, `session.py`, `pipeline_blind.py`, `records.py`,
`cli.py`, `plan.py`, `domain.py`, `extraction.py`, `render.py`,
`server.py`, `annotate.html`, `population.py`, `trigger.py`,
`intro_latch.py`, `stage_identity.py`, `pins.py` and `pilot.py`; and the
unblinded modules.

The full map-ink reference test suite, `ruff check` and
`ruff format --check` must pass before the repair is committed.

### Testing requirements (normative)

Synthetic fixtures only. Neither the five prepared pilot sources nor any
corpus recording may be used. Tests show that:

1. the pilot duplicate count is `ceil(2 * N / 3)` in exact integer
   arithmetic over a range of N, including 144 → 96 and 72 → 48;
2. pilot duplicates equal the frozen selection (same seed text and
   generator) with that count, and a pilot duplicates record with any other
   count, including the corpus count, STOPs;
3. corpus behaviour is unchanged: `duplicate_point_ids` without the new
   argument gives unchanged output, existing corpus tests pass, and corpus
   records are byte-identical to the Amendment 7 repair on fixtures;
4. the tool identity rule accepts the two repairs, and the two repairs plus
   a valid interface commit after a passing G5. It STOPs on: repair 8
   outside its file set, without entry 5, with changed entries 1–4, before
   C8 or after G1; a fourth protected-path commit; a merge commit; and any
   protected-path commit after G6;
5. every command STOPs after C8 until repair 8 is committed;
6. every Amendment 7 testing requirement still holds, with its tool
   identity cases updated to this rule.

### Superseded sentences (normative)

1. Amendment 3, Point sampling: "The duplicate count is fixed by this rule
   and must not be increased." This now reads: for the corpus, unchanged;
   for the Amendment 3 step 3 pilot, the count is the pilot duplicate rule
   of Amendment 8, fixed by that rule and not to be increased.
2. Amendment 7, What was frozen, what changes, why, **Unchanged**:
   "point sampling, seeds and the duplicate rule". This now reads: point
   sampling, seeds and the duplicate rule, except the pilot duplicate count
   set by Amendment 8.
3. Amendment 7, Shared semantics, complete list of pilot–corpus
   differences: add item 12, "Duplicate count: `ceil(2 * N / 3)` (corpus:
   `ceil(0.10 * N_points)`), with the same selection, seed and placement
   (Amendment 8)."
4. Amendment 7, Tool identity rule, "Allowed history": replaced by the
   allowed history of this amendment.

### Sealing order additions (normative)

Before Amendment 3 step 3, in this order:

1. this amendment committed as C8, changing only the preregistration file;
2. repair 8 implemented, tested with synthetic fixtures only, and reviewed;
3. repair 8 committed alone, with its commit and the Amendment 8 chain
   entry reported;
4. `preconditions` passes at repair 8;
5. STOP for explicit authorization.

Amendment 3 step 3 then follows the Amendment 7 sequence G1–G6 unchanged,
with this amendment's pilot duplicate rule applied in G2.

### Alternatives considered (informative)

All alternatives use the same arithmetic as above, at N = 144.

- `ceil(N / 2)` (72 pairs): x = 1 can pass if e ≤ 26. Under the
  combined-population loss model, P(S ≥ 46) ≈ 7.27 × 10^−4. 216 queue
  items.
- `ceil(2 * N / 3)` (96 pairs, chosen): 240 queue items.
- Every point duplicated (144 pairs): up to x = 8 with e = 0. 288 queue
  items and the largest tail block of repeats.
- A count of 29: passes only with zero disagreements and zero exclusions.
  Rejected.
- Topping up excluded pairs: would change the gate population after
  annotation, contrary to Amendment 3. Rejected.
- Changing the cap, the confidence level or the test: not considered; they
  are frozen.

The frozen rule gives 159 queue items (144 + 15).

### Disclosures

- Drafted after the Amendment 7 repair `3883dd7` was committed and
  independently reviewed, and before any Amendment 3 step 3 operation.
  `pilot-select` has not run. No pilot eligibility count, selection, plan,
  extracted frame, point or label exists or is known.
- The infeasibility was first seen in a synthetic test fixture of the
  Amendment 7 repair (two synthetic recordings gave 15 pairs and a failing
  gate). It was confirmed by arithmetic and independently by the operator's
  review.
- The rate (`ceil(2 * N / 3)`) and the pilot-only scope were chosen by the
  operator from the alternatives above, on the arithmetic above alone,
  before any pilot information exists.
- During drafting only this preregistration, tool and test source, git
  metadata (commit identities and this file's digests at the commits
  listed) and computations on synthetic inputs were used. No media, vision
  manifest, analysis output or sealed record content was opened.
- The one-third exclusion anchor is a design rationale, not a data-based
  estimate.
- No tool or test was changed for this amendment. Tracks B and C,
  Amendments 1–7 and all other Track A criteria are unchanged except for
  the sentences superseded above.
