# Team colour calibration: downstream exposure (read-only code trace)

Status: **no current coaching consumer; latent risk only.** No code, config
or artifact was changed.

## Question

The v2 diagnosis (`REPORT.md`) found that the gameplay team colour
calibration can latch onto pre-HUD scenery (sky, hue about 103). That
calibration also sets the ink colours for map ink. Does a scenery latch
reach anything that coaching consumes?

## Where the calibration goes

`vision/team_color_calibration.py` latches `ally_h` / `opponent_h`
(`match_identity.json`). It then has two consumers:

1. **Map ink.** `MapInkClassifier.apply_calibration`
   (`vision/map_ink.py`) builds the ally and opponent hue profiles. These
   classify ink in `map_observations.json`, which
   `analysis/scenario_context.py` attaches as `ScenarioContext.map.ink`.
2. **Review `ally_row`.** `review/team_identity.py` picks the Review row
   whose hue is nearest `ally_h`. This feeds only research alignment, the
   subject of `REPORT.md`.

Zone control (`vision/zone_control*.py`) does not use the calibration.

## Who reads `ScenarioContext.map.ink` (as of 594a00e)

| Consumer | Reads `map.ink`? |
|---|---|
| Model input `build_coach_llm_view` (`coach/llm_view.py`) | No. `_project_map` emits only `map_check_before_death`, `seconds_since_map_check_before_death`, `map_check_count` and `map_checked_while_dead` |
| Death ranking (`coach/death_importance.py`) | No |
| Claim support (`coach/claim_basis.py`) and the validator | No |
| Public results (`tools/public_site/results.py`) | No |
| Developer viewer (`tools/vision_manifest_viewer`) | Yes, for display only |

Map ink samples never set the map-check fields (`EVIDENCE_CONTRACT.md`), so
a wrong ink profile cannot change `map_check_*` either.

## Candidate count (not a verdict)

Stored analyses, deduplicated by video identity (58 videos): a calibrated
hue between 85 and 110 on either side appears in **11 of 23 Splat Zones
matches** against **1 of 35 matches in every other mode**. That band is a
candidate finder only. Real cyan team colours fall in it too, so these
counts do not establish that any calibration is wrong.

## Conclusion

- A scenery-latched calibration **does not currently change any coaching
  output**: not the model input, ranking, claims or public results.
- The exposure is **latent**. It becomes real the moment `map.ink` is
  projected into the model input, used by ranking or claims, or shown
  publicly.
- **Trigger for a frame-level audit**, before any such wiring:
  - take an independent reference from roster-icon hues on in-match frames;
  - separate real cyan from scenery latches;
  - measure the change in ink fractions between the stored and reference
    profiles.

  Only then decide on a calibration fix.
- The Review `ally_row` side swap remains as recorded in `REPORT.md`.
