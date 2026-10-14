# Experiment D: coaching-unit arms with the production coach (results)

Status: research only. No production code, prompt, evidence or config was
changed.

## Setup

- **Inputs:** 6 episodes with two or more positive-scoring deaths, three
  arms, 30 calls. The inputs were regenerated with the current system prompt
  and `rec_frames` and sealed before any call (`daad45d`).
- **Model:** Cursor `gpt-5.6-luna`, the production provider. Temperature 0
  was requested but **not sent**: the Cursor SDK has no sampling control.
  This is a documented limitation.
- **Ratings:** Round 1 covers 38 deterministic units (statements only;
  sealed at `3f7fd74`). Round 2 covers the 30 model outputs (assessment and
  recommendations, as on the public page; sealed at `89a2a6a`). Both rounds
  were rated blind, then unblinded. They are read with the rater's declared
  directions:
  - higher missing context = more missing;
  - higher redundancy = more redundant;
  - "enough" = enough to support coaching.

## Results

Round 2, per arm (means):

| Arm | n | Usefulness | Redundancy | Anchor shows situation | Missing context | Enough to coach |
|---|---|---|---|---|---|---|
| individual | 13 | 2.62 | 1.62 | 2.54 | 3.62 | 7/13 |
| episode | 6 | 3.17 | 1.67 | 3.00 | 3.00 | 6/6 |
| episode by factor | 11 | 2.82 | 1.45 | 2.64 | 3.36 | 7/11 |

Validator rule hits per call: individual 0.46 (6/13), episode 0.83 (5/6),
episode by factor 0.73 (8/11). Nothing was blocked, and there were no call
or parse errors.

Round 1 for comparison (all 38 units): usefulness 2.34, missing context
4.42, enough to coach 15/38. The model outputs are modestly more useful and
less incomplete than the bare statements, but are still around 3 out of 5.

## Why the arm differences cannot be read as a grouping effect

1. **Grouped outputs coach only the lead death.** All 8 multi-death outputs
   describe the lead's facts and never a member's. No reference resolves
   only in a member view (0 across the run). The episode arm therefore did
   not deliver multi-death coaching; it delivered the lead's coaching.
2. **Usefulness tracks whether a recommendation exists.** It averages 3.18
   with a recommendation (n=22) against 1.75 without one (n=8). Every
   recommendation in the run is the same sentence, the `rec_frames` of the
   back-to-back-deaths entry: "After being splatted, take a moment to reset
   before re-engaging." Episode leads are always repeat deaths, so the
   episode arm always had it (6/6). That explains its higher mean without
   any grouping effect.
3. **Model variation is as large as the arm gaps.** Ten prompts were
   byte-identical across two arms (a single-death unit in both). 7 of the 10
   pairs produced different text, and their ratings differ by up to 2
   points (3 vs 1, 4 vs 2). The arm gaps are 0.2 to 0.55, with n = 6 to 13.

## Evidence use (input for the decision tree)

Across the 13 deaths:

| Evidence | In the model input | In any output |
|---|---|---|
| Special state at death | 13/13; a "not ready" or "gauge not visible" claim offered for every death | 0 mentions |
| Map-check status | Unknown or absent for all 13 (no map coverage in this corpus) | none |
| Score / zone control | None (no Splat Zones episodes here) | none |
| Recommendation frames | 9 entries, all the same back-to-back-deaths sentence | that one sentence only |

- The model restates the factor statements and the clock, and adds the one
  available recommendation.
- Evidence that *was* present (special state) was never used.
- The evidence the rater asked for most (score, player counts, map) was not
  available in these six episodes.

## Rater's notes in Round 2 (representation, not detection)

- "Video seconds" should be game-clock time.
- "In the final 30 seconds, with 0:18 remaining" is redundant.
- An output without a recommendation reads worse ("I need some
  recommendations"; "having a recommendation is nicer than not having one").

## Conclusion

- **Keep the individual death as the production unit.** Experiment D gives
  no evidence that grouping helps. As built, grouping only changes which
  death is coached, never how many.
- The bottleneck the data points to is **the claim and recommendation
  catalog and its wording**, not the unit policy:
  - one recommendation sentence exists in the whole corpus, and usefulness
    follows its presence;
  - available special evidence goes unused;
  - clock wording uses video seconds.

  This sits on the "evidence utilization / representation" branch, before
  any roster or new-detector work.
- **Limits:** a single non-deterministic model, one rater, six episodes with
  no Splat Zones and no map coverage. A follow-up should first establish
  what any catalog or wording change does to the same 13 deaths.
