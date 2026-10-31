# Polygon registration implementation handoff

Naming follow-up: production now emits explicit visible-ink/share fields,
`neither_team_pixels`, `sample_polygon_sha256`, and
`sample_mask_source=registered_polygon`, with no paint `confidence`. The
implementation uses `OverlayMapPolygonConfig`. The results below describe the
preceding verification run; those stored JSON files retain their original field
names and remain readable through input-only compatibility aliases. See
`src/splatoon3_ai_coach/vision/MAP_INK.md` for the current schema.

2026-10-30 closeout. The implementation is sealed with 25 registered gameplay
polygons and circular hue calibration. The operator's registration review
returned PASS for all 25 original outlines; Museum was then intentionally
redrawn, and its current 28-vertex polygon is pinned by the stage-mask test.
The current Museum hash is
`4cb3b5a592f7af2c3f06e134cece66514b67c2e26f922df1172b427580d2f4a2`.

The visible-ink review found imperfect estimates on several stages. The
operator accepted those limits for this release; accuracy tuning and broader
recording reprocessing were not part of closeout.

## What changed

- All 25 canonical `configs/stage_maps/<stage>/stage_mask.yaml` files are production geometry. Twenty-four match their saved gameplay drawings byte-for-byte; Museum uses the operator's subsequent redrawn outline. The `gameplay/<source_mode>/` copies remain provenance, not selectable mode-specific layouts.
- Production no longer loads rectangular packs or falls back to rectangle unions. Old rectangle YAML and explicit historical helper support remain inactive compatibility. Older root polygons have been replaced.
- The metric is visible ink coverage within the registered gameplay polygon; the outline is not a claim about paintable terrain. One Mahi-Mahi polygon is used throughout the match. Submerged/unpainted surfaces stay in the denominator.
- Circular hue centers use a circular median within HUD slots and across accepted frames. Tolerance remains ±15 OpenCV units, saturation >=70, brightness >=40. No color conversion, object exclusions, automatic alignment, terrain segmentation or tide/mode-specific layouts were added.
- Low/zero ink fraction no longer rejects a valid sample. The misleading paint `confidence` field was removed. Legacy `map_ink.min_usable_confidence` is ignored for production sampling.
- Stage identity, Ready?, calibrated team colors, positive overlay reading and cadence checks remain. Sampling now also applies the existing map-overlay confidence floor (0.50), already used by fusion. A wrong-stage polygon is rejected.
- New observations contain `sample_polygon_sha256`, computed over the normalized registered polygon. This provides geometry provenance without a new geometry registry or game-clock persistence.
- Sparse video-time observations, existing ScenarioContext selection and match-end filtering remain; no continuous ink fusion or event-ownership changes.

## Files changed for this implementation

- `configs/stage_maps/<stage>/stage_mask.yaml`: all 25 canonical polygons (linked below).
- `src/splatoon3_ai_coach/vision/map_ink.py`: polygon-only production support without rectangle dependency; optional polygon hash; legacy helper compatibility retained.
- `src/splatoon3_ai_coach/vision/match_side_effects.py`: registered-mask selection, no painted-fraction rejection, existing independent overlay-confidence gate.
- `src/splatoon3_ai_coach/vision/stage_mask.py`: geometry hash and wrong-stage registration rejection.
- `src/splatoon3_ai_coach/vision/stage_maps.py`: documentation identifying its historical-only role.
- `src/splatoon3_ai_coach/config/models.py`, `configs/default.yaml`: explain the retained legacy ink-fraction setting.
- `src/splatoon3_ai_coach/vision/MAP_INK.md`: metric definitions, gates, provenance, Mahi-Mahi limitation and review procedure.
- `tests/test_vision_a1_calibration_window.py`, `tests/test_vision_stage_mask.py`, `tests/test_vision_team_color_calibration.py`: production gates, low/zero retention, exact 25 registrations, invalid identity and shade tests.
- This handoff document and the generated review artifacts below.

The accepted 5-second calibration window is included in the implementation. No unrelated changes were reverted.

## Automated checks

```sh
.venv/bin/pytest -q \
  tests/test_vision_a1_calibration_window.py tests/test_vision_map_ink.py \
  tests/test_vision_stage_mask.py tests/test_vision_team_color_calibration.py \
  tests/test_vision_ready.py tests/test_vision_pipeline.py \
  tests/test_architecture_execution_boundary.py \
  tests/test_analysis_scenario_context.py tests/test_coach_evidence_contract.py
```

Closeout verification: the focused vision and downstream suite passed **166
tests**, with **1 skipped** because its referenced historical manifest is absent.
`ruff check` and `git diff --check` also passed.

Additional artifact assertions: all 25 registrations load and rasterize; the 24 unchanged outlines match their saved source drawings, while Museum matches its intentionally redrawn canonical polygon. Existing fresh observations have expected polygon hashes, registered-polygon source, no rectangle regions, and exact pixel accounting. Fresh observations predate the circular-hue correction and are not used here as accuracy claims.

Zero and very low coverage are proven by synthetic calibrated production-path tests, including a deliberately high legacy ink-fraction floor. Neither real representative run happened to contain a 0% or <15% observation.

## Human registration review

[Open the 25-image gallery](../../analysis/map_ink_validation/polygon_registration_2026-10-30/index.html). Each full-resolution PNG shows the original reference on the left, registered yellow boundary and 12% interior tint on the right, plus stage identity and polygon hash. The operator's exported review marked all 25 original registrations PASS. Museum was redrawn afterward; its exact current outline and hash are pinned by `tests/test_vision_stage_mask.py`. These are geometry checks, not ink-accuracy claims.

