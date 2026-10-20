# "GAME!" tape detector: held-out validation result

**Result: PASS.** The frozen rule from `VALIDATION.md` (mask sha256
`efa53802…`, `fill >= 0.73`, `contrast >= 0.35`, 2 consecutive frames at
2 fps) was applied unchanged to 187 held-out recordings (15.1 h) that played
no role in development.

| Criterion (pre-registered) | Required | Observed |
|---|---|---|
| False runs | 0 | **0** |
| Recall, overall | >= 0.95 | **185 / 185 = 1.00** |
| Recall, `en` (75 endings) | >= 0.95 | 75 / 75 |
| Recall, `ja` (46 endings) | >= 0.95 | 46 / 46 |
| Recall, `unknown` (64 endings) | >= 0.95 | 64 / 64 |
| On-time rate (run starts <= 1.0 s after true onset) | >= 0.95 | **184 / 185 = 0.995** |

Artifacts: `heldout/truth.csv` (committed before `report` ran),
`heldout/detections.json`, `heldout/RESULT.json`, `heldout/endings.json`.

## Coverage

What the held-out set actually contained (185 endings in 182 videos; three
recordings hold two matches each):

| Dimension | Breakdown |
|---|---|
| Ending type | knockout 75, time 70, overtime 40 |
| Match length | 5:00 (ranked) 163, 3:00 (Turf War) 22 |
| Tape color | orange 48, lime 35, blue 34, yellow 28, pink 26, teal 14 |
| UI language (file prefix) | en 75, ja 46, unknown 64 |

- **Overtime** is well represented in both languages (en 17, ja 8,
  unknown 15). These are the endings a clock-only rule cannot place.
- **Japanese UI** ("Finish!" tape text, "延長中!" overtime banner) is
  covered by the 46 `ja` endings. Several `unknown`-prefix recordings are
  also Japanese UI (e.g. `2026-09-18 19-36-27`, `2026-09-20 14-47-03`), so
  the prefix undercounts it.
- **Modes:** all four ranked modes appear in the onset strips (Rainmaker,
  Tower Control, Splat Zones and Clam Blitz announcements), plus Turf War.
  Mode was not tallied per ending.
- **Negatives:** the 5 videos without a run are 2 Salmon Run shifts (no
  tape exists), 1 solo practice clip and 2 lobby/menu recordings. None
  contains a missed ending.

How the descriptive columns were produced (`describe`, never a pass input):
ending type from the production `TimerDetector` over the 60 s before the
true onset (slack = how long after the clock would have hit 0:00 the tape
starts: < -1.5 s knockout, -1.5 to 2.0 s time, > 2.0 s overtime); match
length from the largest keyframe clock reading (exactly 3:00 for all 22
short matches, all of which end on time, as Turf War must); tape color from
the band's dominant hue. Four overtime classifications and the one "unknown"
were checked by eye against the HUD overtime banner. The "unknown" (no clock reading within 60 s,
`2026-09-20 14-47-03`) is an overtime longer than the window, recorded as
overtime in `truth.csv` after checking the banner.

## Timing

| Run start minus true onset | Endings |
|---|---|
| 0.0 s | 167 |
| +0.5 s (tape sliding in one frame before the run) | 17 |
| +3.5 s (late) | 1 |

No ending had tape visible 1.0 s before its run (checked on the sampler's
own frames for all 17 one-frame-early cases).

**The late run** (`ja-2026-09-20 20-43-42`, Turf War, true onset 216.5 s,
run 220.0 s): an orange tape over orange-pink canyon rock. `fill` was 0.81
to 0.99 throughout, but `contrast` sat at 0.32-0.34 for seven frames before
reaching 0.355 at 220.0 s. The run was 2 frames long, the shortest in the
set. This is a background-color failure of the contrast feature, the same
feature that rejects ink walls.

## Margins (not criteria, but relevant to promotion)

The held-out margins are much thinner than the development set suggested
(there, gameplay frames with high fill never exceeded contrast 0.216):

- **4 gameplay frames passed the per-frame rule** (fill 0.730-0.734,
  contrast 0.353-0.366): large uniform lime or orange ink walls, the same
  pattern as the development magenta wall. Each was isolated, so the
  2-frame confirmation rejected all of them. Without that rule this
  validation would have failed with 4 false runs.
- 749 of 106,370 non-tape frames have `fill >= 0.73`; the highest
  `contrast` among those that stayed negative is 0.347.
- Run length (frames): `en` median 10, min 6; `unknown` median 9, min 5;
  `ja` median 8, min 2, with 7 of 46 runs at 5 frames or fewer. Japanese
  "Finish!" tapes sit closer to the thresholds than English "GAME!" tapes.

So the rule passes as frozen, but its safety comes from the two-frame
confirmation, and both error directions (ink walls, same-colored
backgrounds) are within a few hundredths of the thresholds.

## Implications for `src/` integration (for the next approval, not done here)

- The detector meets every pre-registered criterion, and that is the
  condition stated for approving `src/` integration and historical
  re-analysis. `AGENTS.md` is unchanged until that approval.
- Keep the 2-frame confirmation as a hard requirement; a single-frame rule
  is not safe on this evidence.
- A run can start a few seconds after the tape appears (3.5 s here). The
  frames in between show the tape, not play, so a late boundary admits no
  extra gameplay evidence; it only makes the boundary less precise. Do not
  extrapolate the onset backward.
- Timeline: this tool labels frames by `fps=2` output slot, which is not
  the same frame as `ffmpeg -ss t` (at 216.5 s in the late video, `-ss`
  shows the map while the sampled frame shows tape). Integration should use
  the production cadence timestamps, not this tool's labels.
- If thresholds are ever revised (for example to recover the late orange
  case), these 187 videos become development data and a new held-out set is
  required, per `VALIDATION.md`.
