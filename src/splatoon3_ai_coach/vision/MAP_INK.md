# Visible ink coverage within registered gameplay polygons

Production uses only `configs/stage_maps/<stage_id>/stage_mask.yaml`. The 25
canonical files contain registered gameplay polygons; Museum's canonical polygon
was subsequently redrawn. Earlier source drawings remain under
`gameplay/<source_mode>/` as provenance; that mode is not a runtime layout selector.
Older rectangle YAML packs and explicit
legacy helper support remain inactive historical compatibility.

## Meaning and geometry

The metric is **visible ink coverage within the registered gameplay polygon**.
The outline is not claimed to contain only paintable terrain. Every pixel
outside the rasterized polygon is excluded, and every pixel inside contributes
to its denominator. Production does not load rectangular regions, perform
alignment, adjust vertices, segment terrain, or mask objects.

One registered Mahi-Mahi polygon applies throughout the match. Surfaces that
are submerged or visually unpainted still contribute to the polygon area, so
this is not coverage of currently accessible paintable terrain. No tide-specific
geometry or mode-specific layouts are selected.

## Color and validity

- Use the latched match-specific team hues, circular tolerance ±15 OpenCV units.
- Calculate HUD hue centers with a circular median within each slot and across
  accepted frames. Saturated background pixels within a slot can still bias a
  center; the circular calculation does not distinguish the icon from its
  background.
- Require saturation >= 70 and brightness >= 40. Grey, very dark and washed-out
  pixels contribute to neither team; no color conversion, elevation or shadow
  model is applied.
- Preserve stage identity, Ready?, successful post-live-play calibration,
  positive map-overlay detector evidence at the existing map-overlay confidence
  floor (the same configured threshold fusion uses), and sampling cadence gates.
- Missing, invalid or wrong-stage polygons produce no observation. There is
  no production rectangle fallback.
- Low/zero classified fraction is not a validity failure. The legacy
  `map_ink.min_usable_confidence` setting is retained for config compatibility
  but is ignored for production polygon observations. The map-ink `confidence`
  field has been removed. Coverage is never a quality score; actual detector
  confidence remains separate.
- The map detector's existing heuristics are unchanged; this registration
  change does not establish detection accuracy on every obscured/incorrect frame.

## Stored observations

`map_observations.json` contains sparse `MapObservation` records, not continuous
state or GameEvents. Production records use `sample_mask_source=registered_polygon`,
`sample_polygon_sha256` (SHA-256 of the normalized OverlayMapPolygonConfig
JSON), empty legacy `regions`, video time, detector evidence IDs and pixel
counts. Old records may lack the polygon hash and contain rectangle-region
diagnostics.

For polygon pixels N, ally pixels A and opponent pixels O:

| Field / quantity | Meaning |
| --- | --- |
| `visible_ink_fraction` | (A + O) / N: visible ink coverage |
| `ally_share_of_detected_ink` | A / (A + O): ally share of detected ink |
| `opponent_share_of_detected_ink` | O / (A + O): opponent share of detected ink |
| Ally polygon coverage (derived) | A / N |
| Opponent polygon coverage (derived) | O / N |
| `neither_team_pixels` | N - A - O; contributes to neither team |

A valid observation with no classified pixels has 0 visible ink coverage and
undefined ally/opponent shares. No redundant derived percentage fields are added.

## Timeline association

Video time is canonical. Fusion independently builds MAP_OVERLAY intervals;
observations retain detector evidence IDs rather than an overlay event ID.
ScenarioContext selects sparse observations by its existing evidence window and
bounded nearest-before lookup. Observations at/after MATCH_END are removed from
coaching evidence by the existing loader. No sample is held as continuous ink
state. No new game-clock persistence is added; only existing raw-timer lookup
can supply a nearby observed clock, preserving its own timestamp and gap.

## Review artifacts

`analysis/map_ink_validation/polygon_registration_2026-10-30/index.html` contains
25 human PASS/FAIL registration images (original left, registered outline and
light interior tint right). The operator marked all 25 original registrations
PASS. Museum was redrawn afterward; its current registered polygon is pinned by
`tests/test_vision_stage_mask.py`. These images verify geometry, not ink accuracy.
Fresh representative analyses are in that directory's `fresh/` subdirectory;
they predate circular hue calibration. A new video scan is needed to recompute
observations with the current calibration; this was not required for closeout.
Existing analyses and sealed research records were not rewritten.

## Accepted production limitations

The current measurement is accepted with known imperfections: visible ink can be
underreported, stage-specific color calibration remains imperfect (including
Umami), and objects inside the polygon are not excluded. Polygon boundaries
approximate the visible map and do not identify paintable terrain; Mahi-Mahi
uses one polygon across tide states. These limitations can affect ink values
without changing fusion events or scenario ownership. No further accuracy work
is part of this implementation closeout.

## Historical geometry findings

The following material concerns earlier rectangle packs and prior studies. It
does not validate the newly registered polygons or the current coverage policy.

## Geometry validation status (Manta / Museum)

Offline multi-frame check: `tools/diagnostics/map_ink_geometry_validate.py`
→ `analysis/map_ink_validation/VALIDATION_REPORT.md`.

| Stage | Status |
|-------|--------|
| `manta_maria` | R01 top at `y=0.10`: **no repeatable defect** on available frames (`CONSISTENT`) |
| `museum_dalfonsino` | Far-right wing: **REPEATABLE_DEFECT** on confirmed Museum frames; fixed by extending R01/R02/R03 right edges (`x2`→~0.89–0.90). Post-fix: `CONSISTENT` |

Targeted Manta R01 + Museum far-right checks: **passed**. Attribution matcher for Museum auto-discovery remains weak (separate).

Catalog snapshot (all 25 packs vs Sanpo/`_game` refs):
`analysis/map_ink_validation/geometry_sanity_25/GEOMETRY_SANITY_25.md`

### Next architecture (deferred)

```text
MAP_OVERLAY interval
    → sparse MapObservations
    → MapObservationClock.at(t, max_gap_seconds=...)
    → scenario / CoachInput evidence
```

Mirror `PlayerCountClock` max-gap semantics. Do not implement until geometry
freeze clears.

## Explicit naming and compatibility

New JSON uses `visible_ink_fraction`, `ally_share_of_detected_ink`,
`opponent_share_of_detected_ink`, `neither_team_pixels`,
`sample_polygon_sha256`, and `sample_mask_source=registered_polygon`.
Neither MapObservation nor its MapInkSample ScenarioContext projection has a
`confidence` field. Old names are input-only validation aliases. Legacy
`confidence` input is ignored, never exposed as a property or serialized.
The viewer normalizes old keys to the same names. Existing stored research
artifacts are not rewritten merely for this naming change.

Production calls `analyze_overlay_map_ink(image, polygon, classifier, ...)`,
with `OverlayMapPolygonConfig` and explicit helpers from
`vision/overlay_map_polygon.py`. The `vision/stage_mask.py` module retains old
import aliases for offline callers. The existing `stage_mask.yaml` asset names
remain stable. Polygon hashes are unchanged because the normalized data and
vertices are unchanged. `analyze_map_ink` and its optional rectangle input are
legacy compatibility; production does not use that fallback.
