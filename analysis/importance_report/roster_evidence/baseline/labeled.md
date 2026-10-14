# Gameplay HUD roster hand-labeled baseline

- Labels sha256: `cd10f3b094ff9b7298c5c591d1cb9a8f3b31234ab0cb6573bf28bda9c60a847b`
- `?` is human ambiguity, not an incorrect label.

## Overall

- Samples: 120
- Human-determinable slots: 751 / 960 (78.2%)
- Detector X / alive on ambiguous slots: 4 / 45
- Slot X precision / recall: 96.9% / 87.9%
- Exact side count: 88.8% (166/187)
- Exact raw AvB: 77.4% (72/93)
- Exact fused AvB: 62.0% (57/92)
- Exact pre-death fused AvB: 71.4% (15/21)
- Visibility precision / recall: 94.0% / 100.0%

## Source: live_gameplay

- Samples: 97
- Human-determinable slots: 607 / 776 (78.2%)
- Detector X / alive on ambiguous slots: 4 / 37
- Slot X precision / recall: 96.0% / 91.5%
- Exact side count: 91.4% (138/151)
- Exact raw AvB: 82.7% (62/75)
- Exact fused AvB: 74.3% (55/74)
- Exact pre-death fused AvB: 82.4% (14/17)
- Visibility precision / recall: 93.8% / 100.0%

## Source: replay_derived

- Samples: 23
- Human-determinable slots: 144 / 184 (78.3%)
- Detector X / alive on ambiguous slots: 0 / 8
- Slot X precision / recall: 100.0% / 77.1%
- Exact side count: 77.8% (28/36)
- Exact raw AvB: 55.6% (10/18)
- Exact fused AvB: 11.1% (2/18)
- Exact pre-death fused AvB: 25.0% (1/4)
- Visibility precision / recall: 94.7% / 100.0%

## Stratum: margin

- Samples: 20
- Human-determinable slots: 136 / 160 (85.0%)
- Detector X / alive on ambiguous slots: 2 / 22
- Slot X precision / recall: 92.3% / 85.7%
- Exact side count: 82.4% (28/34)
- Exact raw AvB: 64.7% (11/17)
- Exact fused AvB: 50.0% (8/16)
- Exact pre-death fused AvB: n/a (0/0)
- Visibility precision / recall: 85.0% / 100.0%

## Stratum: flicker

- Samples: 20
- Human-determinable slots: 143 / 160 (89.4%)
- Detector X / alive on ambiguous slots: 2 / 15
- Slot X precision / recall: 92.0% / 92.0%
- Exact side count: 88.6% (31/35)
- Exact raw AvB: 76.5% (13/17)
- Exact fused AvB: 35.3% (6/17)
- Exact pre-death fused AvB: 100.0% (1/1)
- Visibility precision / recall: 90.0% / 100.0%

## Stratum: pre_death

- Samples: 20
- Human-determinable slots: 160 / 160 (100.0%)
- Detector X / alive on ambiguous slots: 0 / 0
- Slot X precision / recall: 100.0% / 85.7%
- Exact side count: 90.0% (36/40)
- Exact raw AvB: 80.0% (16/20)
- Exact fused AvB: 70.0% (14/20)
- Exact pre-death fused AvB: 70.0% (14/20)
- Visibility precision / recall: 100.0% / 100.0%

## Stratum: fused_change

- Samples: 20
- Human-determinable slots: 152 / 160 (95.0%)
- Detector X / alive on ambiguous slots: 0 / 8
- Slot X precision / recall: 100.0% / 90.6%
- Exact side count: 92.1% (35/38)
- Exact raw AvB: 84.2% (16/19)
- Exact fused AvB: 68.4% (13/19)
- Exact pre-death fused AvB: n/a (0/0)
- Visibility precision / recall: 95.0% / 100.0%

## Stratum: steady

- Samples: 20
- Human-determinable slots: 160 / 160 (100.0%)
- Detector X / alive on ambiguous slots: 0 / 0
- Slot X precision / recall: 100.0% / 85.7%
- Exact side count: 90.0% (36/40)
- Exact raw AvB: 80.0% (16/20)
- Exact fused AvB: 80.0% (16/20)
- Exact pre-death fused AvB: n/a (0/0)
- Visibility precision / recall: 100.0% / 100.0%

## Stratum: hud_hidden

- Samples: 20
- Human-determinable slots: 0 / 160 (0.0%)
- Detector X / alive on ambiguous slots: 0 / 0
- Slot X precision / recall: n/a / n/a
- Exact side count: n/a (0/0)
- Exact raw AvB: n/a (0/0)
- Exact fused AvB: n/a (0/0)
- Exact pre-death fused AvB: n/a (0/0)
- Visibility precision / recall: n/a / n/a
