# N2 citation-compliance experiment: dry run (no LLM calls)

- Provider / model: `cursor` / `gpt-5.6-luna`; planned calls 24 (2 repeats); model calls made: 0
- Two rebuilds byte-identical: True
- Stale inputs: none
- Hash problems: none
- Production since prompt commit: none
- Scorer smoke failures: none

## Scorer smoke test (`en-live-playerview-2026-10-10 23-43-31` `death_episode:205.000`)

| citation | expected | strict | form | validator | validator accepts |
| --- | --- | --- | --- | --- | --- |
| `clock.display = "1:55"` | n2_scalar_correct | n2_scalar_correct | exact | view_native | True |
| `clock.seconds_remaining = 115` | n2_scalar_correct | n2_scalar_correct | exact | view_native | True |
| `death.death_to_active_again.value = 8.5 seconds` | n2_scalar_correct | n2_scalar_correct | remark | view_native | True |
| `clock.display = 1:55` | n2_scalar_correct | n2_scalar_correct | lenient | view_native | True |
| `related[0].relations.leads_to_death_episode_id = "death_episode:226.000"` | n2_scalar_correct | n2_scalar_correct | exact | view_native | True |
| `claim_basis[0].claim_id = "death_low_ink"` | n2_scalar_correct | n2_scalar_correct | exact | view_native | True |
| `death.death_to_active_again.end_gap_seconds = null` | n2_scalar_correct | n2_scalar_correct | exact | view_native | True |
| `Clock: clock.display = "1:55"` | n2_scalar_correct | n2_scalar_correct | exact | view_native | True |
| `clock.display = "1:55", clock.seconds_remaining = 115` | n2_scalar_correct, n2_scalar_correct | n2_scalar_correct, n2_scalar_correct | exact, exact | view_native | True |
| `clock.seconds_remaining = 116` | n2_scalar_wrong_value | n2_scalar_wrong_value | wrong | view_native | False |
| `death.death_to_active_again.value = 8.54` | n2_scalar_wrong_value | n2_scalar_wrong_value | wrong | view_native | True |
| `death.death_to_active_again = 8.5` | n2_object_asserted | n2_object_asserted | None | view_native | False |
| `clock.source_path = "game_clock_samples[label=death]"` | source_path_field | source_path_field | None | view_native | True |
| `game_clock_samples[label=death].observation.seconds_remaining = 115` | legacy_label | legacy_label | None | source_path_ref | False |
| `game_clock_samples[label=death].display = "1:55"` | legacy_label | legacy_label | None | source_path_ref | True |
| `primary_context.timeline.time_since_previous_death.value = 1.0` | n1_coachinput | n1_coachinput | None | unresolved | False |
| `related[0].scenario.start_time = 1.0` | n1_coachinput | n1_coachinput | None | unresolved | False |
| `map_check_before_death.status = "observed"` | relative_path | relative_path | None | unresolved | False |
| `clock.seconds_left = 1` | unresolved_path | unresolved_path | None | unresolved | False |
| `enemy.weapon = "splattershot"` | unsupported_invented | unsupported_invented | None | unresolved | False |
| `death.death_to_active_again.value` | malformed | malformed | None | view_native | True |
| `clock.display: "1:55"` | malformed | malformed | None | view_native | True |
| `The clock showed the final seconds of the match` | malformed | malformed | None | unresolved | False |
