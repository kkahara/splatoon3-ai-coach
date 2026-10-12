# Review to gameplay alignment v2: pre-registered held-out gate

Written and committed before any v2 held-out pair is recorded. Tuning
evidence and the v2 change: `TUNING.md`. v1 record: `../PREREGISTRATION.md`,
`../REPORT.md` (FAIL, not edited).

## Frozen system

- v2 method code: commit `70c2eeb11fff2ce9d12f8407235c9585eaef2aa3`
  (`src/splatoon3_ai_coach/alignment/`, output `schema_version` `"2.0"`).
- Contract: `src/splatoon3_ai_coach/alignment/ALIGNMENT_CONTRACT.md`
  (Structural checks: coverage and clock domain from consistent HUD edges).
- `configs/default.yaml` sha256
  `17cf9fa6218686a2f2ba425cc2b80c23309cd71acbd71afb884b828263d19621`
  (unchanged since v1).
- `alignment` block sha256 (canonical JSON, `sort_keys=True`)
  `463787c0d38f1698afda752bd83235b96c5de228182132c481de2e3343efad3f`
  (unchanged since v1).
- `review` block sha256
  `98c2bf537caee518ce609812eea509e53fea1e7f4239a72120631e4230325e24`.
- `delta = 0.0`. Association window 5.0 s; onset persistence 2 visible frames;
  onset bracket at most 3.0 s; exactly one candidate used, none unassociated,
  several ambiguous.
- Tool: `tools/diagnostics/review_gameplay_alignment.py --gate` (unchanged
  since v1).

## Held-out recordings

- **4 completely fresh pairs**, none of them seen by v1 or v2 tuning. Each is
  a live player-view recording from before GO through results plus that
  match's Review timeline sweep, named with the same timestamp. Record them
  only after this file is committed.
- Record the next matches you play, whatever the mode or colours; do not
  choose or discard matches by how they look. A pair is declared when you
  send its file names, before any processing.
- Exclusion only for a recording-protocol violation you declare before
  processing (for example a truncated file). Anything else stays in.
- The sha256 of both videos per pair is committed before processing.

## Criteria (verbatim from v1, unchanged; roster-X is the pass reference)

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

## Reported, not pass criteria

- Local DEATH residuals and counts.
- `unassociated` / `ambiguous` counts per reference; death glyphs per row.
- Raw `highest_hud_read` against `consistent_clock_top` per pair.
- Negative controls: each held-out Review import paired with the next
  held-out gameplay analysis in declared order (cyclic, 4 controls), stating
  whether the structural check rejected each. Structural consistency is not
  proof of match identity; the Review import's mode comes from its own
  gameplay analysis, so a mode mismatch is not independent evidence.

## Run once

```bash
.venv/bin/python tools/diagnostics/review_gameplay_alignment.py --gate \
  --pair <review_1> <gameplay_1> ... --pair <review_4> <gameplay_4> \
  --negative-control <review_1> <gameplay_2> ... \
  --negative-control <review_4> <gameplay_1> \
  --out analysis/review_validation/alignment/v2/heldout/gate
```

The run is mechanical and happens once. A FAIL or INCONCLUSIVE is recorded
in `v2/REPORT.md`, not rescued. On PASS the boundary is tagged
`review-gameplay-alignment-v2-validated`.
