# Experiment J: absolute rating procedure notes

Recorded for the final Experiment J report. Not a design amendment: the
design, populations, gates, thresholds, the sealed absolute sheet and its key
are unchanged. The design asks the rater to judge each output against "the
footage"; each sheet row's `footage` column names the full recording, which
stayed available throughout.

## 1. Rater-aid clips lengthened during rating (recorded 2026-10-21)

**What had been seen when this was recorded:** no automatic summary, check,
ablation, key, label or population file had been opened. Rating was in
progress; no rating value was read by the tooling.

- Rater-aid clips (`rating_clips/o###.mp4`, local only, not committed) are
  one per sheet row, named by `output_id`, cut from the sealed blank sheet's
  recording and death time. Rows that share a death share one encoded clip.
- **First cut:** 15 s before the death to 5 s after.
- **Problem found by the rater during rating:** some outputs make claims
  about earlier play (for example the time since the player was back in play,
  or the previous death) that a 20 s clip cannot show, so `supported_by_footage`
  could not be judged from the clip alone.
- **Replacement cut:** from 3 s before the death's back-in-play time
  (previous respawn, or match start; from the sealed `census.json`) or 30 s
  before the death, whichever is earlier, to 5 s after the death. The window
  depends only on the death, never on the output, arm or population.
- **Rows already rated with the first cut:** the rater re-checks, before the
  sheet is sealed, any row whose claims the first clip could not show, judging
  each row against its own footage only.
