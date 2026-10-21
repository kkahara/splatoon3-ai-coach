# N2 citation-compliance experiment: automatic scoring

**Gate: PASS**

| rule | holds |
| --- | --- |
| `all_calls_parsed` | True |
| `zero_unsupported_or_invented` | True |
| `zero_wrong_value` | True |
| `zero_object_path_asserted` | True |
| `zero_legacy_form` | True |
| `zero_malformed_or_unresolved` | True |
| `support_cited` | True |

- Outputs: 24 / 24 compliant (24 parsed); units compliant on every repeat: 12 / 12
- Entries: 77 / 77 compliant; prose before the path 0, several facts 0
- Citations: 77 / 77 n2_scalar_correct
- Value forms: {'exact': 77}; malformed reasons: {}
- Families cited: {'clock': 11, 'death': 36, 'timeline': 12, 'special': 2, 'playback': 5, 'low_ink': 11}
- Selected claims with an evidence path cited: 53 / 53
- Outputs without citations: 0
- Validator status: {'validated': 24}
- Validator vs strict disagreements: 0

## Citation classes

| class | count |
| --- | --- |
| `n2_scalar_correct` | 77 |
| `n2_scalar_wrong_value` | 0 |
| `n2_object_asserted` | 0 |
| `source_path_field` | 0 |
| `legacy_label` | 0 |
| `n1_coachinput` | 0 |
| `relative_path` | 0 |
| `unresolved_path` | 0 |
| `unsupported_invented` | 0 |
| `malformed` | 0 |

## Non-compliant citations and disagreements

