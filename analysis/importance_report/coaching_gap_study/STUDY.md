# Coaching gap study (frozen before the draw)

Observational. No model calls and no production change. Frozen at
`5ab9ace` before the draw and before any fact is
recorded. Machine-readable: `study.json`.

## Population and draw

Source: Experiment H design.json; arm A (production, roster off) outputs. Seed **20261019**.

- Production-selected (6, all included): 2026-10-11 18-18-00 @197.5; 2026-10-11 18-25-16 @175.5; 2026-10-11 20-33-31 @199.5; 2026-10-11 22-20-42 @152.0; 2026-10-11 22-31-53 @208.5; 2026-10-12 20-17-32 @65.5
- Unselected pool (16, draw 4, at most one per match): 2026-10-11 18-25-16 @85.0; 2026-10-11 20-33-31 @151.0; 2026-10-11 20-33-31 @180.5; 2026-10-11 20-33-31 @37.5; 2026-10-11 22-37-47 @210.5; 2026-10-11 22-37-47 @301.5; 2026-10-11 22-37-47 @58.0; 2026-10-11 22-57-30 @106.5; 2026-10-11 22-57-30 @176.5; 2026-10-11 22-57-30 @244.0; 2026-10-11 22-57-30 @278.5; 2026-10-11 22-57-30 @308.0; 2026-10-11 22-57-30 @49.5; 2026-10-12 20-17-32 @179.5; 2026-10-12 20-17-32 @221.0; 2026-10-12 20-17-32 @257.5

All production-selected deaths are included. From the unselected pool, random.Random(seed) shuffles the sorted list of matches that have an unselected death; the first N matches are kept and, in that order, rng.choice picks one death from each match's sorted unselected deaths.

## Classification (one row per missing fact)

- **A.** Already available: a concrete field holds the fact for this death. a_location=view if it is in the CoachLlmView the model receives (e.g. death, clock, low_ink, map, related, importance); a_location=pipeline if it is in ScenarioContext or the vision manifest but not in the view (e.g. special, score, zone_control, map.ink, players).
- **B.** Derivable: computable from readings or fused state that exist today, with no new detector (e.g. special ready when the fight began, from special gauge readings and the death time).
- **C.** New detector required: visible on screen but not extracted (e.g. the weapon that splatted you, the splat feed).
- **D.** Not realistically observable: needs information the footage does not carry or substantially harder inference (e.g. off-screen teammate positions, opponent intent).

Exactly one primary category per fact. When more than one applies, the category needing the least engineering work wins: A before B before C before D, and within A, view before pipeline.

Evidence pointer: A: field path and its value for this death. B: the existing readings or state it is computed from. C: the screen element. D: the reason.

Theme (one per fact): special, objective, ink, map, opponent, teammate, positioning, timing, other.

## Decision rule

Counts are per missing fact, not per death. Actionable facts are A, B and C; D facts are recorded only. If A + B >= C, the next experiment targets the theme with the most A + B facts (ties: more A facts, then report both); within that theme the most common of A-view, A-pipeline and B decides its kind (ties: least work first): A-view -> prompt / claim-catalog experiment, A-pipeline -> view-extension experiment, B -> fusion feature. If C > A + B, scope a detector for the theme with the most C facts. Ranking review is flagged if unselected deaths average at least 1.0 more A + B missing facts per death than selected deaths (descriptive; no automatic change).

## Stop points

1. Freeze (this file), committed.
2. Draw and worksheet, committed; the rater fills `gap_sheet.csv`.
3. Classification (`gap_classification.csv`); rater review.
4. `GAP_STUDY.md` with the decision rule applied.
