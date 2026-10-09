# SPECIAL_USE held-out gate (zero offset, X = 0)

**Verdict: FAIL** (isolated_timing_failures): implementation unchanged; diagnose the listed cases.

- Pre-registration commit: `8828b4672637f65e4c58aef98cc31710d09bd1f7`
- Gate run commit: `cefeb8032c07d9eb4050f8cece96b052090660ab` (tool/config dirty: False)
- Review config sha256: `98c2bf537caee518ce609812eea509e53fea1e7f4239a72120631e4230325e24`
- Batches: analysis/review_validation/heldout/sync/boundary (71), analysis/review_validation/heldout/sync/boundary_batch2 (30)
- Rechecked cases (8): B07, B08, B09, B39, B46, B58, B66, C30
- Known non-timing failures: {'B38': 'disconnected_slot'}

| | Raw labels | Final (recheck merged) |
|---|---|---|
| labels | {'alive': 35, 'dead': 37, 'indeterminate': 29} | {'alive': 35, 'dead': 37, 'indeterminate': 29} |
| outcomes | {'correct': 46, 'pipeline_unknown': 20, 'wrong': 6, 'indeterminate': 29} | {'correct': 52, 'pipeline_unknown': 20, 'indeterminate': 29} |
| timing_failures | ['B07', 'B08', 'B09', 'B39', 'B46', 'B58', 'B66', 'C30'] | ['B09', 'B39'] |
| non_timing_failures | ['B38'] | ['B38'] |
| x0_unknown_in_bracket | 46 | 46 |
| x0_unknown_out_of_bracket | 0 | 0 |
| directions | {'lag': 4, 'lead': 2, 'none': 2} | {'none': 2} |
| empirical X (all cases) | 0.57 s | 0.28 s |

## Gate conditions

1. Timing failures (merged B + C): 2 ['B09', 'B39'].
2. Timing failures among 52 timing-attributable cases with a determinable label and an X = 0 state: 0 []. Redundant: these failures are a subset of condition 1, so condition 1 implies condition 2; no separate statistic.
   Timing-attributable empirical X: 0.09 s (wrong up to None s, indeterminate up to 0.09 s).

Bias test: 2 timing failures, counts {'lag': 0, 'lead': 0, 'none': 2}; needs >= 3 and >= 80% in one direction; dominant None (0.0); systematic bias: False.

## All cases

