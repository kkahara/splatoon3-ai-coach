# Map ink + match intro (observation layer)

## Terminology

| Term | Meaning | Representation |
| --- | --- | --- |
| **Event/interval** | Match map is visible | Existing `GameEventType.MAP_OVERLAY` from `MapOverlayDetector` |
| **Observation** | Sampled 2D ink measurement at a video time | `MapObservation` in `map_observations.json` |
| **State/value** | Ally/opponent classified fractions | Fields on `MapObservation` |
| **Region** | Geometric sampling rectangle | `configs/stage_maps/<stage_id>/…` |
| **Classifier** | Pixels → ally / opponent / other | `MapInkClassifier` (HSV ranges) |

`MAP_OVERLAY` answers: “the map is visible during this interval.”

`MapObservation` answers: “at this video time, this ink distribution was observable.”

Do **not** create `INK_COVERAGE_CHANGED` or treat ink as a `GameEvent`.
Do **not** interpolate between observations.
Do **not** attribute map X markers to the player’s death location in this phase.

## Gate

1. `MatchIntroDetector` (templates) captures `stage_id` + optional `battle_mode_id`.
2. Early-stop once both are known; after `intro_deadline_seconds`, stop accepting
   intro evidence. Stage-only is enough for map ink.
3. Map ink runs when `stage_id` is known **and** `MapOverlayReading.present`.
   Missing mode → `configs/stage_maps/<stage_id>/default.yaml`. Missing stage →
   map ink disabled for the match.

## Geometry

- Default: `configs/stage_maps/<stage_id>/default.yaml`
- Optional override: `configs/stage_maps/<stage_id>/<battle_mode_id>.yaml`
- Missing override → stage default; missing default → skip ink for that frame
- Rectangles are **overlapping sampling regions**, not exact polygons. They may
  include non-map pixels on purpose so the union covers the paintable map.
- Aggregate pixel counts use the **union** of regions (overlaps counted once).
  The classifier decides ally / opponent / other among sampled pixels.
- No baked vertical scale correction yet — validate ROIs on real game frames.

## Outputs

- `match_identity.json` — resolved stage/mode (or unresolved / map_ink disabled)
- `map_observations.json` — sparse list of `MapObservation` (not continuous state)
- Optional `debug_map_ink/` overlays when diagnostics enabled (white ROI boxes,
  cyan union contour, green/red ink classes, dim unclassified-in-union)

## Limitations

- Live match map only; review/aerial map not distinguished yet.
- Ally/opponent HSV defaults are color channels, not team assignment.
- Stage packs and intro templates must be provided before production use.
- No CoachInput / MapObservationClock in this phase.

## Geometry validation status (Manta / Museum)

Offline multi-frame check: `tools/map_ink_geometry_validate.py`
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
