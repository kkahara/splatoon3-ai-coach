# Experiment I report: map-check coaching

Decision rule applied exactly as pre-registered in `DESIGN.md` with
amendments 1-2 (thresholds and noise floor unchanged). Directional only:
one rater, small n. Any production change is a separate decision.

Ratings: 88 rows sealed at `c20b7cb` (sha256 `b5db14eec2fdfe754c93a4e9019b9df79c317f3ebdce1bf7324a0f00d5d29616`) before the key was opened. Key sha256 `d368951c90c2467eae774aeccc149155e147cf231ace4e324b279d68e0316eca`. Unblinded 2026-10-20T13:20:18+00:00; pre-unblinding gate and join checks passed (`analysis.json`).

## Outcome: Fails 1 or 2: production unchanged; investigate.

## Pre-registered decision rule

### 1. Safety gate (arm M, all 44 calls): **fail**

- Map-rule violations M 0 (must be 0): pass
- Value-fidelity errors M 0 (must be 0): pass
- Blocked outputs M 0 vs A 0: pass
- Validator hits per call, amendment-1 adjusted (the gate): M 0.7273 (32 / 44) vs A 0.0227 + 0.25: fail
- Production count (reported, not the gate): M 1.8864 (83 / 44) vs A 0.0227 + 0.25: fail

### 2. Uptake: **pass**

- Eligible arm-M outputs mentioning the map check: 28 of 28 (required >= 14 of 28). Uptake is not success by itself.

### 3. Value: **pass**

- Eligible deaths (14): mean usefulness M - A 1.8571 (M 4.0, A 2.1429) vs floor 0.1364 (must exceed): pass
- Supported rate, all 22 deaths: M 1.0 vs A 1.0 (M >= A): pass
- All 22 deaths: mean usefulness M - A 1.1818 (M 3.4091, A 2.2273) vs -0.1364 (must be >=): pass

## Automatic-validator anomaly (recorded, not corrected)

Unresolved evidence paths by arm and path family: A other 1, M other 4, M primary_context.map_awareness.* 28. The model cited the arm-M block as `primary_context.map_awareness.*`; the block is top level in the view, so the production validator reports `unknown_block`. These hits are part of both the production and the amendment-1 adjusted counts as frozen; the validator was not changed or re-run.

## Descriptive (not gates)

Rating means (higher missing context = more missing):

| arm | subset | n | usefulness | redundancy | anchor | missing context | supported | map_use |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | all | 44 | 2.2273 | 2.1364 | 2.5909 | 4 | 1.0 | not_mentioned 44 |
| A | eligible | 28 | 2.1429 | 2 | 2.4286 | 4 | 1.0 | not_mentioned 28 |
| A | not_eligible | 16 | 2.375 | 2.375 | 2.875 | 4 | 1.0 | not_mentioned 16 |
| A | null_block | 2 | 3 | 3 | 4 | 4 | 1.0 | not_mentioned 2 |
| M | all | 44 | 3.4091 | 2.1364 | 3.5909 | 3.3636 | 1.0 | correct 28, not_mentioned 16 |
| M | eligible | 28 | 4 | 2 | 4 | 3 | 1.0 | correct 28 |
| M | not_eligible | 16 | 2.375 | 2.375 | 2.875 | 4 | 1.0 | not_mentioned 16 |
| M | null_block | 2 | 3 | 3 | 4 | 4 | 1.0 | not_mentioned 2 |

Repeat consistency (not the frozen floor; the floor is Experiment H's measured B-vs-C value): mean over deaths of |usefulness r1 - r2|: A 0 (22 of 22 deaths identical); M 0 (22 of 22 deaths identical)

| group | deaths | usefulness A / M | map_use=correct A / M |
| --- | --- | --- | --- |
| eligible, back in play 10-30 s | 9 | 2.222 / 4 | 0 / 18 |
| eligible, back in play over 30 s | 5 | 2 / 4 | 0 / 10 |
| production selected=False | 16 | 2.062 / 3.5 | 0 / 24 |
| production selected=True | 6 | 2.667 / 3.167 | 0 / 4 |

Production selection is Experiment H's `A_selected` (roster off).

| match | death | eligible | usefulness A / M | supported A / M |
| --- | --- | --- | --- | --- |
| 2026-10-11 18-18-00 | `death_episode:197.500` | Y | 2.0 / 4.0 | 1.0 / 1.0 |
| 2026-10-11 18-25-16 | `death_episode:175.500` | N | 2.0 / 2.0 | 1.0 / 1.0 |
| 2026-10-11 18-25-16 | `death_episode:85.000` | N | 2.0 / 2.0 | 1.0 / 1.0 |
| 2026-10-11 20-33-31 | `death_episode:151.000` | Y | 2.0 / 4.0 | 1.0 / 1.0 |
| 2026-10-11 20-33-31 | `death_episode:180.500` | N | 2.0 / 2.0 | 1.0 / 1.0 |
| 2026-10-11 20-33-31 | `death_episode:199.500` | null block | 3.0 / 3.0 | 1.0 / 1.0 |
| 2026-10-11 20-33-31 | `death_episode:37.500` | Y | 2.0 / 4.0 | 1.0 / 1.0 |
| 2026-10-11 22-20-42 | `death_episode:152.000` | N | 3.0 / 3.0 | 1.0 / 1.0 |
| 2026-10-11 22-31-53 | `death_episode:208.500` | Y | 3.0 / 4.0 | 1.0 / 1.0 |
| 2026-10-11 22-37-47 | `death_episode:210.500` | N | 2.0 / 2.0 | 1.0 / 1.0 |
| 2026-10-11 22-37-47 | `death_episode:301.500` | Y | 2.0 / 4.0 | 1.0 / 1.0 |
| 2026-10-11 22-37-47 | `death_episode:58.000` | Y | 3.0 / 4.0 | 1.0 / 1.0 |
| 2026-10-11 22-57-30 | `death_episode:106.500` | Y | 2.0 / 4.0 | 1.0 / 1.0 |
| 2026-10-11 22-57-30 | `death_episode:176.500` | Y | 2.0 / 4.0 | 1.0 / 1.0 |
| 2026-10-11 22-57-30 | `death_episode:244.000` | Y | 2.0 / 4.0 | 1.0 / 1.0 |
| 2026-10-11 22-57-30 | `death_episode:278.500` | Y | 2.0 / 4.0 | 1.0 / 1.0 |
| 2026-10-11 22-57-30 | `death_episode:308.000` | Y | 2.0 / 4.0 | 1.0 / 1.0 |
| 2026-10-11 22-57-30 | `death_episode:49.500` | N | 2.0 / 2.0 | 1.0 / 1.0 |
| 2026-10-12 20-17-32 | `death_episode:179.500` | Y | 2.0 / 4.0 | 1.0 / 1.0 |
| 2026-10-12 20-17-32 | `death_episode:221.000` | Y | 2.0 / 4.0 | 1.0 / 1.0 |
| 2026-10-12 20-17-32 | `death_episode:257.500` | Y | 2.0 / 4.0 | 1.0 / 1.0 |
| 2026-10-12 20-17-32 | `death_episode:65.500` | N | 3.0 / 3.0 | 1.0 / 1.0 |
