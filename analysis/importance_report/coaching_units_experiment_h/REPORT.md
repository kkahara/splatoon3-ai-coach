# Experiment H report: roster evidence in coaching

**Status: closed 2026-10-16.** `coach.roster_evidence` stays off; the
next research phase is the coaching gap study
(`../coaching_gap_study/`).

Decision rule applied exactly as pre-registered in `DESIGN.md`
(thresholds unchanged). Directional only: one rater, small n. Any
production change is a separate decision.

Ratings: 132 rows, sealed before the key was opened. Blinding: intact. The rater confirmed that automatic_summary.json and two arm-A call records were opened only after all 132 ratings were finished.

## Outcome: Passes 1 and 2, fails 3: roster evidence is handled correctly but shows no demonstrated value. Keep `coach.roster_evidence` off.

## Decision rule

### 1. Safety gate (arm C): **pass**

- Roster-rule violations C 0 (must be 0): pass
- Count-fidelity errors C 0 (must be 0): pass
- Blocked outputs C 0 vs A 0: pass
- Validator hits per call C 0.0 vs A 0.091 + 0.25: pass

### 2. Q1 correction effect: **pass**

- C outputs on at_or_before_death deaths mentioning roster: 2 of 10; count fidelity 2 / 2 (>= 90% required, zero mentions fails): pass
- Mean B-vs-C divergence treatment 0.1457 vs control 0.1219 (treatment must exceed): pass
- Treatment outputs rated roster_use=correct: C 2 vs B 0 (C >= B): pass
- Roster-mention frequency (automatic; treatment / control / at_or_before_death, of 22 / 22 / 10): A 0 / 0 / 0; B 0 / 2 / 0; C 2 / 2 / 2

### 3. Q2 value: **fail**

- Mean usefulness C - A over all 22 deaths -0.0227 vs noise floor 0.1364 (must exceed): fail
- Supported rate C 0.9773 vs A 1.0 (C >= A): fail
- Mean usefulness A / B / C: 3.0455 / 3.0 / 3.0227
- Mean missing context A / B / C (higher = more missing): 4.9773 / 4.9773 / 5

## Covariates (descriptive, not a gate)

| group | deaths | usefulness A / B / C | roster_use=correct A / B / C |
| --- | --- | --- | --- |
| C_selected=False | 14 | 3.036 / 2.893 / 2.893 | 0 / 0 / 0 |
| C_selected=True | 8 | 3.062 / 3.188 / 3.25 | 0 / 2 / 4 |
| change_type=at_or_before_death | 5 | 3 / 2.9 / 3.1 | 0 / 0 / 2 |
| change_type=unchanged | 11 | 3.045 / 3.091 / 2.955 | 0 / 2 / 2 |
| change_type=window_only | 6 | 3.083 / 2.917 / 3.083 | 0 / 0 / 0 |

Outputs rated as using roster evidence:

| output | death | set | arm | repeat | roster_use |
| --- | --- | --- | --- | --- | --- |
| output_028 | 2026-10-11 22-57-30 `death_episode:308.000` | treatment | C | 2 | correct |
| output_036 | 2026-10-11 22-57-30 `death_episode:308.000` | treatment | C | 1 | correct |
| output_043 | 2026-10-11 20-33-31 `death_episode:180.500` | control | B | 2 | correct |
| output_056 | 2026-10-11 20-33-31 `death_episode:180.500` | control | C | 2 | correct |
| output_061 | 2026-10-11 20-33-31 `death_episode:180.500` | control | B | 1 | correct |
| output_102 | 2026-10-11 20-33-31 `death_episode:180.500` | control | C | 1 | correct |

## Notes (written after the rule was applied; not a gate)

- **The Q1 pass is thin.** Only 2 of the 10 C outputs on
  at_or_before_death deaths mention roster, and both are the same
  death (22-57-30 @308). C says "your team had 2 players alive against
  4"; B, whose pre-refresh evidence there was 3v4, says nothing about
  numbers. The other four at_or_before_death deaths drew no roster
  mention in any arm. The pass satisfies the rule as written and is
  not adjusted, but it rests on one death.
- **Roster is mentioned only at a gap of two players.** All six roster
  mentions (C at @308, and B and C at control 20-33-31 @180.5) cite
  2 against 4, the gap at which the `death_while_outnumbered` factor
  fires (min gap 2). No output in any arm mentions even, ahead or
  one-down states. Roster evidence reaches the coaching text through
  that factor, not on its own.
- **Human and automatic roster judgments agree.** The six rows rated
  `roster_use=correct` are exactly the six outputs the automatic check
  flags as mentioning roster; none was rated incorrect, and the
  automatic count-fidelity check found no errors. The rater later
  reported that these six were marked from the stated counts, not
  from the footage; the footage check in the addendum below confirms
  all six.
- **Q2 fails on both criteria, but the ratings have little spread.**
  Usefulness is 3 on 118 of 132 rows, redundancy 1 on all 132, missing
  context 5 on 130. Most outputs are one or two factual sentences with
  no recommendation, so a single added roster sentence cannot move
  usefulness far. The supported-rate failure is one row (C 43 / 44
  vs A 44 / 44). The outcome stands as pre-registered: no
  demonstrated value, keep `coach.roster_evidence` off.

## Addendum (2026-10-16, after unblinding): roster_use footage check

The rater reported that the six `roster_use=correct` ratings were
entered from the counts stated in the outputs, without checking the
recordings. The sealed `rating_sheet.csv` (4d6d832) is left unchanged.
After the key was opened, the rater counted the roster at the two deaths
those six outputs describe, from the source recordings
(`roster_video_check.csv`). This is an objective count, not a quality
judgment, but it was made with arm identities known.

- **2026-10-11 22-57-30 @308 (treatment).** 3 against 4 from 5:00 to
  about 5:05, then a teammate went down (2 against 4) until the player's
  own death at 5:08. Both C outputs ("2 players alive against 4" just
  before the death) are correct. The pre-refresh reading (B) is 3v4 at
  5:00 (correct) but still 3v4 at 5:08: it missed the teammate going
  down at about 5:05. B's coaching text never stated a count, so no
  wrong claim reached the output. The refreshed detector's change at
  this death is a real correction.
- **2026-10-11 20-33-31 @180.5 (control).** 3 against 4 until 2:59, a
  brief 4 against 4, then two teammates went down almost together
  (2 against 4) at about 3:00, before the player's own death at about
  3:00.5. B and C both read 2v4; all four outputs are correct.

All six ratings are confirmed correct. The decision-rule outcome does
not depend on this column's values here: B has no roster mention on any
treatment death, so "C rated correct at least as often as B" holds for
any relabelling, and Q2 and the safety gate do not use `roster_use`.
