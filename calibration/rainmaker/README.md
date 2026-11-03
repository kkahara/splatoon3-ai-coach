# Rainmaker HUD development calibration

The [possession survey](../../analysis/development/rainmaker/possession_survey/README.md) contains timestamped
English/Japanese review crops, the operator-confirmed state contract, and
development limitations for the Rainmaker-specific message reader and state
fuser. Add `rainmaker_possession` to `vision.enabled_detectors` to enable it.
It uses `vision.rainmaker_possession.template_dir/<language>/possession/` and
does not read score, checkpoints, map state, or carrier icons.

## Checkpoint breakthrough (development implementation)

Add `rainmaker_checkpoint` to `vision.enabled_detectors` for the separate direct
message reader. `vision.rainmaker_checkpoint` configures `template_dir`,
`match_threshold` (0.93) and competing-team `match_margin` (0.04). Masks live
under `<template_dir>/<language>/checkpoint/`. The search region at reference
1920×1080 is x=200–1719, y=250–379, wide enough for the English message.

| Language | Message | Factual milestone |
| --- | --- | --- |
| English | We broke through the checkpoint! | ally breakthrough |
| English | They broke through the checkpoint! | opponent breakthrough |
| Japanese | カンモンを突破した！ | ally breakthrough |
| Japanese | カンモンを突破された！ | opponent breakthrough |

Each `RainmakerCheckpointObservation` groups direct positive frames for the
same displayed message with a maximum one-second detection gap. A separately
recognized possession message or the other team's breakthrough bounds a group.
The observation retains the first accepted video time, all supporting source
frame references (including decoder PTS/index when available), detector result
IDs, language, template filename, and match scores. Refusion rebuilds these
observations from stored readings without decoding video. First accepted time
is evidence time, not an exact gameplay breakthrough time.

The snapshot's `rainmaker_checkpoint_state` holds `ally` and `opponent` as
`cleared | unknown`, together with their establishing observations. A direct
breakthrough establishes cleared regardless of prior possession. The first
establishing observation is retained; additional frames from that same group
can extend its supporting evidence. Silence, map visibility, possession,
counts, and later reset messages do not undo a clear. Unobserved teams remain
unknown, including at match start; this slice does not infer uncleared.
The state is gated to in-match Rainmaker, reset outside that match, and cleared
from snapshots at/after MATCH_END. No checkpoint geometry is used.

### Template sources and development results

The lossless `analysis/development/rainmaker/checkpoint_development/*.png` crops use x=200–1719, y=250–379.
Their names contain requested canonical video seconds. Masks threshold grayscale
at 210, using these source crops and bounds relative to the crop:

| Template | Source recording | Video seconds | Mask bounds (x1, y1, x2, y2; end exclusive) |
| --- | --- | ---: | --- |
| `en/checkpoint/ally__en23_105.png` | `en-rainmaker-2026-09-23 21-13-04.mov` | 105 | 65, 25, 1450, 100 |
| `en/checkpoint/opponent__en20_74.png` | `en_rain-2026-09-20 23-25-42.mov` | 74 | 65, 25, 1450, 100 |
| `ja/checkpoint/ally__ja_ally_166.png` | `ja-rain-2026-09-19 12-38-47.mov` | 166 | 450, 20, 1070, 105 |
| `ja/checkpoint/opponent__ja_opponent_190.5.png` | same Japanese recording | 190.5 | 400, 20, 1120, 105 |

Half-second development samples detected opponent English at 74 s, ally
English at 105 s, ally Japanese at 165–166 s, and opponent Japanese at
190–191 s. Thus each of the four selected displays had at least one detection.
Readable neighboring English frames at 73, 73.5, 105.5, 106 and 106.5 s and the
Japanese opponent frame at 189.5 s were missed. These remain documented
development limitations; no animation/background heuristics were added.
The old Japanese crop labeled approximately 189 s contains no breakthrough
message; the observed display begins around 189.5 s instead.

The possession survey's 105 s English ally-breakthrough crop also matched;
its other 37 crops produced no checkpoint cue. Selected Japanese possession
and lead-change messages were negative. These are selected development
examples, including template self-matches, not independent accuracy estimates.
Rainmaker mode-specific development stops here, before common context or
coaching integration.

## Remaining counts (first implementation)

