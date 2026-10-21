# Experiment I: postmortem interpretation (separate from the frozen result)

**Frozen result (unchanged, permanent).** Experiment I formally failed its
safety gate (rule 1). Its determination, "fails 1 or 2: production unchanged;
investigate", stands as recorded at `419fae1`. Nothing in the postmortem
(`053e5ab`) or the citation-contract work recomputes, amends or replaces it.

**Interpretation.** The postmortem examined every validator hit (32 adjusted
arm-M hits, 1 arm-A hit). Each one cited a fact that was supplied in the
unit's view, with a matching value, through a path the validator does not
recognize (`REPORT.md`, `evidence_path_rows.csv`). Therefore Experiment I does
not establish an evidence-grounding failure. What it does establish is a
citation-contract failure: the production prompt teaches citation forms that
the view and validator do not support (`docs/CITATION_NAMESPACES.md`,
`tests/test_citation_contract.py`).

This interpretation is not a result of Experiment I and does not make the
arm-M feature eligible for production. Any claim about map-awareness
coaching needs a successor experiment after the citation contract is fixed.
