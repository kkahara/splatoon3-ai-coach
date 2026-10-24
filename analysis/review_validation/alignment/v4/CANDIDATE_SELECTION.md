# Alignment v4: candidate selection record

Status: selection and preregistration only. No candidate is implemented,
tuned, or validated by this record.

## Lineage and frozen boundaries

| Artifact | Commit / identity |
|---|---|
| v3 held-out FAIL (immutable) | `04a4a5b959b6f74d6839d928a2e4acf597d43250` |
| v3 sealed input manifest | `82f4c7ead9c7d1ac3f7e09f59ad1f50d4ac26159`, SHA-256 `4ed58210fad0c98f3ca7b1125b67cee17561c3d4834c61cbc0483a69384bcdf3` |
| v3 frozen implementation | `5e781569e3aa37f917a8edbae4d45d4f37332c27` |
| v3 Amendment 1 | `92d5a411b47b164f331077210d30c658e47ab5f1` |
| v4 design record | `9122c0dd5f3a246380c6bddfa53b00a99a142efc` |
| v4 `DESIGN.md` SHA-256 | `a07b3189b4c87e4da000767ec3a120f3213b9af4206aef9f94a083bc7e2558c4` |

The ten opened v3 pairs are regression and postmortem evidence only. They may
inform mechanism-specific regression fixtures; they are not held-out evidence
and must never support a v4 generalization claim.

## Track A — selected starting candidate

**Selected:** Candidate A1, **phase-fused calibration validity gate** (see
[`DESIGN.md`](DESIGN.md)).

**Deferred for escalation only:** A2 semantic multi-frame color latch; A3
independent in-match anchor.

Rationale: v3 postmortem pair-010 shows roster-gated color accepted on
presentation/transition frames while fused match phase was not independently
required. A1 adds the smallest semantic constraint using existing
`MatchPhaseFuser` evidence before changing pixel latch rules or adding
detectors.

## Track B — comparison protocol (no selection yet)

**Compare before selecting:** Candidate B1 continuity-constrained population
model versus Candidate B2 robust latent clock fit.

**Deferred until after comparison:** B3 endpoint plus continuous-span anchor.

Rationale: pair-009 shows adjacent half-second populations where
maximum-overlap count selection truncates the consistent regulation span. B1
and B2 are the two principled replacements that do not introduce pair-specific
tolerances. Each candidate has absolute qualification criteria declared before
comparison. The outcome may be B1, B2, neither, or a preregistered
tie/simplicity result when both qualify without a material difference. The
outcome is chosen only by the preregistered comparison rule in
[`DEVELOPMENT_PREREGISTRATION.md`](DEVELOPMENT_PREREGISTRATION.md), not by
inspecting v3 held-out outcomes.

## Track C — reporting boundary (not a core gate change)

**In scope for v4 development reporting:** Candidate C1 provenance/package
integrity as a separate status field.

**Out of core alignment gate until a separate study passes:** C2 empirical
cross-modal match fingerprint; C3 shared event-landmark fingerprint.

**Invariant:** Provenance binding establishes acquisition and package
integrity only. It is not proof that gameplay and Review media depict the same
match. `provenance_integrity`, `empirical_media_identity`, and
`structural_compatibility` are separate evidence dimensions, each with
appropriate support/pass, contradiction/fail, and unknown/not-available
semantics. The frozen v3 structural alignment gate vocabulary is unchanged;
Track C does not retroactively reject or accept v3 negative-control examples.

## Next authorized phase after review

1. Implement only the preregistered development candidates.
2. Run the declared development experiment (no fresh held-out acquisition).
3. Freeze exact implementation and configuration hashes.
4. Separately preregister and acquire a **new** consecutive held-out set for
   v4 validation.

No production change, fresh held-out opening, or v4 validation run is
authorized until this preregistration is accepted.
