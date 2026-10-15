# Experiment 3 candidate freeze and held-out pre-registration

Written and committed **before** any held-out frame was decoded. Nothing below
may change once held-out scanning starts; a change would turn the held-out set
into development data.

## Frozen candidate

Selected from development data (`../roi_reach/ROI_REACH.md`):

| Slot | Production search area | Candidate search area |
|------|------------------------|-----------------------|
| A1 | production ROI | production ROI extended **16** native px to the **right** |
| O3 | production ROI | production ROI extended **8** native px to the **right** |
| all other slots | production ROI | unchanged |

Everything else is identical to production: the 11 templates and their
load-time masks, template scale (fitted to the production ROI height), masked
`TM_SQDIFF_NORMED`, NaN-skipping `min`, threshold `0.03`, the visibility gate
(`min_slot_edge_energy` 20 on production ROIs), fusion and coaching. No
Lead!-conditional behaviour. Production code is not modified in this phase.

Exactly two methods are compared, on the same frames and the same human labels:

1. **production**: current geometry;
2. **candidate**: A1 +16 right, O3 +8 right.

If held-out results show either value is inadequate, the failure is recorded
and the work stops. The values are not re-tuned.

## Held-out pool

`pool.json` lists the pool (105 videos, 50 of them dated after 2026-09-10) and
every excluded video with its
reason. Selection rule, applied to the 252 videos in `~/Movies`:

1. Live gameplay only: names containing `review`, `preview` or `timeline`
   are replay or Review footage and are excluded (replay stays separate and
   is not evaluated here).
2. Not used by any roster experiment: the video stem matches no analysis
   directory name and no manifest `video_path` anywhere under `analysis/`
   (this covers the roster baseline, the threshold runs and every corpus the
   roster ROI/template/threshold work read).
3. Conservatively, not referenced by any other repository artifact either
   (map-ink validation, special-gauge survey, public/platform video lists,
   configs, tests, fixtures, scripts): 28 more excluded, including the default
   development video `2026-09-10 15-58-36`.
4. 1920x1080 and at least 60 s long (the 10 short clips are excluded).

Known limitation: the roster templates (commits `f2b52b7`, `2e4a29d`,
2026-09-09/10) have no recorded source video. A pool video could in principle
be the unrecorded template source. The report therefore also gives results for
the subset recorded after 2026-09-10 (date in the file name strictly later),
which cannot be the template source.

## Frame protocol

- Decode each pool video with `CAP_PROP_POS_MSEC` seeks at `t = 0.5, 1.5,
  2.5, ... s` (1 Hz).
- A frame is eligible when the production visibility gate passes (every
  production slot ROI edge energy >= 20). Ineligible frames are never labeled.
- For each eligible frame both methods are computed on A1 and O3 (the only
  slots where they can differ).
- Sampling (seed `20261015`, Python `random.Random`):
  - **random stratum**: 100 eligible frames drawn uniformly over the whole
    eligible set, at most 2 per video (videos visited in random order,
    frames drawn without replacement until 100 or exhausted);
  - **discordant stratum**: eligible frames where production and candidate
    disagree on A1 or O3. Consecutive discordant scan times in one video
    (gap <= 1 s, same slot set) form a run; one frame is drawn uniformly per
    run; if there are more than 100 runs, 100 runs are drawn uniformly.
- A frame in both strata is labeled once and carries both tags.
- The union is shuffled and given blind IDs `H001...`. The key (video, time,
  strata, both methods' labels and scores) is sealed in `key.json`.

## Labeling

Same rules as the baseline (`../baseline/labels/LABELING.md`): `hud_visible`
in {visible, hidden, ?}; each A1..A4 and O1..O4 in {x, alive, ?}; `?` whenever
a slot cannot be determined; dark-silhouette icons are labeled `x`; A = ally
row, O = opponent row, slots left to right. Labeling images show the top HUD
and both roster rows enlarged; they show no ROI boxes, scores or method
outputs. `key.json` must not be opened before labels are sealed.

## Pre-registered analysis

Over every human-determinable slot (all 8 slots) of frames with
`hud_visible=visible`:

- **primary safety criterion**: paired slot transitions production -> candidate
  (correct->correct, wrong->correct, correct->wrong, wrong->wrong), overall
  and per stratum;
- X precision and recall for both methods;
- per-slot A1 and O3 confusion counts for both methods;
- exact side-count accuracy (side comparable when no `?` on that side) and
  exact raw AvB (all 8 determinable), with paired AvB transitions;
- every candidate-created false positive individually, with an overlay showing
  where the winning template landed;
- for every held-out human X on A1 or O3: the X centre (best placement over the
  ROI widened 24 px each side) and whether it lies inside production reach and
  candidate reach.

Random-stratum numbers estimate the effect on ordinary frames; the
discordant stratum is a census-style check of every place the methods
disagree (capped at 100 runs). Both are reported separately; the decision
to modify production is the user's.
