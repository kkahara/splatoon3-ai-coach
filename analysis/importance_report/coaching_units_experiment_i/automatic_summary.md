# Experiment I automatic summary (frozen checks; decision rule not applied)

Arm-identified. Automatic checks only; no human rating and no gate
verdict. Text checks read assessment + recommendations + claim sentences. Production and
amendment-1 adjusted validator counts are separate columns. Arm A has
no map_awareness block: its map checks run against a null block and are
descriptive only (the map-rule and fidelity gates apply to arm M).

- Coverage: missing 0, duplicate 0, unexpected 0
- Eligible arm-M outputs mentioning the map check: 28 of 28 (pre-registered denominator 28)

| arm | subset | calls | failed | blocked | production hits (per call) | R5 window exempt | adjusted hits (per call) | map mentions | outputs with fidelity errors | map-rule violations |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | all | 44 | 0 | 0 | 1 (0.023) | 0 | 1 (0.023) | 0 | 0 | 0 |
| A | eligible | 28 | 0 | 0 | 1 (0.036) | 0 | 1 (0.036) | 0 | 0 | 0 |
| A | not_eligible | 16 | 0 | 0 | 0 (0.0) | 0 | 0 (0.0) | 0 | 0 | 0 |
| A | null_block | 2 | 0 | 0 | 0 (0.0) | 0 | 0 (0.0) | 0 | 0 | 0 |
| M | all | 44 | 0 | 0 | 83 (1.886) | 51 | 32 (0.727) | 28 | 0 | 0 |
| M | eligible | 28 | 0 | 0 | 81 (2.893) | 51 | 30 (1.071) | 28 | 0 | 0 |
| M | not_eligible | 16 | 0 | 0 | 2 (0.125) | 0 | 2 (0.125) | 0 | 0 | 0 |
| M | null_block | 2 | 0 | 0 | 0 (0.0) | 0 | 0 (0.0) | 0 | 0 | 0 |
