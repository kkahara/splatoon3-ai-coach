# Experiment H automatic summary (frozen checks; decision rule not applied)

Arm-identified. Automatic checks only; no human rating and no gate
verdict. Text checks read assessment + recommendations.

- Coverage: missing 0, duplicate 0, unexpected 0

| arm | subset | calls | failed | blocked | validator hits (per call) | roster mentions | fidelity errors | fidelity pass / mentions | roster-rule violations |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | all | 44 | 0 | 0 | 4 (0.091) | 0 | 0 | 0 / 0 | 0 |
| A | treatment | 22 | 0 | 0 | 3 (0.136) | 0 | 0 | 0 / 0 | 0 |
| A | control | 22 | 0 | 0 | 1 (0.045) | 0 | 0 | 0 / 0 | 0 |
| A | at_or_before_death | 10 | 0 | 0 | 0 (0.0) | 0 | 0 | 0 / 0 | 0 |
| B | all | 44 | 0 | 0 | 4 (0.091) | 2 | 0 | 2 / 2 | 0 |
| B | treatment | 22 | 0 | 0 | 1 (0.045) | 0 | 0 | 0 / 0 | 0 |
| B | control | 22 | 0 | 0 | 3 (0.136) | 2 | 0 | 2 / 2 | 0 |
| B | at_or_before_death | 10 | 0 | 0 | 0 (0.0) | 0 | 0 | 0 / 0 | 0 |
| C | all | 44 | 0 | 0 | 0 (0.0) | 4 | 0 | 4 / 4 | 0 |
| C | treatment | 22 | 0 | 0 | 0 (0.0) | 2 | 0 | 2 / 2 | 0 |
| C | control | 22 | 0 | 0 | 0 (0.0) | 2 | 0 | 2 / 2 | 0 |
| C | at_or_before_death | 10 | 0 | 0 | 0 (0.0) | 2 | 0 | 2 / 2 | 0 |

B-vs-C divergence (1 - difflib ratio, repeat r with r, mean per death):
treatment 0.1457, control 0.1219, at_or_before_death 0.158, window_only 0.1355.