Add `rainmaker_score` to `vision.enabled_detectors` to enable the opt-in reader.
`vision.rainmaker_score` configures `count_roi`, `template_dir`, and
`match_threshold` (default 0.6). Relative template paths resolve through the
normal config loader. Counts reuse the shared deterministic digit templates.
The count band is y=219–259 at reference resolution 1920×1080; this excludes
the higher carrier countdown. Digit bounding boxes are stored normalized to
the full frame. No carrier/icon interpretation is performed.

Lossless development HUD crops under `tests/fixtures/rainmaker` contain the
top 280 pixels of the original 1920×1080 video frame:

| Fixture | Recording | Video seconds | Visually reviewed counts |
| --- | --- | ---: | --- |
| `ja_172.png` | `ja-rain-2026-09-19 12-38-47.mov` | 172 | red 58, cyan 55 |
| `en_120.png` | `en_rain-2026-09-20 23-25-42.mov` | 120 | purple 66; other count absent |
| `en_200.png` | `en-rainmaker-2026-09-23 21-13-04.mov` | 200 | cyan 48, red 7 |
| `en_190.png` | same September 23 recording | 190 | map open; no count |

These examples are development checks, not accuracy measurements. The initial
0.7 digit threshold missed one cyan 5 in `ja_172`; 0.6 reads both counts.
Single digits are covered only by the red 7 example. Animated, occluded,
unusual-scale and other digit/palette examples remain unvalidated. Missing
or rejected rows remain unavailable rather than defaulting to 100 or zero.

Raw readings retain pod geometry and color. Team identity uses the existing
match Ready-window color calibration, stored as a per-frame association with
calibration frame IDs and time. Without calibration the team counts remain
unknown. Left/right position and possession are not team evidence. Snapshot
fusion mode-gates fresh observations to in-match Rainmaker with the HUD visible;
there is no count hold or score-rate plausibility rule. The snapshot's source
frame and Rainmaker detector evidence IDs trace each count to its raw result.

The separate `rainmaker_objective_observations` adapter translates fresh
same-team decreases to positive count units in `ObjectiveProgressObservation`.
Gaps beyond the supplied limit and increasing counts have unknown progress.
This adapter is available directly; ScenarioContext and coaching consumer
allowlists remain unchanged.

## Message template provenance

These are operator-labeled development crops from
`ja-rain-2026-09-19 12-38-47.mov`. Each crop is the message region
`x=300–1599, y=250–379` from the first frame sampled at the stated video
time. They are development evidence, not independent validation data.

Japanese cue labels:

| Crop | Video time | Operator interpretation |
| --- | ---: | --- |
| `ally_took_dev.png` | 146 s | `ガチホコをうばった！` — ally took the Rainmaker |
| `ally_checkpoint_broke_dev.png` | 166 s | `カンモンを突破した！` — ally broke through the checkpoint |
| `opponent_took_dev.png` | 172 s | `ガチホコをうばわれた！` — opponent took the Rainmaker |
| `opponent_checkpoint_broke_dev.png` | 189 s | Original approximate reference; this crop has no cue. Use `analysis/development/rainmaker/checkpoint_development/ja_opponent_190.5.png` for the labeled `カンモンを突破された！` message. |
| `count_lead_changed_dev.png` | 227 s | `カウントリードされた！` — count lead changed; separate cue |
| `count_lead_changed_ally_dev.png` | 163 s | `カウントをリードした！` — ally took the count lead; separate cue |
| `rainmaker_defended_dev.png` | 77 s | `ガチホコを防いだ！` — defended; separate cue |
| `rainmaker_reset_dev.png` | 103 s | `ガチホコが中央にもどった！` — Rainmaker reset |
| `ally_lost_dev.png` | 320 s | `ガチホコを失った！` — ally lost the Rainmaker |

The cue masks under `ja/possession/` were thresholded and cropped from the
corresponding source message crops above. English masks under `en/possession/`
were prepared from the possession survey's source-frame crops. Template names
retain the language and development label. This is self/near-source development
evidence, not independent validation.

The state vocabulary is `unknown`, `neutral`, `ally_possession`, and
`opponent_possession`. Direct acquisition messages establish the matching
team's possession; either loss message and the reset message establish
`neutral`. Neutral is initialized at match start only when fusion observes the
opening-countdown to in-match transition for an identified Rainmaker match.
Late-started analysis remains unknown until a supported cue. Established state
is held across silent, unreadable, and map-open frames, with the establishing
cue's evidence and source provenance retained. Observation time is the first
accepted message detection, not an exact gameplay transition time.

No checkpoint location, carrier icon, score change, world position, or
checkpoint duration is inferred.
