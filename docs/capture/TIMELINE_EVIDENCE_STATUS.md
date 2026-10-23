# Review timeline evidence: status inventory

Snapshot taken when `timeline-capture-v1` was introduced as a draft (it is
now frozen; `CHARACTERIZATION_LOG.md`). This is an
inventory, not a work plan: none of the items below is fixed by the capture
work, and each fix is to be selected and pre-registered separately.

## Works

- Timeline video and screenshot import (`s3-coach review timeline-import`,
  `timeline-video-import`): structure check, clock reader, match-history axis
  fit.
- SPECIAL_USE timing: frozen (tag `special-use-timing-frozen`, gate D PASS
  77/77 and 56/56). `s3-coach review special-facts` writes closed-vocabulary
  facts (`review/SPECIAL_USE_FUSION.md`).
- Roster lamps: 184/184 on the GO gate labels.

## Experimental

- Special identity on held-out data; five special types unconfirmed.
- `side` stays unknown without a declared ally row.
- Review-to-gameplay alignment: v1 and v2 both failed their gates
  (`alignment/ALIGNMENT_CONTRACT.md`).

## Incomplete or blocking

- Bottom-row death glyphs in blue/violet team colours (hue 120–136): 0 of 139
  expected deaths detected; the failure starts at grayscale conversion.
- Disconnected slots are read as alive.
- Per-second coverage is never complete (3–19 missing seconds across 30
  imports).
- The `non_monotonic` check appears inverted.
- Elapsed/remaining naming is mixed (`elapsed_*` fields hold a remaining
  clock).
- `review_timeline.json` carries no acquisition provenance. *Update:*
  `s3-coach capture review-import` now links it through
  `review_evidence.json` (`review/REVIEW_EVIDENCE_PACK.md`), which also
  records each limitation above per evidence block.
- Nothing in `analysis/` or `coach/` consumes timeline evidence.

## Read-only sanity run of the DRAFT gate (no tuning)

Four stored recordings, each copied into a temporary package; the sources
were only read (hashes unchanged). These recordings predate the procedure, so
they are not controlled captures.

| Recording | Automatic verdict | Non-pass automatic checks |
|---|---|---|
| Tower Control, OBS `.mov` | incomplete (manual checks only) | none |
| Overtime match, OBS `.mov` | fail | entry (starts inside the timeline), intro unknown, right endpoint unknown (overtime) |
| Turf War, OBS `.mov` | incomplete (manual checks only) | none |
| Same overtime match, VLC remux `.mp4` | incomplete | intro unknown, right endpoint unknown (overtime) |

What the observations showed:

- **Missing seconds are mostly label-specific, not sweep-specific.** 6–15
  regulation seconds stay missing in the union of both passes, and largely
  the same labels miss in every recording and both directions (`x:33`,
  `x:46`, `x:53`, `x:56`, `x:57`). At the observed sweep speeds of 16–27
  clock seconds per video second, a label is on screen for only 2–4 frames;
  those labels read with lower confidence (about 0.75 against 0.8 or more),
  and a transition frame drops below the 0.55 clock floor (`unclocked`).
  A second pass therefore recovers little (union 13 vs 15/17 per pass).
  *Update after C1–C3:* the 16–27 figures are pass-window averages that
  include endpoint dwell. Native traversal is fixed at about 30 clock s per
  video s, about two frames per clock second at 60 fps, so the recurring
  misses are a reader-characterization issue, not sweep coverage
  (`CHARACTERIZATION_LOG.md`).
- **No duplicated frames during sweeps** in OBS recordings (pixel-identical
  repeat fraction 0.0 while the cursor moves); one dropped frame in one file.
- **Overtime** was covered in both passes up to `+0:29` with no missing
  overtime seconds; the end of overtime cannot be identified from labels.
- **The intro detector latched** on both recordings that contain the replay
  intro (scores 0.93–0.996 mode, 0.70–0.88 stage); without an intro, best
  scores stayed at 0.36–0.50.
- **A remux passes every automatic technical check.** The VLC file declares
  `avg_frame_rate` 146040/2473 (59.05) with `r_frame_rate` 60/1 and a 0.72 s
  gap, yet sits inside the provisional window. Only its encoder tag (`vlc
  3.0.24 stream output`, brand `isom`) differs from OBS (`OBS Studio`,
  brand `qt`). It is caught today only by the manual
  `capture_path_unmodified` check.

## Recommended next step

1. Record about five controlled `timeline-capture-v1` captures with
   `docs/capture/OPERATOR_CHECKLIST.md` (include at least one overtime and
   one Turf War match), run `s3-coach capture check`, and compare with the
   existing importer on the same artifact.
2. From those, decide endpoint rules, the frame-rate window, sweep speed and
   whether continuity needs a slower sweep or a reader change, before
   freezing anything. (*Update after C1–C3:* sweep speed is fixed by the
   game and the slow-sweep characterization is retired; continuity is a
   reader question.) (*Update at freeze:* steps 1–2 done with C1–C4;
   `timeline-capture-v1` is frozen with timeline-before-GO as the procedure,
   and this sanity set was re-gated with the frozen code, unchanged.)
3. Then pre-register the colour-robust bottom-row death-glyph fix, the step
   the existing diagnostics name.
