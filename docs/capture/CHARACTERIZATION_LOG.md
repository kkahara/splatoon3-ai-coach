# `timeline-capture-v1` characterization log (DRAFT)

Controlled captures recorded with `docs/capture/OPERATOR_CHECKLIST.md`.
Everything here is characterization evidence. Nothing in this log changes
thresholds, clock recognition, plausibility filtering, sweep behaviour or
gate semantics, and the contract is not frozen. Packages live outside the
repository; review codes are kept in each package, not here.

## Captures

| Capture | Mode (regulation) | Purpose | Automatic verdict | Verdict with confirmations |
|---|---|---|---|---|
| C1 | Splat Zones (300 s) | first controlled capture | incomplete (manual checks only) | pass |
| C2 | Turf War (180 s) | Turf War regulation length | incomplete (manual checks only) | pass |
| C3 | Clam Blitz (300 s), overtime | first overtime capture (recorded as a slower sweep; see below) | incomplete (`right_endpoint_observed` unknown: overtime) | pass (right endpoint confirmed by operator) |

All three: 1920x1080, `avg_frame_rate` and `r_frame_rate` 60/1, HEVC, intro
identity latched before the timeline opened, no pixel-identical repeated
frames during either sweep, at most one dropped frame.

Timeline sweep (measured; pass spans include the endpoint pauses):

| Capture | Sweep rate forward / return (clock s per video s) | Missing forward / return / combined | Longest gap |
|---|---|---|---|
| C1 | 25.7 / 23.3 | 12 / 10 / 8 of 301 (4.0% / 3.3% / 2.7%) | 2 s |
| C2 | 19.5 / 19.1 | 8 / 7 / 5 of 181 (4.4% / 3.9% / 2.8%) | 2 s (combined 1 s) |
| C3 | 19.6 / 17.7 | 16 / 14 / 13 of 301 (5.3% / 4.7% / 4.3%) | 2 s |

Recorded observations:

- Missing labels recur across captures and directions, mostly ending in
  `:33`, `:53`, `:54`, `:56` and `:57`. Combined-missing in all three:
  0:33 and 2:56; missing in some pass of all three: 0:33, 1:54, 1:56, 2:56.
- C2: four implausible readings (`92:59` as the timeline opens, `-40:03` as
  it closes) are transition-frame artifacts excluded by the existing
  plausibility rule. No action taken.
- C3 was intended as a slower sweep, but its measured rate is close to C2's
  and only lower than C1's, and the match went to overtime. It is valid
  characterization evidence but **not** the slow-sweep characterization; no
  sweep-speed preference is inferred from it. The slow-sweep
  characterization remains outstanding.
- C3 is the **first overtime capture**. Overtime labels up to +0:12 were
  read on both passes with no missing overtime seconds. The gate behaved as
  designed: the automatic `right_endpoint_observed` stayed `unknown`
  ("end of overtime not identifiable"), the operator confirmed the end of
  overtime separately, and the stored automatic result was not rewritten.
  Overtime is not made to pass automatically. No further overtime capture is
  scheduled unless a distinct overtime requirement remains untested.

## Operator observations after C2

1. **Timeline before GO.** The Review timeline can apparently be opened
   before GO is announced, which may let a timeline capture start with the
   timeline already open at regulation start. Not used in C3 (C3 isolates
   sweep speed). Kept for a separate characterization.
2. **Top-down before GO.** Entering top-down mode after GO loses about the
   first 2 seconds of gameplay (5:00, 4:59, possibly 4:58). Future
   experimental top-down captures enter top-down before GO when practical.
   C1 and C2 are not invalidated or redone; their `top_down_1x` recordings
   may lack those first seconds. C3's top-down was recorded before this
   observation and is assumed to share the limitation unless checked.
3. **Review code on screen.** The replay/review code is displayed to the
   left of the Play button. A possible battle-identity / provenance signal,
   not part of the current gate. A future characterization should determine
   whether a fixed ROI reads it reliably and whether it is suitable for
   verification and/or canonical battle identity.
