# SPECIAL_USE held-out gate (zero offset, X = 0)

**Verdict: PASS** (no_timing_failures): freeze the validated timing configuration.

- Pre-registration commit: `878696e22e8bda15b41e9ad3f3bfcbf65b26949d`
- Gate run commit: `6d9e926e77bb2aadc40ccba244296f4765bb5fb6` (tool/config dirty: False)
- Review config sha256: `98c2bf537caee518ce609812eea509e53fea1e7f4239a72120631e4230325e24`
- Batches: analysis/review_validation/heldout2/boundary (108)
- Rechecked cases (0): 
- Known non-timing failures: {}

| | Raw labels | Final (recheck merged) |
|---|---|---|
| labels | {'alive': 49, 'indeterminate': 23, 'dead': 36} | {'alive': 49, 'indeterminate': 23, 'dead': 36} |
| outcomes | {'correct': 77, 'pipeline_unknown': 8, 'indeterminate': 23} | {'correct': 77, 'pipeline_unknown': 8, 'indeterminate': 23} |
| timing_failures | [] | [] |
| non_timing_failures | [] | [] |
| x0_unknown_in_bracket | 31 | 31 |
| x0_unknown_out_of_bracket | 0 | 0 |
| directions | {} | {} |
| empirical X (all cases) | 0.0 s | 0.0 s |

## Gate conditions

1. Timing failures (merged B + C): 0 [].
2. Timing failures among 77 timing-attributable cases with a determinable label and an X = 0 state: 0 []. Redundant: these failures are a subset of condition 1, so condition 1 implies condition 2; no separate statistic.
   Timing-attributable empirical X: 0.0 s (wrong up to None s, indeterminate up to 0.0 s).

Bias test: 0 timing failures, counts {'lag': 0, 'lead': 0, 'none': 0}; needs >= 3 and >= 80% in one direction; dominant None (None); systematic bias: False.

## All cases

