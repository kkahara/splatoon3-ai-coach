# Game-language probe (Phase 1)

The recording's game UI language (`en` / `ja`) is detected once per analysis
by a bounded pre-scan, `vision/game_language.py::probe_game_language`, before
any language-dependent detector is constructed. It is **not** the
coaching/report language and is never supplied by a user.

Status: Gate 1 (intro) and Gate 2 (gameplay callout) are characterized on the
permitted local corpus below. Gate 2 is a fallback still being characterized;
neither gate is called validated by unit tests passing.

## Contract

- Output is `en`, `ja` or `unknown`. Nothing defaults to a language.
- The first accepted observation establishes the language and stops decoding.
- Absence of a cue is never evidence of a language.
- `unknown` writes `game_language.json` (diagnostics) and fails the analysis
  with `GameLanguageUndeterminedError`
  ("Game language could not be determined from the recording."). `s3-coach
  analyze` exits with code 4; the public worker maps it to the same message
  and telemetry `game_language_undetermined`. No vision manifest, match
  identity or `analysis_id` is produced.
- On success, `with_detected_language` sets `vision.language` on a deep copy of
  the config; the vision config hash and `analysis_id` are computed **after**
  that, from the detected language. The probe never depends on `analysis_id`.
- Detection is the only source of the game language. `VisionConfig.language`
  starts unset and is not configurable: the config loader rejects YAML
  `vision.language`, `analyze` has no `--language`, and detector construction
  and template lookup fail while it is unset. Phase 1 manifests and
  diagnostics carrying `supplied_language` / `supplied_agreement` still load;
  the fields are ignored.

## Gates

Both gates use the sealed `vision/match_intro.py` primitives externally
(`_to_gray` CLAHE on the ROI only, raw grayscale templates, `_fit_template`,
`TM_CCOEFF_NORMED`). A matcher scores the best template per language and
accepts only when `best >= threshold` **and** `best - best_other_language >=
margin`. Rejections: `below_threshold`, `ambiguous_margin`, `no_templates`.

Phase comes from `TimerDetector` + `MatchPhaseFuser` on the probe's own decode
(same `VideoLoader` bounds and `FrameSampler(1 / hud_cadence_fps)` as the main
scan, so frame indices match). A timer read above the longest battle clock
(`max(lifecycle.opening_clock_seconds)`, 5:00) is ignored: lobby, Recon and
Salmon Run digits misread as 11:11 or 59:xx otherwise closed Gate 1 early and
started the fallback bounds before the match (`ja-2026-09-20 14-21-22` failed
that way).

| Gate | Cue | Frames observed | Closes |
|------|-----|-----------------|--------|
| 1 `intro_mode` | Localized battle-mode label, 5 modes x 2 languages (`match_intro.template_dir/<lang>/battle_modes/`) | Before the first `in_match` frame and before `intro_deadline_seconds` | `accepted`, `in_match`, `deadline`, `video_ended` |
| 2 `gameplay_callout` | Bottom-left D-pad signal label (`game_language.template_dir/<lang>/come_on.png`) | Only after Gate 1 closed without acceptance; eligible = `in_match` with a usable timer read | `accepted`, observation bound, live-seconds bound, no-live-frames bound, `video_ended` |

The Gate 2 cue is the persistent D-pad "this way" label: JA カモン, EN
"This way!" (French "Par ici!" is a negative). At 1080p the EN label sits at
x30 y898 (136x42), JA at x64 y902 (70x33). Templates scale by
`frame_height / reference_height`. While the local player is dead the same
slot shows the death text ("Ouch..." / やられた), which never matches.

Gate 1 intentionally does **not** infer language from semantic mode
resolution (`MatchIntroDetector`); only the localized label match counts.

## Diagnostics

`game_language.json` (`GameLanguageDiagnostics`, schema 1) is written for
resolved and unresolved videos: the compact `GameLanguageResult`, probe stats
(frames decoded, seconds decoded, wall time) and every attempt (gate, time,
frame index, match phase, timer, reading). For an unresolved video it shows:

| Question | Field |
|----------|-------|
| Intro not observed / not readable | `intro.attempted`, `intro.status = not_attempted / unreadable` |
| Intro observations ambiguous | `intro.status = ambiguous`, `intro.ambiguous_margin` |
| Fallback entered | `fallback.entered`, `fallback.entered_at` |
| Eligible fallback observations and span | `fallback.observations`, `first_time`, `last_time`, `span_seconds` |
| Fallback ambiguous / unreadable | `fallback.status`, `below_threshold`, `ambiguous_margin` |
| Fallback bound reached | `termination = fallback_limit`, `fallback.bound_reached` (`observations`, `live_seconds`, `no_live_frames`) |
| Video ended before resolution | `termination = video_ended`, `video_ended_before_resolution` |

