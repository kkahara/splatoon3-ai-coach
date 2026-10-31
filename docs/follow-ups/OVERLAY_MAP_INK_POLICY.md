# Overlay map ink: decisions and reusable findings

Recorded 2026-10-30. This document records the operator's practical overlay-map
policy, implementation status and existing evidence. It does not amend or
relabel historical research results. No further broad overlay-map investigation
is planned. Review top-down maps remain a separate future project.

## Decisions and implementation status

| Topic | Decision | Status |
| --- | --- | --- |
| Hue | Circular tolerance ±15 OpenCV hue units (±30 degrees) | Already configured |
| Color sampling | First 5 seconds after qualifying in-match entry; Ready? and visible roster required | Implemented; three acceptable samples still required |
| Dark / washed-out / grey | Count toward neither team's ink; no camera, elevation or shadow model | Existing S >= 70 / V >= 40 gates implement this practical convention |
| Exterior | Exclude all pixels outside the rasterized operator polygon | Aggregates and region diagnostics masked |
| Missing polygon | Wait for operator drawing; no production rectangle fallback | Implemented |
| Color conversions | Add only for a concrete recurring mismatch outside the existing tolerance | None justified by the ten examples below |
| Object exclusions | Baskets, squid/octopus player icons and Rainmaker checkpoints excluded from painted counts | Not yet implemented; need concrete reference images/patterns |
| Tower | Painted Tower Control tower surfaces remain eligible ink | No blanket objective-object exclusion introduced |
| Review top-down | Investigate separately and use a smaller range there | Deferred; no Review classifier added |

The persisted field `unclassified_pixels` includes all pixels rejected by the
color classifier. Under this overlay convention they contribute to neither
team, without a physical claim that no ink exists there. Existing
`ally_classified_fraction` / `opponent_classified_fraction` divide by classified
ink pixels, not polygon area. Whole-polygon fractions can be calculated as
`ally_ink_pixels / total_sample_pixels` and the opponent equivalent. Samples
below the existing minimum classified-fraction gate may still be omitted.

## Existing HUD-to-map color examples

These are representative points already selected in the completed Amendment 3
study, not a new broad experiment. HUD values come from the stored A1
`match_identity.json` files under `analysis/_a1_amendment2_dev/`. Map values were
read from the original video at the stored cadence-frame timestamp and the
already-recorded swatch coordinates. Each swatch is one pixel, not a measured
distribution of every shade in a match. Distances are circular on [0, 180).

| Recording | Team | HUD H | Map H | Distance | Video time | Map pixel x,y |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 18-18-00 | ally | 9 | 7 | 2 | 121.0 | 888,546 |
| 18-18-00 | opponent | 136 | 135 | 1 | 122.5 | 1109,315 |
| 20-33-31 | ally | 122 | 126 | 4 | 75.5 | 932,586 |
| 20-33-31 | opponent | 33 | 31 | 2 | 155.0 | 1031,600 |
| 22-31-53 | ally | 8 | 5 | 3 | 161.5 | 1006,640 |
| 22-31-53 | opponent | 136 | 137 | 1 | 151.0 | 940,422 |
| 22-37-47 | ally | 167 | 162 | 5 | 64.0 | 716,733 |
| 22-37-47 | opponent | 82 | 89 | 7 | 217.5 | 1295,204 |
| 22-57-30 | ally | 3 | 179 | 4 | 183.0 | 784,652 |
| 22-57-30 | opponent | 86 | 87 | 1 | 54.5 | 856,538 |

For the blue example (20-33-31), the map swatch is slightly more toward purple
than the HUD hue. The shift is only four units. All ten examples pass ±15;
none calls for a hardcoded conversion. This does not establish that all map
pixels pass. Add a narrow conversion only if a repeated, visible mismatch
actually exceeds the existing band.

Source records: `analysis/review_validation/alignment/v4/amendment3/`
`swatches.json`, `token_key.json`, `team_identity_stage1.json`, and
`extraction/<recording>.json`. Original recordings are
`~/Movies/en-live-playerview-2026-10-11 <recording>.mov`.

## Useful investigation results

| Finding | Practical use | What it does not establish |
| --- | --- | --- |
| 22-20-42 gets its third acceptable HUD sample at 29.5 s | Supports the accepted 3-to-5-second window extension | Full-match ink accuracy |
| Ten existing swatches differ from HUD by only 1–7 H units | Keep ±15; no conversion needed for these examples | Accuracy on every shade/object |
| Stage polygons already exclude exterior pixels | Reuse existing seven; draw the remaining eighteen | Interior object filtering |
| Museum rectangle omitted its right wing; corrected in the old geometry study | Reference for checking a drawing against a real map image | Automatic correction of a polygon, which overrides rectangles |
| Targeted Manta top-boundary check found no repeatable defect | No need to change that edge based on the old concern alone | All-stage geometry accuracy |
| Old A1 point accuracy 70.7%, baseline 62.6%; inversions about 0–3% per recording | Shows team swapping was a relatively small part of sampled error | Accuracy under the newly accepted no-ink policy or full-area percentage error |
| Several relative comparisons were inconclusive | Historical limitation only | A reason to reopen broad overlay research |

Old accuracy results cannot be rescored just by accepting the new policy:
they used their own sampled domain and labels. Keep those results unchanged.
The old comparisons also included rectangle-only stages that production now
skips until a polygon exists. No Review top-down conclusions follow from them.

## Polygon vertex cost

Local 1920x1080 benchmark of the existing `stage_mask_to_bool`, including mask
allocation, vertex conversion and OpenCV polygon fill: 10 warmups and median
of 100 calls for each regular polygon, erosion zero.

| Vertices | Median milliseconds per rasterization |
| ---: | ---: |
| 15 | 0.065 |
| 30 | 0.071 |
| 60 | 0.074 |
| 120 | 0.087 |

This is a local microbenchmark, not a guarantee for every contour or computer.
Even the measured increase to 120 vertices is only 0.022 ms per observation.
Mask storage remains one image-sized mask. Use 30–60 vertices, or more where
useful, without keeping an artificial 15-vertex cap. Follow the contour in
order and avoid self-intersections.

## Reuse the existing editor

No replacement GUI is needed. From the repository root:

```sh
.venv/bin/python tools/calibration/stage_mask_calibrate.py \
  --stage makomart \
  analysis/map_ink_validation/overlay_examples/18-18-00_map.png
```

Left click adds a vertex; `u` undoes; `c` closes and previews; Enter or `s`
saves; `r` resets; Escape or `q` cancels. There is no vertex cap. The tool
writes `configs/stage_maps/<stage>/stage_mask.yaml`. A video can also be passed
with `--time <seconds>`; omit the input to use the existing file picker.

Prepared original frames also include `20-33-31_map.png` (Shipshape Cargo Co.)
and `22-37-47_map.png` (Wahoo World) beside the MakoMart image.

Existing polygons: barnacle_and_dime, hagglefish_market, hammerhead_bridge,
manta_maria, museum_dalfonsino, robo_rom_en, undertow_spillway.

Still to draw: bluefin_depot, brinewater_springs, crableg_capital, eeltail_alley,
flounder_heights, humpback_pump_track, inkblot_art_academy, lemuria_hub,
mahi_mahi_resort, makomart, marlin_airport, mincemeat_metalworks,
scorch_gorge, shipshape_cargo_co, sturgeon_shipyard, umami_ruins,
urchin_underpass, wahoo_world.
