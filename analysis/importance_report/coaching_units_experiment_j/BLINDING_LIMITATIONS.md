# Experiment J: known blinding limitations

Recorded for the final Experiment J report. This is not a design amendment:
the design, populations, gates, thresholds and the sealed stop point 4
package (`stop_point_4.sha256`, commit `6b637cd`) are unchanged.

## 1. Row multiplicity reveals natural C-point status (recorded 2026-10-21)

**What had been seen when this was recorded:** only automatic structural
integrity checks of the sealed absolute sheet (header, row and death counts,
empty rating columns, footage paths, and a pattern scan for leaked
identifiers). No absolute rating had been entered. The automatic summary,
automatic checks, ablated versions and rating key had not been opened.

Before absolute ratings began, an automatic structural scan established that
row multiplicity can reveal natural C-point status at the death level: 34
deaths have four rows and six deaths have six rows. Therefore a rater who
deliberately groups or counts rows by death could identify the six deaths on
which the diagnostic C arm was run.

The sheet does not reveal which individual output is A, M or C, and it does
not expose relevance labels, mechanical eligibility, population membership,
ablation status, repeat identity, automatic results or rating-key mappings.

C is diagnostic only and cannot determine Experiment J's pass/fail outcome.
The sealed rating package therefore remains unchanged: no rows were
regenerated, removed, padded or otherwise altered, and the key is unchanged.

**Rater procedure:** during rating, the rater processes the frozen shuffled
rows independently and does not intentionally group outputs by death, count
death multiplicities, or attempt to infer C-point status.
