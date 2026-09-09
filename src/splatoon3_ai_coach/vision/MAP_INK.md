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

1. `MatchIntroDetector` (templates) captures `stage_id` + `battle_mode_id`.
2. Early-stop once both are known; fail-closed after `intro_deadline_seconds` if not.
3. Map ink runs only when identity is resolved **and** `MapOverlayReading.present`.

## Geometry

- Default: `configs/stage_maps/<stage_id>/default.yaml`
- Optional override: `configs/stage_maps/<stage_id>/<battle_mode_id>.yaml`
- Missing override → stage default; missing default → skip ink for that frame

## Outputs

- `match_identity.json` — resolved stage/mode (or unresolved / map_ink disabled)
- `map_observations.json` — sparse list of `MapObservation` (not continuous state)
- Optional `debug_map_ink/` overlays when diagnostics enabled

## Limitations

- Live match map only; review/aerial map not distinguished yet.
- Ally/opponent HSV defaults are placeholders — tune per footage/team colors.
- Stage packs and intro templates must be provided before production use.
- No CoachInput / MapObservationClock in this phase.