| Stage | Canonical polygon | Verification image |
| --- | --- | --- |
| barnacle_and_dime | [YAML](../../configs/stage_maps/barnacle_and_dime/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/barnacle_and_dime.png) |
| bluefin_depot | [YAML](../../configs/stage_maps/bluefin_depot/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/bluefin_depot.png) |
| brinewater_springs | [YAML](../../configs/stage_maps/brinewater_springs/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/brinewater_springs.png) |
| crableg_capital | [YAML](../../configs/stage_maps/crableg_capital/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/crableg_capital.png) |
| eeltail_alley | [YAML](../../configs/stage_maps/eeltail_alley/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/eeltail_alley.png) |
| flounder_heights | [YAML](../../configs/stage_maps/flounder_heights/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/flounder_heights.png) |
| hagglefish_market | [YAML](../../configs/stage_maps/hagglefish_market/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/hagglefish_market.png) |
| hammerhead_bridge | [YAML](../../configs/stage_maps/hammerhead_bridge/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/hammerhead_bridge.png) |
| humpback_pump_track | [YAML](../../configs/stage_maps/humpback_pump_track/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/humpback_pump_track.png) |
| inkblot_art_academy | [YAML](../../configs/stage_maps/inkblot_art_academy/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/inkblot_art_academy.png) |
| lemuria_hub | [YAML](../../configs/stage_maps/lemuria_hub/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/lemuria_hub.png) |
| mahi_mahi_resort | [YAML](../../configs/stage_maps/mahi_mahi_resort/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/mahi_mahi_resort.png) |
| makomart | [YAML](../../configs/stage_maps/makomart/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/makomart.png) |
| manta_maria | [YAML](../../configs/stage_maps/manta_maria/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/manta_maria.png) |
| marlin_airport | [YAML](../../configs/stage_maps/marlin_airport/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/marlin_airport.png) |
| mincemeat_metalworks | [YAML](../../configs/stage_maps/mincemeat_metalworks/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/mincemeat_metalworks.png) |
| museum_dalfonsino | [YAML](../../configs/stage_maps/museum_dalfonsino/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/museum_dalfonsino.png) |
| robo_rom_en | [YAML](../../configs/stage_maps/robo_rom_en/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/robo_rom_en.png) |
| scorch_gorge | [YAML](../../configs/stage_maps/scorch_gorge/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/scorch_gorge.png) |
| shipshape_cargo_co | [YAML](../../configs/stage_maps/shipshape_cargo_co/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/shipshape_cargo_co.png) |
| sturgeon_shipyard | [YAML](../../configs/stage_maps/sturgeon_shipyard/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/sturgeon_shipyard.png) |
| umami_ruins | [YAML](../../configs/stage_maps/umami_ruins/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/umami_ruins.png) |
| undertow_spillway | [YAML](../../configs/stage_maps/undertow_spillway/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/undertow_spillway.png) |
| urchin_underpass | [YAML](../../configs/stage_maps/urchin_underpass/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/urchin_underpass.png) |
| wahoo_world | [YAML](../../configs/stage_maps/wahoo_world/stage_mask.yaml) | [Original + registered polygon](../../analysis/map_ink_validation/polygon_registration_2026-10-30/verification/wahoo_world.png) |

## Representative fresh results

Both were full fresh scans in separate output folders; neither existing analysis nor source video was changed.

| Recording | Observations | Polygon pixels | Visible ink range | Frames | Stored observations |
| --- | ---: | ---: | --- | --- | --- |
| brinewater | 5 | 425,525 | 26.16%–35.98% | [Preview](../../analysis/map_ink_validation/polygon_registration_2026-10-30/fresh/brinewater/fresh_frames.jpg) | [JSON](../../analysis/map_ink_validation/polygon_registration_2026-10-30/fresh/brinewater/map_observations.json) |
| mahi_mahi | 5 | 387,545 | 28.39%–42.75% | [Preview](../../analysis/map_ink_validation/polygon_registration_2026-10-30/fresh/mahi_mahi/fresh_frames.jpg) | [JSON](../../analysis/map_ink_validation/polygon_registration_2026-10-30/fresh/mahi_mahi/map_observations.json) |

Fresh Brinewater source: `~/Movies/ja_brinewater_springs_triple_splashdown_2026-09-16 20-27-13.mov`. Fresh Mahi-Mahi source: `~/Movies/ja_mahi_mahi_kraken_2026-09-16 20-18-04.mov`. Both used configs/default.yaml with the CLI language override ja. The output folders also contain match_identity.json, vision_manifest.json and rebuilt ScenarioContexts.

## Unexpected findings and assumptions

1. Visible ink coverage can be underreported. This is accepted for the current production metric.
2. Per-stage color calibration remains imperfect, including the observed Umami shift under circular hue medians. Current hue tolerance and calibration policy are accepted as implemented.
3. No object exclusions are applied. HUD elements, player icons, checkpoints and other in-polygon objects can affect the count.
4. Polygons approximate the viewed map and do not represent paintable terrain. Boundary approximations, unpaintable surfaces, and Mahi-Mahi tide variation remain accepted limitations; one Mahi-Mahi polygon is used throughout.
5. Ink observations remain sparse ScenarioContext evidence. Fusion events and scenario ownership are unchanged.

No broader reprocessing, object-mask work, color correction, alignment or clock persistence was started.