`VisionManifest.game_language` carries only the compact result: language,
source gate, termination, `established_at`, establishing `DetectorResult`
(detector `game_language`, frame/result ids minted after `analysis_id`),
`SourceFrameReference` to `game_language/evidence_<frame_index>.jpg`, gate
summaries and the policy used.

## Defaults and the measurements behind them

Corpus: 237 permitted local recordings (`tools/diagnostics/
game_language_validate.py corpus`; excludes F11 Arm R media, the seven-video
corpus, `2026-10-24` pilots and Review/top-down captures). 196 carry an en/ja
label; two filename labels were visually verified wrong and are overridden in
the tool (`ja_turf--2026-10-21 20-19-13` is EN, `en-2026-09-20 16-20-26` is
JA); one (`en-2026-07-05 17-34-46`) is excluded as conflicted. Measurement:
`characterize` scores every 0.5 s frame of the first 150 s against both
packs; `characterize-report` computes ceilings and a threshold x margin grid.

| Setting | Value | Measurement |
|---------|-------|-------------|
| `intro_threshold` | 0.80 | Wrong-language intro max 0.649 over 10,041 frames (a previous match's results screen scoring the EN Rainmaker template; margin 0.26, so the margin cannot reject it). 0.60 accepts it (161 ok / 1 wrong); 0.65-0.70 give 162 ok / 0 wrong but only 0.00-0.05 headroom; 0.80 gives 0.15 headroom at the cost of 1 recording falling through to Gate 2. French intro max 0.622. |
| `intro_language_margin` | 0.10 | Margin when correct >= 0.70: min 0.226, median 0.588 (n = 1,205). Guards near-ties only. |
| `threshold` (callout) | 0.65 | Wrong-language max 0.342 on 43,115 eligible frames (0.313 on non-eligible frames, 0.285 on dead frames, French 0.277). Some EN recordings (Oct 17-19) peak at only ~0.72-0.77, so 0.70 is fragile; 0.65: 192 ok / 0 wrong / 4 unresolved, seconds from first eligible frame p90 2.0 / max 87, observations p90 4 / max 20. 0.70: same counts, p90 9 s / max 132 s, max 246 observations. 0.75 leaves 21 unresolved. |
| `language_margin` (callout) | 0.20 | Margin when correct >= 0.70: min 0.418, median 0.598 (n = 12,942). |
| `max_fallback_observations` | 120 | 6x the observed maximum (20) at 0.65. |
| `max_fallback_live_seconds` | 180.0 | About 2x the observed maximum (87 s). That 87 s came from one spurious early timer read that flipped the phase to `in_match` before the real match (`ja-2026-09-10 13-44-28`); the clock starts at the first eligible frame. |
| `max_seconds_before_live` | 300.0 | Without it, a video with no battle clock is decoded to the end (a 2 h 15 min recording ran for 35+ min in the probe alone). First eligible frame over 234 recordings: median 19 s, p95 68 s, max 138.5 s; 300 s is about 2x the max. Salmon Run (`2026-07-07 22-28-43`) shows the "This way!" label but has no battle clock, so it never becomes eligible. |
| `roi` | `[0.0, 0.7963, 0.1198, 0.9074]` | 1080p x0-230, y860-980 covers both labels with slack. Overlaps the death Ouch ROI; see dead-frame ceiling above. |

Production probe on 236 recordings (normal and forced-fallback modes agree on
every recording; no wrong language in either): unresolved, all failing closed:

- `en-2026-07-04 16-00-03`: starts mid-match (no intro) and a webcam overlay
  covers the bottom-left cue slot (observation bound, best 0.197).
- `ja-2026-09-09 20-01-09`: Recon (stage walkthrough), no battle clock
  (`no_live_frames`).
- `ja_2026-10-31 14-11-43`: a 5.4 s clip (`video_ended`).
- `fr_2026-10-26 22-29-36`: French (observation bound, best 0.277).
- `2026-07-04 16-06-41`, `2026-07-07 22-28-43` (unlabelled): Salmon Run.

The Phase 1 baseline evidence (corpus, raw characterization, both probe modes,
hashes, and the unmeasured `ja-2026-09-20 17-45-40` recording) is committed in
`analysis/game_language_validation/phase1/` (tag
`game-language-phase1-baseline`).

Rerun after any template, ROI or threshold change:

```bash
python tools/diagnostics/game_language_validate.py corpus --out corpus.json
python tools/diagnostics/game_language_validate.py characterize --seconds 150 --workers 8 --out characterize.json
python tools/diagnostics/game_language_validate.py characterize-report --input characterize.json
python tools/diagnostics/game_language_validate.py probe --mode both --workers 8 --out probe.json
```

`probe --mode forced-fallback` runs the production probe with Gate 1 disabled
so Gate 2 is measured on every recording; reports keep Gate 1 and Gate 2
separate.

## Out of scope (Phase 1)

No Booyah cue, no additional languages, no change to coaching/report
language, team-colour calibration
(post-GO), Ready? gating, map-ink eligibility, lifecycle or canonical
mode/stage detection.
