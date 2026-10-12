# Review team-row death glyphs vs team hue: diagnosis (read-only)

Status: **diagnosis only.** No detector, threshold, template, scale, dedupe,
offset, config or alignment change. `configs/default.yaml` is untouched (review
block hash `98c2bf53…`, alignment block hash `463787c0…`). A fix, if any, is a
separate assignment that must be pre-registered and tested on its own.

## Question

On the nine existing recordings, death glyphs on a team row whose hue is about
120–136 (blue / violet) are never produced. At which stage does good evidence
first become bad?

source pixels → lane crop → preprocessing (grayscale) → template scores →
candidate generation (discovery floor, NMS, window std) → echo suppression →
scoring / acceptance

## Method

- Tool: `tools/diagnostics/review_row_hue_trace.py` (read-only; reuses frozen
  `review.lane_glyphs` functions and the production static frames from
  `select_static_samples(…, static_frame_count=3)`).
- **Expected glyph positions come from Review roster lamps, not glyphs.** For
  every slot, each alive → dead lamp bracket `(x_a, x_b]` at most 20 px wide is
  one expected death at its midpoint. The lamps classify bottom-row slots
  normally in every recording (hundreds of `dead` lamp states per recording in
  the failing ones too). `death_episodes` is empty there only because episodes
  are derived from glyph markers.
- Locator check on rows that work: 91/102 bottom (other hues) and 182/204 top
  expected glyphs match a production death marker within 10 px. So the locator
  is sound.
- For each expected glyph and static frame, the tool replays the production
  stages in order and records the first one that drops it: `cursor`,
  `below_floor` (combined score < `special_discovery_floor` 0.45), `nms`,
  `low_std` (production `_window_std` on grayscale < `event_glyph_min_std`
  0.04), `echo`, then `scored_death` / `scored_unknown`. Per glyph, the
  furthest stage over the three frames is reported.
- It also measures raw evidence at the expected window:
  - death template score;
  - the peak window std;
  - glyph pixel hue, value and grayscale level (pixels with saturation above
    100), and grayscale contrast against the lane background.
- Counterfactual, diagnostic only: the death score under the existing
  `hsv_value` representation, to check whether the evidence is present in the
  source pixels. It is **not** a proposed setting.

Artifacts: `row_hue_trace.json` (every glyph, every frame), `row_hue_trace.md`
(tables), `bottom_row_crops.png` (bottom lanes, colour above grayscale).

Reproduce:

```bash
A=analysis/review_validation/alignment
PYTHONPATH=tools/diagnostics .venv/bin/python tools/diagnostics/review_row_hue_trace.py \
  $A/tuning/{20-33-31,23-56-41,18-18-00,18-25-16,23-43-31} \
  $A/heldout/{22-20-42,22-31-53,22-37-47,22-57-30} \
  --out analysis/review_validation/row_hue_diagnosis
```

## Result

| group | expected | produced | furthest stage | death score | peak std | grayscale contrast | hsv_value score (counterfactual) |
|---|---|---|---|---|---|---|---|
| bottom, hue 120–136 (5 recordings) | 139 | **0** | low_std 78, below_floor 58, scored_unknown 3 | 0.47 | **0.016** | **0.008** | 0.60 |
| bottom, other hues 151/167/167/2 (4) | 102 | 91 | scored_death 91 | 0.71 | 0.079 | 0.086 | 0.52 |
| top, all hues 6–82 (9) | 204 | 182 | scored_death 182 | 0.71 | 0.216 | 0.125 | 0.46 |

Per recording (bottom row):

