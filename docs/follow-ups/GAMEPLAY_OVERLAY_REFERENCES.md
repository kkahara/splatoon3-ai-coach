# Gameplay overlay references for polygon drawing

Prepared 2026-10-30. Polygon work only; no production selection, color conversion, object exclusion or automatic registration implemented.

Found 180 candidate frames across all 25 configured stages. One primary frame per stage has been visually inspected for an open gameplay map. Additional candidates are retained for alternatives and mode variants. Existing polygons were not overwritten.

## Where to find the images

- All prepared files: `analysis/map_ink_validation/gameplay_references/`
- Primary images: `selected/<stage_id>/<battle_mode_id>/frame.png`
- Primary preview: `selected_overview.jpg`
- Source video, requested seek time, original dimensions and identity basis: `selected.json`
- All candidates and provenance: `candidates.json`; per-stage contact sheets: `<stage_id>_contact.jpg`

## Scope and limitations

- Images preserve the decoded video frame at its original dimensions. There is no cropping, resizing, geometric correction or UI removal in the selected PNGs. Contact sheets alone are resized/labelled. Source videos are unchanged.
- These are real gameplay overlays, not Recon screenshots or Review top-down maps. Several folders contain copies or reruns of the same recording; candidate counts are not counts of independent matches.
- Frame extraction used OpenCV time seeking. Recorded video_time is the requested seek time, not a frame-exact timing calibration. That is sufficient for drawing on the extracted image.
- Stage/mode identities mostly come from stored match_identity.json. The Humpback, ROM-en and Undertow recordings were additionally checked with opening-intro template detections; these details are in the JSON. Legacy stage-name spellings ealtail_alley and mincemeat-metalworks were normalized in this preparation inventory only.
- Fully opened overlays can still have UI covering a boundary, or a map edge extending beyond the viewport. The selected frame is a drawing reference, not a guarantee that every hidden contour is recoverable. Use an alternate frame where needed.
- Do not assume these outlines transfer to another mode, another map pose or another match. Availability by mode is recorded; not all modes are covered.
- Mahi-Mahi samples are intentionally tide-unconfirmed. The operator identified the earlier Recon image as low tide; that does not establish the tide of these gameplay images. High-tide references are still pending operator confirmation or a supplied image.
- The first three recent polygons (MakoMart, Wahoo World, Shipshape Cargo Co.) were already drawn on gameplay frames. Keep them; these additional samples can serve as checks. Preserve all Recon drawings when starting gameplay drawings.

## Primary references

