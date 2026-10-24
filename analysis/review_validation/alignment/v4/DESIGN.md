# Alignment v4 remediation design

Status: design only. No candidate in this document is implemented, tuned, or
validated. The Alignment v3 held-out result remains the final **FAIL**.

## Frozen boundaries

The v3 evidence is preserved by commit
`04a4a5b959b6f74d6839d928a2e4acf597d43250`. The sealed input manifest is
commit `82f4c7ead9c7d1ac3f7e09f59ad1f50d4ac26159`. The v3 implementation and
Amendment 1 identities are `5e781569e3aa37f917a8edbae4d45d4f37332c27` and
`92d5a411b47b164f331077210d30c658e47ab5f1`.

The ten v3 pairs are postmortem evidence only. They cannot be reused as final
evidence that a v4 candidate generalizes. This design does not modify
production code, thresholds, ROIs, configuration, the v3 reports, or the
frozen gate.

## Track A — semantic team-color calibration validity

### Causal hypothesis

Roster visibility is necessary but insufficient. Pair-010 admitted
presentation/transition frames after a low-confidence Ready false positive.
The accepted colors (`ally 100`, `opponent 25`, with opponent samples `25, 23,
164`) were transferred to Review rows (`top 85`, `bottom 168`) and
deterministically produced `top` instead of the operator-declared `bottom`.
Pair-003/004 show the opposite boundary: color pixels can exist while
fail-closed readiness is unavailable.

### Candidate A1: phase-fused validity gate

Accept calibration only after the existing `MatchPhaseFuser` reaches
`in_match` from a non-opening timer transition, while retaining positive
roster visibility, separation, and immutable-latch requirements. Ready? alone
must not establish semantic in-match validity.

- Evidence source: existing timer, match phase, player-count, and lifecycle
  evidence; no new detector.
- Failure modes: delayed or missing timer/phase evidence increases `unknown`;
  false timer transitions could admit a bad phase and therefore require
  development characterization.
- Development data: the seven recoverable historical recordings, pair-010-like
  presentation transitions, and delayed/missing-Ready cases including
  pair-003/004 as diagnostic cases, without optimizing to their expected
  labels.
- Tests: presentation-frame rejection, opening-countdown rejection, positive
  in-match latch, missing-Ready unknown, missing-stage unknown, immutable
  provenance, and map-ink suppression.
- Success: no development calibration accepted before validated in-match phase;
  no semantic side swap in labeled development cases; fail-closed unknown
  behavior and provenance remain intact.

### Candidate A2: semantic multi-frame color latch

Keep the phase boundary but require a bounded sequence of post-phase,
roster-visible frames with stable ally/opponent assignments. Add robust
cross-frame dispersion and explicit rejection of color-label jumps such as the
pair-010 opponent sequence.

- Evidence source: existing pixels, roster readings, and calibration
  provenance; no new detector.
- Failure modes: legitimate palette animation, partial HUD visibility, and
  color variation can cause delayed calibration or unknown.
- Development data and tests: the same historical and targeted transition
  corpus; unit tests for stable colors, outliers, label swaps, partial slots,
  and no-latch behavior.
- Success: presentation/transition sequences cannot latch; stable in-match
  sequences still latch at an acceptable rate; no fail-open fallback.

### Candidate A3: independent in-match anchor

Require an independent positive anchor, such as fused `active_gameplay` with
weapon/center evidence or a validated post-GO roster layout, before
calibration. Existing active-gameplay and match-phase evidence are preferred;
a new detector is needed only if they cannot identify the intended HUD state.

- Failure modes: delayed active evidence increases abstention; an anchor that
  is itself presentation-sensitive simply moves the failure.
- Development tests must prove the anchor is independent of Ready? and reject
  pair-010-like transition frames.

The A-track choice must preserve `ally_row = unknown` when semantic validity is
not established. Map ink remains disabled or absent without trustworthy
calibration.

## Track B — HUD clock population selection

### Causal hypothesis

Pair-009 produced adjacent half-second edge populations (`c` near
`313.0–313.5` and `313.5–314.0`). The frozen maximum-overlap method selected
the larger later population, truncating the consistent span at remaining 220
and causing a clock-domain mismatch. This is not a filename-derived issue or
an established recording discontinuity.

### Candidate B1: continuity-constrained population model

Partition edge constraints into candidate `c` populations, then score them
jointly on interval overlap, temporal continuity, monotonic remaining-time
coverage, and full regulation-span support. Do not select by count alone.
Return unknown when multiple populations remain comparably supported.

- Evidence source: existing raw timer edges and timestamps; no new detector.
- Failure modes: a real discontinuity could be incorrectly merged; explicit
  discontinuity penalties and ambiguous-fit abstention are required.
