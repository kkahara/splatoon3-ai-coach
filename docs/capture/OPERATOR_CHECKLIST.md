# Operator checklist: `timeline-capture-v1` (DRAFT) and top-down 1x

Contract: `src/splatoon3_ai_coach/capture/CAPTURE_CONTRACT.md`. The contract
is a draft; this checklist is the procedure for the controlled captures that
will decide its thresholds. Do not tune anything between captures.

## Once per session: capture path

Known-good path: Switch → Elgato Cam Link 4K → OBS Studio, recorded to disk.

1. OBS → Settings → Video: Base (canvas) **1920x1080**, Output (scaled)
   **1920x1080**, Common FPS **60**.
2. Cam Link source → Properties: resolution **1920x1080**, FPS **60** (not
   50, not "Match output FPS" unless output is 60). Write down what the
   dialog shows; if it is not 60, fix it before recording and say so in the
   capture note.
3. No filters on the source, no other sources or overlays in the scene, the
   source fills the canvas.
4. Recording format MOV or MP4 (H.264 or HEVC). Do not use OBS remux, VLC,
   QuickTime trimming or any editor on the file afterwards.

## Per match: timeline capture

1. Load the replay from its review code. Write the code down exactly.
2. **Start recording before the replay starts**, so the replay intro (mode
   and stage plates) and at least a second before the timeline are in the
   file.
3. Open the match timeline.
4. Move the cursor to the **left edge** (pre-GO / 5:00, or 3:00 in Turf War)
   and pause about 1 s.
5. Sweep **right** to the end in one smooth, steady motion. Keep the speed
   the same on every capture and note how you moved the cursor (stick or
   d-pad, held or tapped). In overtime, continue to the end of the overtime
   section.
6. Pause about 1 s at the right edge.
7. Sweep **back to the left edge** the same way and pause about 1 s.
8. Stop recording. Do not trim.

## Per match: package and gate

```bash
unset PYTHONPATH
PKG=<captures-root>/<yyyy-mm-dd>-<nn>

s3-coach capture new "$PKG" --source review_code --code XXXX-XXXX-XXXX-XXXX \
  --language en --note "cursor: <stick|d-pad>, <held|tapped>; cam link fps: <value>"
s3-coach capture add "$PKG" "<path to the OBS recording>" --kind timeline
s3-coach capture check "$PKG"
```

Then decide each manual check and any `unknown` the gate reports, always
with a note saying what you looked at:

```bash
s3-coach capture confirm "$PKG" capture_path_unmodified --pass \
  --note "OBS Cam Link recording, untrimmed, not remuxed"
s3-coach capture confirm "$PKG" acquisition_profile_1080p60 --pass \
  --note "OBS 1920x1080@60; Cam Link source 1920x1080@60"
s3-coach capture confirm "$PKG" replay_matches_declared_code --pass \
  --note "replay loaded from the code above"
# only if reported unknown:
s3-coach capture confirm "$PKG" intro_identity_observed --pass \
  --note "intro shows <mode> on <stage>"
s3-coach capture confirm "$PKG" right_endpoint_observed --pass \
  --note "<regulation 0:00 | overtime end +M:SS | knockout at M:SS> reached"
```

Use `--fail` instead of `--pass` when the answer is no. A confirmation cannot
change a check the gate already passed or failed.

Optionally, to compare with the existing importer (read-only, separate
output directory):

```bash
s3-coach review timeline-video-import "$PKG/artifacts/timeline.mov" \
  --out "$PKG/derived/review_timeline_import"
```

Record per capture: package path, verdict, sweep speed and missing seconds
per pass from `quality/timeline-capture-v1/result.json`, and anything odd.

## Top-down reference capture (experimental, collection only)

No contract, no computer vision. The point is a clean reference.

1. Same package as the timeline capture of the match (same review code).
2. Playback setting **1x** (normal speed). Enter top-down mode **before
   GO** when practical; entering after GO loses about the first 2 seconds
   of gameplay.
3. Start recording before GO with the countdown visible; record continuous
   playback through FINISH / 0:00 (or the end of overtime); stop on the
   results screen. No pausing, seeking or speed changes.
4. Add it with the playback setting exactly as declared:

```bash
s3-coach capture add "$PKG" "<path to the OBS recording>" --kind top_down \
  --playback-rate 1x --note "top-down view, normal playback, firmware/game version if known"
```

`effective_playback_rate` stays empty. It is measured later; an accelerated
setting is never assumed to equal its label (a survey measured about 1.6x
for the fast-forward setting). A later accelerated capture of the same match
is a second artifact (`--playback-rate 2x` → `top_down_2x`).