| Case | Recording | Slot | Transition | Bracket (s) | Raw | Recheck | Final | Source | X=0 | Outcome | Failure | Direction |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B01 | 21-35-40 | bottom_3 | dead_to_alive | [-1.01, -0.06] | alive |  | alive | raw | alive | correct |  | none |
| B02 | 21-35-40 | top_4 | dead_to_alive | [-0.65, 0.3] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| B03 | 21-35-40 | top_1 | dead_to_alive | [-1.72, -0.77] | alive |  | alive | raw | alive | correct |  | none |
| B04 | 21-35-40 | bottom_1 | dead_to_alive | [-0.77, 0.3] | dead |  | dead | raw | unknown | pipeline_unknown |  | none |
| B05 | 21-35-40 | bottom_4 | dead_to_alive | [0.06, 1.13] | dead |  | dead | raw | dead | correct |  | none |
| B06 | 21-35-40 | bottom_2 | dead_to_alive | [-1.01, 0.06] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| B07 | 21-35-40 | top_1 | alive_to_dead | [0.12, 1.19] | dead | alive | alive | recheck | alive | correct |  | none |
| B08 | 21-35-40 | bottom_2 | dead_to_alive | [0.12, 1.19] | alive | dead | dead | recheck | dead | correct |  | none |
| B09 | 21-35-40 | top_2 | alive_to_dead | [-1.01, -0.06] | alive | indeterminate | indeterminate | recheck | dead | indeterminate | timing | none |
| B10 | 21-35-40 | top_3 | dead_to_alive | [-1.01, 0.06] | dead |  | dead | raw | unknown | pipeline_unknown |  | none |
| B11 | 21-35-40 | top_1 | dead_to_alive | [0.18, 1.13] | dead |  | dead | raw | dead | correct |  | none |
| B12 | 21-35-40 | bottom_3 | dead_to_alive | [-1.48, -0.53] | alive |  | alive | raw | alive | correct |  | none |
| B13 | 21-35-40 | top_2 | alive_to_dead | [0.36, 1.42] | alive |  | alive | raw | alive | correct |  | none |
| B14 | 21-35-40 | bottom_2 | alive_to_dead | [-1.31, -0.36] | dead |  | dead | raw | dead | correct |  | none |
| B15 | 21-35-40 | top_3 | dead_to_alive | [-1.78, -0.83] | alive |  | alive | raw | alive | correct |  | none |
| B16 | 06-43-43 | bottom_2 | dead_to_alive | [-114.81, 54.23] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B17 | 06-43-43 | bottom_2 | dead_to_alive | [-70.23, 98.81] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B18 | 06-43-43 | bottom_2 | dead_to_alive | [-1.99, 0.09] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| B19 | 06-43-43 | top_1 | dead_to_alive | [-1.04, 1.04] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| B20 | 06-43-43 | bottom_2 | alive_to_dead | [0.28, 1.42] | alive |  | alive | raw | alive | correct |  | none |
| B21 | 06-43-43 | top_2 | dead_to_alive | [-1.61, -0.66] | alive |  | alive | raw | alive | correct |  | none |
| B22 | 06-43-43 | bottom_1 | dead_to_alive | [-1.61, -0.66] | alive |  | alive | raw | alive | correct |  | none |
| B23 | 06-43-43 | bottom_2 | dead_to_alive | [-47.42, 121.62] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B24 | 06-43-43 | top_1 | alive_to_dead | [0.28, 1.42] | alive |  | alive | raw | alive | correct |  | none |
| B25 | 06-43-43 | bottom_2 | dead_to_alive | [-106.67, 62.37] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B26 | 06-43-43 | bottom_1 | dead_to_alive | [-0.28, 0.66] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B27 | 06-43-43 | bottom_2 | dead_to_alive | [-136.39, 32.65] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B28 | 06-43-43 | bottom_3 | alive_to_dead | [-1.42, -0.28] | dead |  | dead | raw | dead | correct |  | none |
| B29 | 06-43-43 | top_4 | dead_to_alive | [-0.85, 0.09] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| B30 | 06-43-43 | bottom_2 | dead_to_alive | [-17.7, 151.34] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B31 | 06-43-43 | bottom_2 | dead_to_alive | [-96.07, 72.97] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B32 | 06-43-43 | top_4 | dead_to_alive | [-0.09, 1.04] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| B33 | 06-43-43 | bottom_2 | dead_to_alive | [-153.04, 16.0] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B34 | 06-43-43 | bottom_2 | dead_to_alive | [-33.98, 135.06] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B35 | 06-43-43 | bottom_2 | dead_to_alive | [-124.46, 44.58] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B36 | 06-43-43 | top_1 | dead_to_alive | [0.66, 1.61] | dead |  | dead | raw | dead | correct |  | none |
| B37 | 06-43-43 | bottom_1 | dead_to_alive | [-0.28, 0.66] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| B38 | 06-43-43 | bottom_2 | dead_to_alive | [-169.32, -0.28] | indeterminate |  | indeterminate | raw | alive | indeterminate | non_timing (disconnected_slot) | none |
| B39 | 06-43-43 | top_4 | dead_to_alive | [0.09, 1.04] | alive | indeterminate | indeterminate | recheck | dead | indeterminate | timing | none |
| B40 | 06-43-43 | bottom_2 | dead_to_alive | [-11.07, 157.97] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B41 | 06-43-43 | bottom_2 | dead_to_alive | [-38.9, 130.14] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B42 | 06-43-43 | bottom_2 | dead_to_alive | [-92.47, 76.57] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B43 | 06-43-43 | top_4 | alive_to_dead | [-1.23, -0.09] | dead |  | dead | raw | dead | correct |  | none |
| B44 | 06-43-43 | bottom_1 | alive_to_dead | [-1.23, -0.09] | dead |  | dead | raw | dead | correct |  | none |
| B45 | 06-43-43 | bottom_2 | dead_to_alive | [-147.18, 21.86] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| B46 | 06-43-43 | top_1 | dead_to_alive | [-1.51, -0.57] | dead | alive | alive | recheck | alive | correct |  | none |
| B47 | 06-43-43 | top_2 | dead_to_alive | [-0.19, 0.95] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B48 | 06-43-43 | bottom_2 | dead_to_alive | [-50.16, 118.88] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B49 | 06-43-43 | bottom_2 | dead_to_alive | [-98.05, 70.98] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| B50 | 06-43-43 | top_2 | dead_to_alive | [-1.14, -0.19] | alive |  | alive | raw | alive | correct |  | none |
| B51 | 06-43-43 | bottom_2 | dead_to_alive | [-166.2, 2.84] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B52 | 06-46-06 | bottom_2 | alive_to_dead | [-1.23, -0.28] | dead |  | dead | raw | dead | correct |  | none |
| B53 | 06-46-06 | top_1 | dead_to_alive | [-0.09, 1.04] | dead |  | dead | raw | unknown | pipeline_unknown |  | none |
| B54 | 06-46-06 | top_2 | dead_to_alive | [0.66, 1.61] | dead |  | dead | raw | dead | correct |  | none |
| B55 | 06-46-06 | top_4 | alive_to_dead | [-1.23, -0.09] | dead |  | dead | raw | dead | correct |  | none |
| B56 | 06-46-06 | top_1 | dead_to_alive | [-1.23, -0.28] | alive |  | alive | raw | alive | correct |  | none |
| B57 | 06-46-06 | bottom_4 | dead_to_alive | [-1.23, -0.28] | alive |  | alive | raw | alive | correct |  | none |
| B58 | 06-46-06 | bottom_2 | alive_to_dead | [0.47, 1.42] | dead | alive | alive | recheck | alive | correct |  | none |
| B59 | 06-46-06 | top_1 | alive_to_dead | [-1.42, -0.47] | dead |  | dead | raw | dead | correct |  | none |
| B60 | 06-46-06 | top_4 | dead_to_alive | [0.09, 1.04] | dead |  | dead | raw | dead | correct |  | none |
| B61 | 06-46-06 | top_1 | dead_to_alive | [-0.95, 0.0] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| B62 | 06-46-06 | top_1 | alive_to_dead | [0.19, 1.14] | alive |  | alive | raw | alive | correct |  | none |
| B63 | 06-46-06 | top_4 | dead_to_alive | [-0.85, 0.09] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| B64 | 06-46-06 | top_1 | dead_to_alive | [-0.85, 0.28] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B65 | 06-46-06 | bottom_3 | dead_to_alive | [-0.85, 0.28] | dead |  | dead | raw | unknown | pipeline_unknown |  | none |
| B66 | 06-52-33 | top_1 | alive_to_dead | [-1.48, -0.47] | indeterminate | dead | dead | recheck | dead | correct |  | none |
| B67 | 06-52-33 | bottom_1 | dead_to_alive | [-0.59, 0.36] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| B68 | 06-52-33 | bottom_2 | dead_to_alive | [-0.59, 0.36] | dead |  | dead | raw | unknown | pipeline_unknown |  | none |
| B69 | 06-52-33 | bottom_4 | alive_to_dead | [-1.22, -0.21] | dead |  | dead | raw | dead | correct |  | none |
| B70 | 06-52-33 | top_3 | alive_to_dead | [-1.04, -0.03] | dead |  | dead | raw | dead | correct |  | none |
| B71 | 06-52-33 | bottom_1 | alive_to_dead | [-1.04, -0.03] | dead |  | dead | raw | dead | correct |  | none |
| C01 | 07-45-59 | bottom_3 | dead_to_alive | [0.74, 1.77] | dead |  | dead | raw | dead | correct |  | none |
| C02 | 07-45-59 | bottom_4 | dead_to_alive | [-1.71, -0.69] | alive |  | alive | raw | alive | correct |  | none |
| C03 | 07-45-59 | top_3 | dead_to_alive | [0.63, 1.66] | dead |  | dead | raw | dead | correct |  | none |
| C04 | 07-45-59 | bottom_2 | dead_to_alive | [0.63, 1.66] | dead |  | dead | raw | dead | correct |  | none |
| C05 | 07-45-59 | top_3 | alive_to_dead | [-1.09, -0.06] | dead |  | dead | raw | dead | correct |  | none |
| C06 | 07-45-59 | bottom_4 | dead_to_alive | [-20.06, 7.95] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| C07 | 07-45-59 | bottom_4 | dead_to_alive | [-4.63, 23.38] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| C08 | 07-45-59 | bottom_1 | dead_to_alive | [0.06, 1.09] | dead |  | dead | raw | dead | correct |  | none |
| C09 | 07-54-48 | top_2 | dead_to_alive | [-3.03, 2.84] | dead |  | dead | raw | unknown | pipeline_unknown |  | none |
| C10 | 07-54-48 | bottom_4 | dead_to_alive | [0.95, 2.84] | dead |  | dead | raw | dead | correct |  | none |
| C11 | 07-54-48 | bottom_2 | alive_to_dead | [-1.14, -0.19] | dead |  | dead | raw | dead | correct |  | none |
| C12 | 07-54-48 | top_2 | dead_to_alive | [-0.47, 0.66] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| C13 | 07-54-48 | bottom_1 | dead_to_alive | [0.47, 1.42] | dead |  | dead | raw | dead | correct |  | none |
| C14 | 07-54-48 | top_3 | alive_to_dead | [-1.42, -0.47] | dead |  | dead | raw | dead | correct |  | none |
| C15 | 07-54-48 | top_4 | dead_to_alive | [-0.47, 1.61] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| C16 | 07-54-48 | top_2 | dead_to_alive | [-3.41, 1.7] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| C17 | 07-54-48 | bottom_1 | dead_to_alive | [0.57, 1.51] | dead |  | dead | raw | dead | correct |  | none |
| C18 | 07-54-48 | top_2 | dead_to_alive | [-0.38, 0.57] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| C19 | 07-54-48 | top_3 | dead_to_alive | [-1.42, -0.47] | alive |  | alive | raw | alive | correct |  | none |
| C20 | 07-54-48 | bottom_1 | dead_to_alive | [-0.09, 2.93] | dead |  | dead | raw | unknown | pipeline_unknown |  | none |
| C21 | 07-54-48 | bottom_3 | alive_to_dead | [-1.04, -0.09] | dead |  | dead | raw | dead | correct |  | none |
| C22 | 07-54-48 | bottom_3 | dead_to_alive | [-0.66, 0.28] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| C23 | 07-54-48 | top_3 | alive_to_dead | [0.28, 1.23] | alive |  | alive | raw | alive | correct |  | none |
| C24 | 07-54-48 | bottom_1 | dead_to_alive | [-1.8, -0.85] | alive |  | alive | raw | alive | correct |  | none |
| C25 | 07-54-48 | bottom_2 | alive_to_dead | [0.28, 1.23] | alive |  | alive | raw | alive | correct |  | none |
| C26 | 07-54-48 | bottom_1 | dead_to_alive | [0.28, 1.42] | dead |  | dead | raw | dead | correct |  | none |
| C27 | 07-54-48 | bottom_1 | dead_to_alive | [-0.66, 0.28] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| C28 | 07-54-48 | top_2 | dead_to_alive | [-5.02, 0.09] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| C29 | 07-54-48 | bottom_1 | dead_to_alive | [-1.04, -0.09] | alive |  | alive | raw | alive | correct |  | none |
| C30 | 07-54-48 | bottom_1 | alive_to_dead | [-1.14, -0.19] | indeterminate | dead | dead | recheck | dead | correct |  | none |
