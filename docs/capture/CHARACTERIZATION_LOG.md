# `timeline-capture-v1` characterization log (FROZEN)

Controlled captures recorded with `docs/capture/OPERATOR_CHECKLIST.md`.
C1–C4 and the read-only sanity/negative-control set
(`TIMELINE_EVIDENCE_STATUS.md`) are the evidence `timeline-capture-v1` was
frozen on (see **Freeze record**). Nothing in this log changed thresholds,
clock recognition, plausibility filtering, sweep behaviour or gate
semantics. Packages live outside the repository; review codes are kept in
each package, not here.

## Captures

| Capture | Mode (regulation) | Purpose | Automatic verdict | Verdict with confirmations |
|---|---|---|---|---|
| C1 | Splat Zones (300 s) | first controlled capture | incomplete (manual checks only) | pass |
| C2 | Turf War (180 s) | Turf War regulation length | incomplete (manual checks only) | pass |
| C3 | Clam Blitz (300 s), overtime | first overtime capture (attempted slower sweep; see below) | incomplete (`right_endpoint_observed` unknown: overtime) | pass (right endpoint confirmed by operator) |
| C4 | Clam Blitz (300 s), ranked, ends in regulation | timeline opened before GO | incomplete (manual checks only) | pass |

All four: 1920x1080, `avg_frame_rate` and `r_frame_rate` 60/1, HEVC, intro
identity latched before the timeline opened, no pixel-identical repeated
frames during either sweep, at most one dropped frame. C1–C3 opened the
timeline after GO; C4 before GO.

Timeline sweep. The gate field `clock_seconds_per_video_second` is a
**pass-window average including endpoint dwell**: regulation seconds
covered divided by the whole pass window, which includes the operator's
pauses at the edges (and, in C3, the overtime section). It is **not** native
cursor velocity. The field and the gate are unchanged.

| Capture | Pass-window average forward / return (clock s per video s) | Interior velocity forward / return | Missing forward / return / combined | Longest gap |
|---|---|---|---|---|
| C1 | 25.7 / 23.3 | 30.0 / 30.0 | 12 / 10 / 8 of 301 (4.0% / 3.3% / 2.7%) | 2 s |
| C2 | 19.5 / 19.1 | 30.0 / 29.9 | 8 / 7 / 5 of 181 (4.4% / 3.9% / 2.8%) | 2 s (combined 1 s) |
| C3 | 19.6 / 17.7 | 30.0 / 30.0 | 16 / 14 / 13 of 301 (5.3% / 4.7% / 4.3%) | 2 s |
| C4 | 24.0 / 22.7 | 30.0 / 30.0 | 12 / 14 / 10 of 301 (4.0% / 4.7% / 3.3%) | 2 s |

Interior velocity is measured read-only from each pass's clocked samples,
between 10 clock seconds inside one edge and 10 inside the other, so
endpoint dwell is excluded.

### Native timeline traversal (characterization result)

- Native continuous timeline traversal is approximately **30.0 game-clock
  seconds per video second**.
- Observed in all six C1–C3 passes, in both directions, across modes and
  timeline lengths (180 s and 300 s, with and without overtime), and
  uniformly across five equal interior segments of every pass (no
  acceleration from rest).
- C3's attempted slower operator input did not reduce traversal velocity.
- At 60 fps, native traversal provides approximately **two video frames per
  integer game-clock second**.
- Independent operator observations: a light stick tilt does not slow
  traversal, and the D-pad does not traverse the timeline.
- The 17.7–25.7 spread of the pass-window average reflects endpoint dwell,
  overtime and timeline length, not cursor speed.
- The planned slow-sweep characterization (C4) is **retired**: it is not
  achievable through ordinary continuous timeline input. It is not replaced
  with pulsed or release-and-re-engage movement. A dedicated control
  recording is not needed unless later evidence gives a reason to revisit
  this.

Recorded observations:

- Missing labels recur across captures and directions, mostly ending in
  `:33`, `:53`, `:54`, `:56` and `:57`. Combined-missing in all three:
  0:33 and 2:56; missing in some pass of all three: 0:33, 1:54, 1:56, 2:56.
  Because every label gets about two frames at the fixed native velocity,
  this is a **reader-characterization issue**, not evidence of insufficient
  sweep coverage. The reader is unchanged.
- C2: four implausible readings (`92:59` as the timeline opens, `-40:03` as
  it closes) are transition-frame artifacts excluded by the existing
  plausibility rule. No action taken.
- C3 was intended as a slower sweep, but its interior velocity was the
  native 30.0, and the match went to overtime. It is valid characterization
  evidence but **not** a slow-sweep characterization; no sweep-speed
  preference is inferred from it.
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
   timeline already open at regulation start. Not used in C3. Characterized
   in C4 and adopted as the v1 procedure (below).
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

## C4: timeline opened before GO

A first C4 recording was rejected as C4 evidence: it showed a different
match (the C3 match, recorded before the C4 procedure existed) with the
timeline opened about 1 s after GO and no return pass (automatic verdict
fail). Its package is kept unchanged as an invalid attempt. The re-recorded
C4 is the evidence below.

- The replay intro stays available and is detected normally (mode 0.997,
  stage 0.824) before the timeline opens; `timeline_entry_observed` has
  about 19 s of intro and pre-GO gameplay before the timeline.
