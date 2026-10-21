# Experiment J automatic summary (frozen checks; decision rule not applied)

Arm-identified: read after the absolute ratings are sealed. Sealed stop
point 3 `output_checks` and `ablate`, each against the view its own sealed
prompt carried. Production counts, the C adjustment and the Amendment 3
R5 window exemption are separate columns. Uptake and ablatability count
unparsed outputs as not.

| group | calls | parsed | blocked | production hits (per call) | C adj. | R5 exempt | adjusted hits (per call) | any rec. | fidelity errors | map-rule violations | uptake | ablatable / applicable | ablated with rec. |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P1 A | 70 | 70 | 0 | 1 (0.014) | 0 | 0 | 1 (0.014) | 8 | 0 | 0 | 0 | 0 / 0 | 0 |
| P1 M | 70 | 70 | 0 | 101 (1.443) | 0 | 101 | 0 (0.0) | 70 | 0 | 0 | 70 | 66 / 70 | 0 |
| P2 A | 10 | 10 | 0 | 0 (0.0) | 0 | 0 | 0 (0.0) | 0 | 0 | 0 | 0 | 0 / 0 | 0 |
| P2 M | 10 | 10 | 0 | 17 (1.7) | 0 | 16 | 1 (0.1) | 10 | 0 | 0 | 10 | 10 / 10 | 0 |
| P3 M | 40 | 40 | 0 | 0 (0.0) | 0 | 0 | 0 (0.0) | 12 | 0 | 0 | 0 | 0 / 0 | 0 |
| C C | 12 | 12 | 0 | 12 (1.0) | 12 | 0 | 0 (0.0) | 12 | 0 | 0 | 0 | 0 / 0 | 0 |
| all A | 80 | 80 | 0 | 1 (0.013) | 0 | 0 | 1 (0.013) | 8 | 0 | 0 | 0 | 0 / 0 | 0 |
| all M | 120 | 120 | 0 | 118 (0.983) | 0 | 117 | 1 (0.008) | 92 | 0 | 0 | 80 | 76 / 80 | 0 |
| all C | 12 | 12 | 0 | 12 (1.0) | 12 | 0 | 0 (0.0) | 12 | 0 | 0 | 0 | 0 / 0 | 0 |

Ablation refusal reasons by group:

- P1 M: map_claim_not_exactly_one 3, map_in_remaining_segment 3, no_other_segment 1
- P2 M: none
- all M: map_claim_not_exactly_one 3, map_in_remaining_segment 3, no_other_segment 1

Not built:

- `c_intended_point_uptake`: the sealed tool freezes uptake for arm M only; C's intended-point uptake has no frozen definition and is not computed here
