# Game-language detection — Phase 1 validation evidence

Baseline: git tag `game-language-phase1-baseline`. Contract and the rationale for
every default: `src/splatoon3_ai_coach/vision/GAME_LANGUAGE.md`. Tool:
`tools/diagnostics/game_language_validate.py`.

These are real-recording results on the local corpus, kept separate from the
unit/integration tests. They do not make automatic detection validated for
recordings unlike this corpus.

## Corpus

`corpus.json` lists the permitted local recordings in `~/Movies` on the capture
machine (the videos themselves are not committed). Exclusions: F11 Arm R
recordings, the seven-recording corpus, `2026-10-24` pilots, and
Review / preview / timeline / top-down captures. Labels come from the filename
prefix (`en`, `ja`/`jp`, `fr`); two were visually verified wrong and are
overridden in the tool (`ja_turf--2026-10-21 20-19-13` is EN,
`en-2026-09-20 16-20-26` is JA); `en-2026-07-05 17-34-46` is excluded from
label scoring as conflicted.

## Files

| File | Produced by | Code state | Recordings |
|------|-------------|------------|-----------|
| `corpus.json` | `corpus` | baseline | 237 |
| `characterize_150s.json.gz` | `characterize --seconds 150` | baseline matchers; `phase`/`timer` columns are raw (before the 5:00 clock rule) | 237 |
| `characterize.log` | same run | | |
| `characterize_report.json` | `characterize-report` | policy intro 0.80/0.10, callout 0.65/0.20 | 237 |
| `probe_both.json`, `probe_both.log` | `probe --mode both` | baseline **without** the 5:00 clock rule (300 s bound included) | 236 |
| `rerun_stems.json` | selection below | | 35 |
| `probe_rerun.json`, `probe_rerun.log` | `probe --mode both --only <rerun_stems>` | baseline | 35 |
| `probe_final.json` | `merge --input probe_both.json --overlay probe_rerun.json` | baseline | 236 |
| `scripts/crop_at.py` | helper used to cut `tests/fixtures/game_language/` crops | | |

Characterization scores are threshold-independent raw template scores, so the
report can be regenerated for any policy.

Why a partial rerun is equivalent to a full one: the 5:00 clock rule only
changes a recording whose timer reads exceed 300 s inside its probe window.
Every recording resolved in `probe_both.json` was resolved within its first
150 s (latest 138.5 s), and `characterize_150s` records every timer read in
that window. `rerun_stems.json` is every recording with any read above 300 s
in its first 150 s, plus all six recordings left unresolved by
`probe_both.json`. All other recordings see identical inputs and produce
identical results.

SHA-256:

```text
693f8f7d3e673c606f4f7bd9711dc4558be7babcc5e66ef847375615049d81ca  corpus.json
0802ebbf92419f1e1baf1d6796a3965e126294f55e8c90a9eadcbec31713b844  characterize_150s.json.gz
c556970767817913459d90ad37344fde5283cc1fbc0d279f09508f3d8b079b4c  characterize.log
c4a91822433620d28179a4d57be2d7eed3388b35b9ac4eeb25f3aafd24e45a41  characterize_report.json
f1e3ee7946cad6d4830155d402efb76a03b5a08860340afb39b26a4147cdea95  probe_both.json
9084f638d6b49d71a35dac800c98237b15d303e66a33fe3cb848e41f7d8b1997  probe_both.log
596fbb4e225a8eb4605de57bfa50dabcf3f3b21054914d65f2441d5a67040189  rerun_stems.json
f5387ce56640ca346ddcafdea0e95adef11564c4b273e73f50c0e204810ac790  probe_rerun.json
ca7d1543c6bd6d7e720081f2269359b7099b777d032f2d907ed5c10fad401834  probe_rerun.log
0e6e71db59518372f1dbc2b6bc17afba6971aed160f3e4b3d1100a63b48f9706  probe_final.json
ae0d45d1052cd7a293c15b2bc83e88333461222c8c2951d8744747cca32954a9  scripts/crop_at.py
```

## Results (`probe_final.json`)

| Group | Normal mode | Forced-fallback mode | Wrong |
|-------|-------------|----------------------|-------|
| EN (105) | 87 intro, 17 callout, 1 unknown | 104 callout, 1 unknown | 0 |
| JA (90) | 74 intro, 14 callout, 2 unknown | 88 callout, 2 unknown | 0 |
| FR (1) | unknown | unknown | — |
| Unlabelled (40) | 31 EN, 7 JA, 2 unknown | 31 EN, 7 JA, 2 unknown | — |

Normal and forced-fallback modes agree on every recording. Unresolved (all fail
closed): `en-2026-07-04 16-00-03` (mid-match start, webcam over the cue slot),
`ja-2026-09-09 20-01-09` (Recon, no battle clock), `ja_2026-10-31 14-11-43`
(5.4 s clip), `fr_2026-10-26 22-29-36` (French), `2026-07-04 16-06-41` and
`2026-07-07 22-28-43` (Salmon Run).

## Missing recording: `ja-2026-09-20 17-45-40.mov`

- Present in `corpus.json` and `characterize_150s.json.gz` (2 h 15 min,
  8,122.65 s, 1080p60 HEVC; 301 sampled frames in the first 150 s, none
  eligible: no in-match frame with a usable timer read).
- A first production-probe run (before the 300 s no-live-frames bound existed)
  was still decoding this recording after about 35 minutes and was stopped; that
  run wrote no results.
- The file was removed from `~/Movies` outside the validation tooling before the
  rerun, so `probe_both.json` (corpus rebuilt at launch) and
  `probe_final.json` contain 236 recordings.
- No production-probe result exists for it and none is reconstructed. Its Phase
  1 outcome is **not measured**.

## Reproduce

```bash
T=tools/diagnostics/game_language_validate.py
D=analysis/game_language_validation/phase1
python $T corpus --out corpus.json
python $T characterize --seconds 150 --workers 8 --out characterize_150s.json
gunzip -k $D/characterize_150s.json.gz
python $T characterize-report --input $D/characterize_150s.json > characterize_report.json
python $T probe --mode both --workers 8 --out probe.json
python $T merge --input $D/probe_both.json --overlay $D/probe_rerun.json --out probe_final.json
```

A fresh `probe --mode both` at the baseline replaces the merge (about 20 minutes
with 8 workers on the capture machine).