- The timeline fades in over "Ready?" in about 0.2 s with the cursor at
  5:00, so 5:00 is observable from the first timeline frame and again on
  the return pass. After-GO entries (C1–C3) start at 4:59 / 2:59 and see the
  regulation start only on the return.
- No automatic transition at GO: the cursor stays where the timeline opened
  until the stick moves (2.1 s in C4; 1.2–4.5 s in C1–C3). No stabilization
  delay is needed.
- First stick push, at the start and at the turn in every capture: one
  label, a hold of about 0.48 s, then continuous traversal. This is the
  control's behaviour, not an effect of the before-GO entry.
- Endpoints: right edge 0:00, no overtime; the return passes 5:00, -0:01,
  -0:02 and holds at the pre-GO label -0:03, the true left edge (also seen
  at the end of C1–C3 returns).
- 2681 frames, no intervals over 1.5x nominal; 1491 clocked, 53 unclocked,
  1137 rejected (before the timeline); no implausible readings. Combined
  missing labels 0:33, 0:54, 0:56, 1:54, 1:56, 2:44, 2:56, 4:53, 4:56,
  4:57: the recurring reader set plus 2:44.
- Paired top-down (experimental, not gate input): entered before GO;
  "Ready", 5:00 and 4:59 are present, so the opening seconds are covered.
- Before-GO was characterized on a ranked 300 s match. Turf War (180 s) was
  characterized with after-GO entry (C2); the gate rules are the same.

## Freeze record

`timeline-capture-v1` was frozen after a review of C1–C4 and the
sanity/negative-control set. Evidence status:

- Every C1–C4 result was produced by evaluation code identical to the
  frozen code: C1–C3 at `8513390`, C4 at `ad06be2`. Later commits touch
  documentation and one unused Japanese Ready template; the freeze commit
  changes only `contract_status`, docstrings and a test pinning the frozen
  values and reader settings.
- The four sanity/negative-control recordings (originally run before the
  capture code was committed) were re-gated on fresh copies with the frozen
  evaluation code: identical check statuses, verdicts and union missing
  counts (13, 15, 6, 15). The negative controls still behave as recorded:
  a recording that starts inside the timeline fails `timeline_entry_observed`;
  a missing intro is `unknown`; an overtime right endpoint is `unknown`;
  a VLC remux passes every automatic technical check and is caught only by
  the manual `capture_path_unmodified` check.
- Four controlled captures were accepted instead of the "about five" first
  planned: the slow-sweep capture was retired as not achievable, and the
  knockout case stays untested and non-blocking.
- The before-GO procedure passed the unchanged checks without any rule
  change and is the adopted v1 procedure.

Sealed v1 evidence (packages outside the repository; artifacts read-only;
`result.json` never rewritten). Identity is `media.video_identity` of the
timeline artifact; result hash is sha256 of `result.json` at the freeze.

| Capture | Package id | Gate git SHA | Timeline identity | `result.json` sha256 |
|---|---|---|---|---|
| C1 | `9254bdfc1f8b` | `8513390` | `df355dd0edc695155f814cc6d4d4a0b3b038f4e2fecf73c9a5c7242aa8d2a465` | `66d219d10ba0f51e739fc30f27a7eaefe551e52493f74c37096ed617e1b2b9f6` |
| C2 | `c611cd48eed8` | `8513390` | `3ba9831aa030fbed9b2bbcbbe1bc4bd37ca51d5f126223ffd0ac1979fbdcb55f` | `62cc886b72831dafbc7daf4b44d756af405d86977b66bced4f6eeec35a19d53d` |
| C3 | `bee9a0e6630d` | `8513390` | `e35af3395d7c1e18a4518ba00229103977615ad7ed3c8548e67e3cbe47156979` | `21474828e937ee04ae492d65094624a863cccde0e9935893ef9d25c986b4d153` |
| C4 | `b4cb48c9f557` | `ad06be2` | `7e601ba6f76f5879df8bedd8ba02969984cdc79725ee5ce803c8e4078d6199a2` | `9f1606098209f45ae253cd92186c80619f805e51407c93f163bbf9b0aa86fe8e` |

Rejected first C4 attempt (not evidence): package `4001b22f551a`, timeline
identity `772e365b8c5e…`.

## Research observation R1: Switch 2 (outside v1 evidence)

R1 is a research-only cross-console observation. It is not part of the
C1–C4 series, is not evidence for `timeline-capture-v1`, and changes no
threshold, gate semantics, acquisition or console requirement, or operator
procedure. It is a different match from C4 (same stage, different mode),
recorded under the C4 conditions except for the console.

- Switch 2 matched the established capture behaviour: every automatic gate
  check (for information only), 60 fps acquisition with no frame gaps,
  native traversal of 30.0 clock seconds per video second, about two frames
  per clock label, timeline geometry (cursor scale and endpoint positions
  within a pixel of C4), first-stick behaviour and endpoint structure.
- The existing clock reader produced substantially fewer unreadable
  regulation labels in R1 (2 of 301 combined) than in C4 (10 of 301).
- Captured frames were present for the missing labels in both; the cursor
  did not skip them.
- No wrong clock values were observed; failures were unreadable frames.
- Clock glyphs render with softer, smoothed edges on Switch 2 than the
  hard-stepped edges on Switch. That is a plausible reader-level
  explanation for the difference, but causality is not established by this
  single cross-console observation.