- Development data/tests: v1/v2 timer tuning evidence, pair-009-like adjacent
  populations, synthetic half-second shifts, true discontinuities, missing
  intervals, overtime, and knockout fixtures.
- Success: pair-009-like quantization is distinguished without a pair-specific
  tolerance; real discontinuities and ambiguous fits remain unknown.

### Candidate B2: robust latent clock fit

Fit one monotonic `video_t = c - remaining` population with preregistered
bounded edge jitter. Treat individual edges as outliers only under fixed
robust-loss and continuity rules, reporting every excluded edge and reason.

- Existing timer evidence is sufficient.
- Failure mode: outlier rejection could hide a real domain change; fail closed
  when competing fits are similarly supported.
- Tests cover outlier accounting, ties, half-second jitter, discontinuity, and
  sparse coverage.

### Candidate B3: endpoint plus continuous-span anchor

Require agreement between trustworthy opening/end anchors and the dominant
continuous edge sequence. Overtime and knockout lack a trustworthy common
endpoint and must remain unknown/inconclusive rather than gain an invented
anchor.

The B-track choice must be frozen with exact method/config hashes before any
new validation set is collected.

## Track C — structural negative-control discrimination

### Distinct concepts

**Provenance binding** and **empirical media-identity evidence are separate.**
Provenance can establish acquisition/package integrity: which files were
declared together, which review code/package was recorded, and whether bytes
match the sealed artifact hashes. Provenance alone is not proof that two media
artifacts depict the same match.

Empirical media-identity evidence is evidence extracted independently from the
media or their typed derived artifacts that the recordings depict the same
match. It must be evaluated separately, with its own uncertainty and
unknown/failure semantics.

### Causal hypothesis

The current structural check proves clock/mode compatibility, not match
identity. Same-mode/same-clock pairs such as 007×008 can remain structurally
consistent. Missing mode/stage metadata also prevented rejection of 003×004
and 004×005.

### Candidate C1: provenance binding as a separate integrity gate

Require declared pair membership, package/review-code binding, artifact
identity, and sealed provenance to be present and internally consistent.
Missing or conflicting provenance is `unknown`/unusable, not proof of a
mismatch and not proof of same-match identity.

- Evidence source: acquisition/package metadata only; no new detector.
- Failure mode: perfectly intact but incorrectly paired media still pass
  provenance. This candidate must never be described as media identity proof.
- Tests: altered bytes, mismatched package IDs, missing declarations, and
  intact unrelated same-mode pairs.

### Candidate C2: empirical cross-modal match fingerprint

Compare independent media-derived fingerprints such as stage/mode intro
identity, regulation/overtime signature, and stable timeline landmarks. Require
preregistered independent agreement; insufficient or conflicting fingerprints
remain unknown.

- Existing MatchIntro, Review identity, and timer evidence may suffice for a
  first study; a dedicated fingerprint/association layer may be needed.
- Failure mode: same stage/mode and similar clocks remain ambiguous; do not
  force rejection or acceptance.
- Development data: true same-match pairs, same-mode different-match pairs,
  missing identity, overtime, and knockout cases.

### Candidate C3: shared event-landmark fingerprint

Compare independent objective/event landmarks across gameplay and Review with
time uncertainty, such as objective transitions or sparse score/control
changes. This is a new secondary evidence source/bridge, not a player-side or
causal inference.

- Existing score/objective evidence must first be shown reliable in both axes;
  otherwise a new validated evidence source is required.
- Failure modes: sparse or coincidental landmarks; insufficient evidence is
  unknown.
- Tests cover no landmarks, one landmark, multiple agreement, conflict, and
  same-mode unrelated controls.

Track C must report provenance integrity and empirical media identity as
separate fields. No candidate changes the frozen v3 gate in retrospect. A
future decision must explicitly classify any identity result as provenance
status, empirical identity status, or unknown.

## Development and future validation contract

Before implementation, select at most one candidate per track, define exact
thresholds and unknown semantics, and preregister the method/config hashes.
Development may use historical tuning data and newly collected development
data, but not the v3 ten-pair aggregate as final generalization evidence.

Unit and regression tests must cover positive, negative, missing-evidence, and
ambiguous cases for each selected track. A new fresh consecutive held-out set
must have immutable operator declarations and sealed provenance before opening.
The selected v4 method must be run once on that untouched set with no
post-hoc threshold or pair-selection changes. Report false acceptance,
false rejection, and unknown rates separately for Track C.

No production implementation, candidate tuning, new threshold, validation
run, or Stage 3 work is authorized by this design record.