| Stage | Primary mode | Video time (s) | Modes found | Image |
| --- | --- | ---: | --- | --- |
| barnacle_and_dime | splat_zones | 77.000 | splat_zones | [frame](../../analysis/map_ink_validation/gameplay_references/selected/barnacle_and_dime/splat_zones/frame.png) |
| bluefin_depot | splat_zones | 158.500 | splat_zones | [frame](../../analysis/map_ink_validation/gameplay_references/selected/bluefin_depot/splat_zones/frame.png) |
| brinewater_springs | splat_zones | 209.967 | splat_zones | [frame](../../analysis/map_ink_validation/gameplay_references/selected/brinewater_springs/splat_zones/frame.png) |
| crableg_capital | splat_zones | 49.500 | splat_zones | [frame](../../analysis/map_ink_validation/gameplay_references/selected/crableg_capital/splat_zones/frame.png) |
| eeltail_alley | turf_war | 198.500 | turf_war | [frame](../../analysis/map_ink_validation/gameplay_references/selected/eeltail_alley/turf_war/frame.png) |
| flounder_heights | splat_zones | 110.500 | splat_zones, tower_control | [frame](../../analysis/map_ink_validation/gameplay_references/selected/flounder_heights/splat_zones/frame.png) |
| hagglefish_market | splat_zones | 61.000 | clam_blitz, splat_zones | [frame](../../analysis/map_ink_validation/gameplay_references/selected/hagglefish_market/splat_zones/frame.png) |
| hammerhead_bridge | clam_blitz | 173.500 | clam_blitz, rainmaker, tower_control | [frame](../../analysis/map_ink_validation/gameplay_references/selected/hammerhead_bridge/clam_blitz/frame.png) |
| humpback_pump_track | turf_war | 42.000 | turf_war | [frame](../../analysis/map_ink_validation/gameplay_references/selected/humpback_pump_track/turf_war/frame.png) |
| inkblot_art_academy | turf_war | 114.000 | splat_zones, turf_war | [frame](../../analysis/map_ink_validation/gameplay_references/selected/inkblot_art_academy/turf_war/frame.png) |
| lemuria_hub | turf_war | 144.500 | turf_war | [frame](../../analysis/map_ink_validation/gameplay_references/selected/lemuria_hub/turf_war/frame.png) |
| mahi_mahi_resort (tide unconfirmed) | splat_zones | 42.042 | splat_zones | [frame](../../analysis/map_ink_validation/gameplay_references/selected/mahi_mahi_resort/splat_zones/frame.png) |
| makomart | tower_control | 205.500 | tower_control | [frame](../../analysis/map_ink_validation/gameplay_references/selected/makomart/tower_control/frame.png) |
| manta_maria | turf_war | 187.000 | turf_war | [frame](../../analysis/map_ink_validation/gameplay_references/selected/manta_maria/turf_war/frame.png) |
| marlin_airport | splat_zones | 77.000 | splat_zones | [frame](../../analysis/map_ink_validation/gameplay_references/selected/marlin_airport/splat_zones/frame.png) |
| mincemeat_metalworks | splat_zones | 308.500 | splat_zones | [frame](../../analysis/map_ink_validation/gameplay_references/selected/mincemeat_metalworks/splat_zones/frame.png) |
| museum_dalfonsino | splat_zones | 85.500 | rainmaker, splat_zones, tower_control | [frame](../../analysis/map_ink_validation/gameplay_references/selected/museum_dalfonsino/splat_zones/frame.png) |
| robo_rom_en | clam_blitz | 190.000 | clam_blitz | [frame](../../analysis/map_ink_validation/gameplay_references/selected/robo_rom_en/clam_blitz/frame.png) |
| scorch_gorge | splat_zones | 124.000 | splat_zones | [frame](../../analysis/map_ink_validation/gameplay_references/selected/scorch_gorge/splat_zones/frame.png) |
| shipshape_cargo_co | rainmaker | 186.000 | rainmaker, splat_zones | [frame](../../analysis/map_ink_validation/gameplay_references/selected/shipshape_cargo_co/rainmaker/frame.png) |
| sturgeon_shipyard | splat_zones | 196.000 | splat_zones | [frame](../../analysis/map_ink_validation/gameplay_references/selected/sturgeon_shipyard/splat_zones/frame.png) |
| umami_ruins | clam_blitz | 75.000 | clam_blitz | [frame](../../analysis/map_ink_validation/gameplay_references/selected/umami_ruins/clam_blitz/frame.png) |
| undertow_spillway | tower_control | 246.000 | tower_control | [frame](../../analysis/map_ink_validation/gameplay_references/selected/undertow_spillway/tower_control/frame.png) |
| urchin_underpass | rainmaker | 65.500 | rainmaker | [frame](../../analysis/map_ink_validation/gameplay_references/selected/urchin_underpass/rainmaker/frame.png) |
| wahoo_world | splat_zones | 217.000 | rainmaker, splat_zones | [frame](../../analysis/map_ink_validation/gameplay_references/selected/wahoo_world/splat_zones/frame.png) |

## Start redrawing without overwriting the Recon polygon

Use the existing editor and an explicit separate output path. The example below collects the drawing as an asset; it does not register a new production layout resolver.

```sh
.venv/bin/python tools/calibration/stage_mask_calibrate.py \
  --stage bluefin_depot \
  --output configs/stage_maps/bluefin_depot/gameplay/splat_zones/stage_mask.yaml \
  analysis/map_ink_validation/gameplay_references/selected/bluefin_depot/splat_zones/frame.png
```

Left click adds vertices; `u` undoes; `c` closes/previews; Enter or `s` saves; Escape or `q` cancels. Stop after polygon collection; subsequent implementation requires a separate plan.
