# Experiment J: exploratory diagnostics from the 64 completed human reviews

Recorded 2026-10-22, with `PARTIAL_REVIEW_CHECKPOINT.md`. **Exploratory
only.** This is not Experiment J's result, decides no gate, and is not an
M-versus-A comparison. The key, automatic artifacts, ablations, labels and
ideal-coaching notes were not opened. Rows are grouped only by what the rater
saw: the assessment and recommendation text.

Every count is **within the 64 completed reviews**. It is not an estimate
for the 172 rows or for any population.

## Limitations

- Non-random subset: where the rater stopped was not pre-specified. The rows
  are the first 40 and last 24 of the shuffled order, covering 33 of 40
  deaths (2 fully) and 17 recordings, all English.
- Rows on the same death are not independent: 64 rows are 33 deaths.
- One rater. Notes exist on 8 rows, so a missing note does not mean the row
  had no issue.
- Recommendation type correlates with arm. A difference between groups below
  is not an arm effect.
- Clip windows were extended during rating (procedure notes 1-3).
- Rater claims that conflict with detector output (rows below) were not
  checked against the analysis artifacts.

## Score distribution

| measure | 2 | 3 | 4 |
| --- | --- | --- | --- |
| usefulness | 28 | 30 | 6 |
| actionable | 38 | 22 | 4 |
| specific to this death | 48 | 15 | 1 |

No row got a 1 or a 5. `supported_by_footage`: 57 yes, 7 no.

## Patterns

1. **Supported but low value: a bare out-of-play duration.** 29 rows have no
   recommendation (usefulness 2 on 21, 3 on 8). 18 of them consist of one
   sentence stating how long the player was out of play; 16 of those 18 were
   rated 2/2/2. None was marked unsupported. Whether better coaching needs
   other selection logic or evidence that does not exist yet: **UNKNOWN**.
2. **The map-after-respawn recommendation is where the higher scores are.**
   30 rows have it: usefulness 3 on 21, 4 on 6 and 2 on 3. All 6
   usefulness-4 rows have it. Its wording is the same fixed template every
   time, and specificity is mostly 2. That is consistent with "supported but
   generic" (**PRESENTATION / COACHING_LOGIC**, not separable here).
3. **"Since you were back in play" on a first death.** Notes on 3 rows
   (`o160`, `o171`, `o172`) say that when the window starts at match start,
   the wording should say "since the game started". `o033` makes a related
   point: "after respawning" misleads when the player had been alive for
   over a minute. The source of the window (`match_start` /
   `previous_respawn`) is already in the view, so: **PRESENTATION**.
4. **Map checks the rater saw but the evidence did not.** On one death
   (`en-2026-06-30 21-17-03`, 1:12), notes on `o160` and `o166` say the
   player checked the map several times before the death. The outputs say
   none was observed. A **DETECTOR_QUALITY** candidate (MAP_OVERLAY missed or
   brief checks), unconfirmed. Two other rows on that death (`o013`, `o028`)
   carry the same claim and were marked supported.
5. **Special gauge.** On one death (`en-2026-07-04 15-34-49`, 3:17), the note
   on `o039` says the special was not charged. Three other rows on that death
   (`o007`, `o019`, `o023`) state it was charged and were marked supported. A
   **DETECTOR_QUALITY** candidate, unconfirmed.
6. **Timing precision.** `o154`: the splat was "more like 3 seconds" before
   the death, not 2.0 (`o009`, the same death and claim, was marked
   supported). `o169`: out of play "more like 10 seconds", not 8.5. These
   are single instances. They could be measurement error or a different
   definition (sample cadence, or when control returns): **UNKNOWN**.
7. **Reposition-after-splat recommendation.** 5 rows: usefulness 2 on 4 and
   3 on 1. The 2 unsupported rows are the timing notes in pattern 6. Too few
   rows to interpret.

## Not established

- No note names a missed coaching opportunity or asks for evidence the
  pipeline does not collect, so **MISSING_EVIDENCE** is not established by
  these reviews. Low specificity across all rows is compatible with it, but
  is not evidence for it.

## Cheap next checks (not done here)

- Compare MAP_OVERLAY events with the footage for the death in pattern 4,
  and the special-gauge readings for the death in pattern 5.
- Use `back_in_play.source` in the respawn-window wording (pattern 3) if that
  wording reaches production.
- Revisit the value of a bare out-of-play sentence when nothing else
  qualifies (pattern 1).