| Case | Recording | Slot | Transition | Bracket (s) | Raw | Recheck | Final | Source | X=0 | Outcome | Failure | Direction |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| D01 | 09-53-27 | bottom_4 | dead_to_alive | [-1.5, -0.43] | alive |  | alive | raw | alive | correct |  | none |
| D02 | 09-53-27 | bottom_1 | alive_to_dead | [0.21, 1.07] | alive |  | alive | raw | alive | correct |  | none |
| D03 | 09-53-27 | bottom_2 | alive_to_dead | [0.21, 1.07] | alive |  | alive | raw | alive | correct |  | none |
| D04 | 09-53-27 | top_2 | dead_to_alive | [-0.85, 0.0] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| D05 | 09-53-27 | top_3 | dead_to_alive | [-0.85, 0.0] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| D06 | 09-53-27 | top_3 | dead_to_alive | [-1.28, -0.21] | alive |  | alive | raw | alive | correct |  | none |
| D07 | 09-53-27 | top_2 | dead_to_alive | [-0.64, 0.43] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D08 | 09-53-27 | top_1 | dead_to_alive | [-0.64, 0.21] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D09 | 09-53-27 | bottom_4 | dead_to_alive | [0.21, 1.28] | dead |  | dead | raw | dead | correct |  | none |
| D10 | 09-53-27 | bottom_2 | dead_to_alive | [0.21, 1.28] | dead |  | dead | raw | dead | correct |  | none |
| D11 | 09-53-27 | top_4 | dead_to_alive | [0.96, 3.1] | dead |  | dead | raw | dead | correct |  | none |
| D12 | 09-53-27 | bottom_1 | dead_to_alive | [0.96, 3.1] | dead |  | dead | raw | dead | correct |  | none |
| D13 | 09-53-27 | top_3 | alive_to_dead | [0.21, 1.28] | alive |  | alive | raw | alive | correct |  | none |
| D14 | 09-53-27 | top_4 | dead_to_alive | [-0.11, 0.75] | dead |  | dead | raw | unknown | pipeline_unknown |  | none |
| D15 | 09-53-27 | top_4 | alive_to_dead | [-1.18, -0.11] | dead |  | dead | raw | dead | correct |  | none |
| D16 | 09-53-27 | top_1 | dead_to_alive | [-1.82, 0.32] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D17 | 09-53-27 | bottom_1 | dead_to_alive | [-1.18, -0.11] | alive |  | alive | raw | alive | correct |  | none |
| D18 | 09-53-27 | bottom_2 | dead_to_alive | [-1.6, -0.75] | alive |  | alive | raw | alive | correct |  | none |
| D19 | 09-53-27 | bottom_2 | dead_to_alive | [-0.32, 0.53] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D20 | 09-53-27 | bottom_3 | dead_to_alive | [-1.39, -0.32] | alive |  | alive | raw | alive | correct |  | none |
| D21 | 09-53-27 | bottom_4 | dead_to_alive | [-0.32, 0.53] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D22 | 09-54-34 | bottom_4 | dead_to_alive | [0.69, 1.67] | dead |  | dead | raw | dead | correct |  | none |
| D23 | 09-54-34 | top_2 | dead_to_alive | [0.49, 1.47] | dead |  | dead | raw | dead | correct |  | none |
| D24 | 09-54-34 | top_4 | dead_to_alive | [-1.47, -0.49] | alive |  | alive | raw | alive | correct |  | none |
| D25 | 09-54-34 | bottom_3 | dead_to_alive | [-0.49, 0.49] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D26 | 09-54-34 | top_4 | dead_to_alive | [0.1, 1.08] | dead |  | dead | raw | dead | correct |  | none |
| D27 | 09-54-34 | top_2 | alive_to_dead | [0.49, 1.47] | alive |  | alive | raw | alive | correct |  | none |
| D28 | 09-54-34 | top_2 | alive_to_dead | [0.2, 1.18] | alive |  | alive | raw | alive | correct |  | none |
| D29 | 09-54-34 | top_3 | dead_to_alive | [-1.77, 0.2] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| D30 | 09-54-34 | bottom_2 | alive_to_dead | [0.2, 1.18] | alive |  | alive | raw | alive | correct |  | none |
| D31 | 09-54-34 | top_3 | dead_to_alive | [0.78, 1.77] | dead |  | dead | raw | dead | correct |  | none |
| D32 | 09-54-34 | top_1 | dead_to_alive | [0.78, 1.77] | dead |  | dead | raw | dead | correct |  | none |
| D33 | 09-54-34 | bottom_3 | dead_to_alive | [-0.2, 0.78] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D34 | 09-54-34 | top_3 | dead_to_alive | [-0.98, 0.98] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| D35 | 09-54-34 | top_3 | dead_to_alive | [0.29, 1.28] | dead |  | dead | raw | dead | correct |  | none |
| D36 | 09-54-34 | bottom_2 | dead_to_alive | [0.29, 1.28] | dead |  | dead | raw | dead | correct |  | none |
| D37 | 09-54-34 | top_1 | dead_to_alive | [-0.29, 0.69] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D38 | 09-54-34 | top_3 | dead_to_alive | [-1.47, -0.29] | alive |  | alive | raw | alive | correct |  | none |
| D39 | 09-54-34 | bottom_1 | alive_to_dead | [-1.47, -0.29] | dead |  | dead | raw | dead | correct |  | none |
| D40 | 09-54-34 | bottom_3 | dead_to_alive | [-0.69, 0.29] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D41 | 09-54-34 | bottom_4 | alive_to_dead | [0.29, 1.28] | alive |  | alive | raw | alive | correct |  | none |
| D42 | 09-54-34 | top_2 | dead_to_alive | [0.1, 1.08] | dead |  | dead | raw | dead | correct |  | none |
| D43 | 09-54-34 | top_2 | dead_to_alive | [0.1, 2.06] | dead |  | dead | raw | dead | correct |  | none |
| D44 | 09-54-34 | top_3 | alive_to_dead | [0.1, 2.06] | alive |  | alive | raw | alive | correct |  | none |
| D45 | 09-54-34 | bottom_1 | alive_to_dead | [0.1, 2.06] | alive |  | alive | raw | alive | correct |  | none |
| D46 | 09-54-34 | bottom_3 | dead_to_alive | [0.1, 2.06] | dead |  | dead | raw | dead | correct |  | none |
| D47 | 09-54-34 | top_2 | dead_to_alive | [-0.29, 0.69] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D48 | 09-54-34 | bottom_3 | dead_to_alive | [-1.28, -0.29] | alive |  | alive | raw | alive | correct |  | none |
| D49 | 09-54-34 | top_2 | alive_to_dead | [0.1, 1.08] | alive |  | alive | raw | alive | correct |  | none |
| D50 | 09-54-34 | top_4 | alive_to_dead | [0.39, 1.37] | alive |  | alive | raw | alive | correct |  | none |
| D51 | 09-54-34 | bottom_3 | dead_to_alive | [-0.39, 0.59] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D52 | 09-55-33 | top_3 | alive_to_dead | [-1.39, -0.37] | dead |  | dead | raw | dead | correct |  | none |
| D53 | 09-55-33 | bottom_4 | dead_to_alive | [-0.37, 0.51] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D54 | 09-55-33 | bottom_4 | dead_to_alive | [-2.19, 0.73] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D55 | 09-55-33 | bottom_2 | dead_to_alive | [-0.51, 0.51] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D56 | 09-55-33 | bottom_4 | dead_to_alive | [0.88, 1.9] | dead |  | dead | raw | dead | correct |  | none |
| D57 | 09-55-33 | bottom_4 | dead_to_alive | [-2.49, 0.44] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D58 | 09-55-33 | top_2 | dead_to_alive | [0.66, 1.53] | dead |  | dead | raw | dead | correct |  | none |
| D59 | 09-55-33 | top_3 | dead_to_alive | [0.66, 1.53] | dead |  | dead | raw | dead | correct |  | none |
| D60 | 09-55-33 | bottom_4 | dead_to_alive | [-1.39, -0.37] | alive |  | alive | raw | alive | correct |  | none |
| D61 | 09-56-35 | top_2 | dead_to_alive | [-1.61, -0.66] | alive |  | alive | raw | alive | correct |  | none |
| D62 | 09-56-35 | bottom_2 | dead_to_alive | [0.47, 1.42] | dead |  | dead | raw | dead | correct |  | none |
| D63 | 09-56-35 | top_1 | alive_to_dead | [0.47, 1.61] | alive |  | alive | raw | alive | correct |  | none |
| D64 | 09-56-35 | bottom_3 | alive_to_dead | [-1.42, -0.47] | dead |  | dead | raw | dead | correct |  | none |
| D65 | 09-56-35 | top_2 | alive_to_dead | [0.09, 1.04] | alive |  | alive | raw | alive | correct |  | none |
| D66 | 09-56-35 | top_2 | dead_to_alive | [-0.28, 0.66] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D67 | 09-56-35 | bottom_1 | dead_to_alive | [-1.23, -0.28] | alive |  | alive | raw | alive | correct |  | none |
| D68 | 09-56-35 | top_1 | dead_to_alive | [-0.28, 0.66] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D69 | 09-56-35 | bottom_2 | dead_to_alive | [-1.8, -0.85] | alive |  | alive | raw | alive | correct |  | none |
| D70 | 09-56-35 | bottom_4 | alive_to_dead | [0.47, 1.42] | alive |  | alive | raw | alive | correct |  | none |
| D71 | 09-56-35 | top_4 | dead_to_alive | [-0.28, 0.66] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D72 | 09-56-35 | bottom_3 | dead_to_alive | [-0.28, 0.66] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D73 | 09-56-35 | top_4 | dead_to_alive | [-0.95, 0.0] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| D74 | 09-56-35 | bottom_1 | dead_to_alive | [0.66, 1.61] | dead |  | dead | raw | dead | correct |  | none |
| D75 | 09-56-35 | bottom_4 | dead_to_alive | [-1.42, -0.47] | alive |  | alive | raw | alive | correct |  | none |
| D76 | 09-56-35 | bottom_3 | dead_to_alive | [-0.85, 1.23] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D77 | 09-56-35 | top_2 | dead_to_alive | [-0.66, 0.47] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D78 | 09-56-35 | top_1 | dead_to_alive | [0.66, 1.8] | dead |  | dead | raw | dead | correct |  | none |
| D79 | 09-57-43 | top_1 | dead_to_alive | [-1.7, -0.69] | alive |  | alive | raw | alive | correct |  | none |
| D80 | 09-57-43 | top_2 | alive_to_dead | [0.31, 2.32] | alive |  | alive | raw | alive | correct |  | none |
| D81 | 09-57-43 | bottom_3 | dead_to_alive | [-1.38, -0.38] | alive |  | alive | raw | alive | correct |  | none |
| D82 | 09-57-43 | top_3 | dead_to_alive | [-1.76, -0.75] | alive |  | alive | raw | alive | correct |  | none |
| D83 | 09-57-43 | top_4 | dead_to_alive | [-1.76, -0.75] | alive |  | alive | raw | alive | correct |  | none |
| D84 | 09-57-43 | bottom_4 | dead_to_alive | [0.57, 1.57] | dead |  | dead | raw | dead | correct |  | none |
| D85 | 09-57-43 | bottom_3 | dead_to_alive | [-1.38, -0.38] | alive |  | alive | raw | alive | correct |  | none |
| D86 | 09-57-43 | bottom_4 | dead_to_alive | [-0.19, 0.82] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D87 | 09-57-43 | top_3 | dead_to_alive | [-1.82, -0.82] | alive |  | alive | raw | alive | correct |  | none |
| D88 | 09-57-43 | top_4 | alive_to_dead | [0.19, 1.19] | alive |  | alive | raw | alive | correct |  | none |
| D89 | 09-59-27 | top_3 | dead_to_alive | [0.66, 1.61] | dead |  | dead | raw | dead | correct |  | none |
| D90 | 09-59-27 | top_4 | alive_to_dead | [0.47, 1.42] | alive |  | alive | raw | alive | correct |  | none |
| D91 | 09-59-27 | top_2 | dead_to_alive | [0.19, 1.14] | dead |  | dead | raw | dead | correct |  | none |
| D92 | 09-59-27 | bottom_2 | alive_to_dead | [0.19, 1.14] | alive |  | alive | raw | alive | correct |  | none |
| D93 | 09-59-27 | top_4 | dead_to_alive | [-2.65, -0.76] | alive |  | alive | raw | alive | correct |  | none |
| D94 | 09-59-27 | bottom_1 | alive_to_dead | [-1.51, -0.38] | dead |  | dead | raw | dead | correct |  | none |
| D95 | 09-59-27 | bottom_2 | dead_to_alive | [0.19, 1.33] | dead |  | dead | raw | dead | correct |  | none |
| D96 | 09-59-27 | top_4 | dead_to_alive | [0.47, 2.56] | dead |  | dead | raw | dead | correct |  | none |
| D97 | 09-59-27 | bottom_1 | alive_to_dead | [-1.61, -0.47] | dead |  | dead | raw | dead | correct |  | none |
| D98 | 09-59-27 | top_2 | dead_to_alive | [-2.37, -0.47] | alive |  | alive | raw | alive | correct |  | none |
| D99 | 09-59-27 | top_4 | dead_to_alive | [-1.99, -0.09] | alive |  | alive | raw | alive | correct |  | none |
| D100 | 09-59-27 | bottom_1 | dead_to_alive | [-1.61, -0.66] | alive |  | alive | raw | alive | correct |  | none |
| D101 | 09-59-27 | top_1 | dead_to_alive | [-0.38, 0.57] | indeterminate |  | indeterminate | raw | unknown | indeterminate |  | none |
| D102 | 09-59-27 | top_4 | alive_to_dead | [-1.33, -0.38] | dead |  | dead | raw | dead | correct |  | none |
| D103 | 09-59-27 | top_4 | dead_to_alive | [0.38, 2.27] | dead |  | dead | raw | dead | correct |  | none |
| D104 | 09-59-27 | top_1 | alive_to_dead | [-2.46, -0.38] | dead |  | dead | raw | dead | correct |  | none |
| D105 | 09-59-27 | bottom_2 | dead_to_alive | [0.57, 1.51] | dead |  | dead | raw | dead | correct |  | none |
| D106 | 09-59-27 | top_3 | dead_to_alive | [-1.14, 0.0] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| D107 | 09-59-27 | bottom_2 | dead_to_alive | [-1.14, 0.0] | alive |  | alive | raw | unknown | pipeline_unknown |  | none |
| D108 | 09-59-27 | bottom_3 | dead_to_alive | [-1.89, -0.95] | alive |  | alive | raw | alive | correct |  | none |
