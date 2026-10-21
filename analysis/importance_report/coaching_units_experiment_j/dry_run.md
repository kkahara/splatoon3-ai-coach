# Experiment J dry run (stop point 3; no model calls)

- Dry-run criterion (compliant outputs: 0 hits after the pre-registered C adjustment and the Amendment 3 R5 window exemption; literal block paths resolve; non-qualifying R5 hits stay counted): **met**
- Amendment 3 non-qualifying R5 hits kept: {'a3_unanchored_absence': True, 'a3_absence_with_alive_check': True}
- Seals: verified. Census fact mismatches: none
- Stale inputs: none
- Invariants: none
- Literal block paths: none
- Populations (deaths): {'P1': 35, 'P2': 5, 'P3': 20, 'C': 6}
- Expected calls: {'P1': 140, 'P2': 20, 'P3': 40, 'C': 12, 'total': 212}; upper bound {'p1_p2': 200, 'p3': 40, 'c_expected': 10, 'total_approx_max': 250}; within bound: True; model calls made: 0
- System prompt chars: {'A': 16628, 'M': 17517, 'C': 17269}
- Experiment I stored outputs: 27 of 28 ablatable, 27 with no recommendation left; failures [{'call_id': 'M__2026_10_11_22_31_53_death_episode_208_500__r1', 'reasons': ['map_sentence_not_exactly_one_segment', 'map_in_remaining_segment']}]; matches design: True

## Compliant synthetic outputs

- M: {"adjusted_hits": 0, "adjusted_hits_by_map_status": {"not_observed": 0, "observed": 0}, "amendment_3_r5_window_exempt": 60, "blocked": 0, "c_adjustment_removed": 0, "outputs": 40, "outputs_with_adjusted_hits": 0, "outputs_with_production_hits": 30, "production_hits": 60, "production_hits_by_map_status": {"not_observed": 0, "observed": 60}, "rules": {"R5:map_absence": 30}, "value_fidelity_errors": 0}
- C: {"adjusted_hits": 0, "adjusted_hits_by_map_status": {"not_observed": 0, "observed": 0}, "amendment_3_r5_window_exempt": 0, "blocked": 0, "c_adjustment_removed": 6, "outputs": 6, "outputs_with_adjusted_hits": 0, "outputs_with_production_hits": 6, "production_hits": 6, "production_hits_by_map_status": {"not_observed": 1, "observed": 5}, "rules": {"R10:no_template": 6}, "value_fidelity_errors": 0}

## Every synthetic output

| case | arm | death | map status | production hits | C adj. | A3 R5 exempt | adjusted | rules | map-rule violations | value errors | uptake | ablation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| compliant | M | `001|death_episode:72.500` | not_observed | 0 | 0 | 0 | 0 | - | - | 0 | True | yes |
| compliant | M | `001|death_episode:188.000` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `002|death_episode:46.000` | not_observed | 0 | 0 | 0 | 0 | - | - | 0 | True | yes |
| compliant | M | `002|death_episode:89.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `003|death_episode:48.000` | not_observed | 0 | 0 | 0 | 0 | - | - | 0 | True | yes |
| compliant | M | `003|death_episode:127.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `003|death_episode:197.000` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `004|death_episode:66.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `006|death_episode:224.000` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `006|death_episode:263.000` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `007|death_episode:60.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `007|death_episode:92.000` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `007|death_episode:143.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `007|death_episode:169.000` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `007|death_episode:206.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `007|death_episode:251.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `008|death_episode:26.000` | not_observed | 0 | 0 | 0 | 0 | - | - | 0 | True | yes |
| compliant | M | `008|death_episode:105.000` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `009|death_episode:78.000` | not_observed | 0 | 0 | 0 | 0 | - | - | 0 | True | yes |
| compliant | M | `009|death_episode:191.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `009|death_episode:319.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `010|death_episode:80.500` | not_observed | 0 | 0 | 0 | 0 | - | - | 0 | True | yes |
| compliant | M | `010|death_episode:221.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `010|death_episode:297.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `010|death_episode:321.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `011|death_episode:94.000` | not_observed | 0 | 0 | 0 | 0 | - | - | 0 | True | yes |
| compliant | M | `011|death_episode:132.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `011|death_episode:162.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `012|death_episode:65.500` | not_observed | 0 | 0 | 0 | 0 | - | - | 0 | True | yes |
| compliant | M | `012|death_episode:199.000` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `013|death_episode:112.500` | not_observed | 0 | 0 | 0 | 0 | - | - | 0 | True | yes |
| compliant | M | `014|death_episode:209.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `014|death_episode:235.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `015|death_episode:63.000` | not_observed | 0 | 0 | 0 | 0 | - | - | 0 | True | yes |
| compliant | M | `015|death_episode:116.000` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `017|death_episode:106.000` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `017|death_episode:151.000` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `018|death_episode:219.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `019|death_episode:83.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | M | `019|death_episode:107.500` | observed | 2 | 0 | 2 | 0 | R5:map_absence | - | 0 | True | yes |
| compliant | C | `007|death_episode:143.500` | observed | 1 | 1 | 0 | 0 | R10:no_template | - | 0 | - | - |
| compliant | C | `007|death_episode:206.500` | observed | 1 | 1 | 0 | 0 | R10:no_template | - | 0 | - | - |
| compliant | C | `007|death_episode:251.500` | observed | 1 | 1 | 0 | 0 | R10:no_template | - | 0 | - | - |
| compliant | C | `008|death_episode:105.000` | observed | 1 | 1 | 0 | 0 | R10:no_template | - | 0 | - | - |
| compliant | C | `010|death_episode:80.500` | not_observed | 1 | 1 | 0 | 0 | R10:no_template | - | 0 | - | - |
| compliant | C | `014|death_episode:235.500` | observed | 1 | 1 | 0 | 0 | R10:no_template | - | 0 | - | - |
| causal | M | `synthetic` | not_observed | 1 | 0 | 0 | 1 | causal_map_claim | causal | 0 | True | map_sentence_not_exactly_one_segment, map_in_remaining_segment |
| map_content | M | `synthetic` | not_observed | 0 | 0 | 0 | 0 | - | map_content | 0 | False | map_sentence_not_exactly_one_segment, map_in_remaining_segment |
| wrong_seconds | M | `synthetic` | not_observed | 1 | 0 | 0 | 1 | R7:value_mismatch | - | 3 | False | yes |
| paraphrased_map_sentence | M | `synthetic` | not_observed | 0 | 0 | 0 | 0 | - | - | 0 | True | map_sentence_not_exactly_one_segment, map_in_remaining_segment |
| advice_not_eligible | M | `synthetic` | observed | 1 | 0 | 0 | 1 | R10:ungrounded_template | advice_not_eligible | 0 | False | - |
| a3_unanchored_absence | M | `synthetic` | observed | 2 | 0 | 1 | 1 | R5:map_absence | - | 0 | False | map_sentence_not_exactly_one_segment, map_in_remaining_segment |
| a3_absence_with_alive_check | M | `synthetic` | observed | 1 | 0 | 0 | 1 | R5:map_absence | - | 0 | False | - |