| recording | bottom hue | ally row | produced / expected | first failing stage | peak std | grayscale contrast |
|---|---|---|---|---|---|---|
| 22-20-42 | 120 | bottom | 0 / 26 | low_std 21, below_floor 4 | 0.017 | 0.026 |
| 20-33-31 | 125 | bottom | 0 / 24 | below_floor 24 | — | −0.023 |
| 23-56-41 | 131 | bottom | 0 / 34 | low_std 32, below_floor 2 | 0.017 | 0.024 |
| 18-18-00 | 136 | top | 0 / 36 | below_floor 22, low_std 12 | 0.016 | 0.004 |
| 22-31-53 | 136 | top | 0 / 19 | low_std 13, below_floor 6 | 0.015 | 0.004 |
| 18-25-16 | 151 | bottom | 10 / 12 | (scored_death) | 0.053 | 0.050 |
| 23-43-31 | 167 | bottom | 23 / 24 | (scored_death) | 0.077 | 0.087 |
| 22-37-47 | 167 | bottom | 23 / 25 | (scored_death) | 0.077 | 0.086 |
| 22-57-30 | 2 | bottom | 35 / 41 | (scored_death) | 0.109 | 0.102 |

## Where good evidence becomes bad

1. **Source pixels and lane crop: good.** At every expected position, saturated
   glyph pixels of the team hue are found (median hue 132). The crop image
   shows the glyphs clearly in colour. The counterfactual `hsv_value` death
   score (0.60) is as high as on rows that work. The row geometry is the same
   rows that succeed for other bottom hues.
2. **Preprocessing (grayscale): first material difference.** The glyph's
   grayscale level (0.19) is almost the background's. The contrast is 0.008,
   against 0.086 for other bottom hues and 0.125 for top rows. That is about a
   tenfold collapse.

   Grayscale conversion (`0.299 R + 0.587 G + 0.114 B`) maps saturated blue or
   violet to a dark grey close to the lane background. Magenta, pink and red
   keep their red component and stay bright.
3. **Template scores / candidate generation: where they are dropped.** With the
   glyph nearly flat in grayscale, the result depends on the frame:
   - the combined score falls below the 0.45 discovery floor (58 glyphs;
     death score down to 0.21 in 20-33-31); or
   - a peak survives but the production window std, 0.016, is under the 0.04
     gate (78 glyphs).

   Both failures come from the same contrast collapse.
4. **Echo suppression: not involved.** Not a single hue 120–136 glyph reaches
   echo suppression. This is a different failure from the neighbour-row echo
   problem seen elsewhere, which shows up as `echo` on rows that otherwise work
   (6 bottom and 16 top frames here).

## Association

- All 5 recordings with a bottom hue in 120–136 produce 0 of 139 expected
  bottom deaths.
- All 4 recordings with other bottom hues produce 91 of 102.
- Top rows, hues 6–82, produce 182 of 204 in all 9 recordings.

The failure does not depend on which team is the ally. Hue 136 fails both
when the bottom row is the opponents' (18-18-00, 22-31-53) and when it is the
ally's (the others). Across recordings, peak std and grayscale contrast rank
with the hue class without overlap.

## Limits

- Only two hue clusters were observed (120–136 vs 151–167 and 2). Where the
  boundary lies between 137 and 150, and whether a top-row blue fails the same
  way, is unobserved. No top row in these nine has a hue in 120–136.
- The expected positions are lamp-bracket midpoints with a 10 px match. This
  is a locator, not a label set; no user labels were inferred.
- Three static frames per recording, as in production.
- The `hsv_value` counterfactual is lower on top rows (0.46) than the
  production grayscale score (0.71), so simply swapping the representation is
  not shown to be safe. This is evidence for where to look, not a fix.

## Starting point for a later, separately pre-registered fix

The defect is **colour-to-luminance sensitivity in the grayscale
preprocessing**: it affects both the discovery-floor score and the
`event_glyph_min_std` gate. It is not echo suppression, not thresholds as
such, and not row geometry. Any fix must be pre-registered:

- candidates include a hue-robust preprocessing or a per-team-colour
  normalisation;
- it must be scored on these nine recordings as tuning data;
- it must show no regression on top rows and on rows that already work;
- it must be validated once on fresh recordings that include blue / violet
  teams on both rows.

It is independent of the alignment v2 gate.
