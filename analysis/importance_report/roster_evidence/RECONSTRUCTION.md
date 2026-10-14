# Live-gameplay HUD roster evidence: reconstruction

Date: 2026-10-14

Status: evidence source reopened for measurement only. The roster coaching
capability remains off. This document does not restore a feature or change a
detector, threshold, template, fusion setting, ranking rule, prompt, or public
output.

## Scope

This work concerns only the **live-gameplay HUD roster detector** that reads
the eight player icons around the match clock from ordinary gameplay frames.
It does not concern the Review timeline roster lamps.

The two systems stay independent:

```text
gameplay video -> gameplay HUD X detector -> fused gameplay roster evidence

Review video   -> Review timeline lamps   -> Review roster/death evidence
```

Review-to-gameplay alignment has its own validation gate and has not passed
that gate. Review evidence is therefore neither ground truth nor a correctness
input for this baseline.

## Detection and fusion today

`src/splatoon3_ai_coach/vision/player_count.py` implements
`PlayerCountDetector`.

1. The detector crops four configured ally slots and four opponent slots from
   the top HUD (`vision.player_count.ally_slots` and `opponent_slots`).
2. It computes mean Sobel edge energy for each crop. If any crop is below
   `min_slot_edge_energy: 20`, the roster is treated as not drawn (for
   example, while a map is open or on the respawn map). The reading has
   `roster_visible=false`, no slot scores and zero confidence.
3. Each visible crop is compared with the calibrated roster-X templates using
   masked `TM_SQDIFF_NORMED`. Lower is a better X match. A score at or below
   `sqdiff_match_threshold: 0.03` marks that slot as carrying an X.
4. `PlayerCountReading` persists the one-based ally/opponent X-slot indexes,
   all eight SQDIFF scores, all eight edge energies and roster visibility.
   These are per-frame observations, not local-player `DEATH` events.
5. `vision.state._fuse_player_count_frame` converts X-slot counts to alive
   counts (`4 - X count`) and debounces each team independently.
   `confirm_readings: 2` means a changed count needs two consecutive agreeing
   readings; `hold_seconds: 1.0` permits a recent accepted count to survive one
   pending disagreement. An unreadable frame restarts pending confirmation.
6. Fused `ally_alive_count`, `opponent_alive_count` and confidence are stored
   on `GameStateSnapshot`.
7. `analysis.player_count_series` converts persisted snapshots into sparse
   observations, compresses consecutive equal AvB states, and applies
   `assess_roster_plausibility`. Its queue-based rule marks intervals where an
   alive-count increase cannot be explained by a sufficiently old preceding
   decrease. Confidence is not used to waive the rule.

The evidence means only roster state at a video time: for example, “3 allies
and 4 opponents were observed active.” It does not establish who participated
in a fight, why a player died, or what action the player should have taken.

## History

### Initial implementation

Commit `897e74d` (2026-09-09) added player-count detection. The original
downstream work used pre-death roster observations for two factual importance
factors:

- `death_while_outnumbered`
- `death_while_ahead_in_numbers`

The factors required a configured roster gap. They described the observed
numbers state, not fight quality or causation.

### Debounce and the 1 Hz experiment

Fusion debounce was retained because detector and fusion answer different
questions:

```text
per-frame appearance -> raw roster reading -> debounce -> believed state
```

On 2026-09-28, an experiment compared the existing 2 Hz stream with the even
and odd halves of that stream as two simulated 1 Hz phases. Before the
detector correction:

- 38.4% of consecutive raw readings differed;
- the two 1 Hz phases disagreed on the pre-death roster for 44 of 120 deaths;
- wrong-looking sequences included `3, 2, 2, 4, 2, 1, 1, 2, 4, 1, 4, 4`.

The conclusion was that sampling frequency was not the root problem. Which
half of the detector frames happened to be sampled changed the coaching
evidence.

### Demonstrated detector problems and correction

The investigation found two concrete problems.

1. **The X threshold was too permissive.** Real X markers clustered around
   SQDIFF 0.00–0.02, while ordinary live icons began around 0.04. The previous
   0.08 threshold sat inside the live-icon distribution. Every inspected crop
   in the 0.04–0.08 band was a live icon classified as X.
2. **The detector scored slots while the roster HUD was absent.** Blurred
   background during map/respawn-map displays could score as low as 0.016,
   overlapping actual X markers.

The threshold was moved to 0.03 and the current edge-energy visibility gate
was added. The fusion debounce remained unchanged and production stayed at
2 Hz. The reported re-run over 27 videos improved:

- consecutive differing raw readings: 38.4% -> 16.9%;
- one-frame A-B-A reversals: 889 -> 79;
- even-vs-odd 1 Hz pre-death disagreement: 44 -> 16 of 120;
- factor disagreement between 2 Hz and either 1 Hz phase: 14/15 -> 4/4;
- factor disagreement between the two 1 Hz phases: 13 -> 2.

These were internal consistency measurements, not accuracy against labels.
The remaining 1 Hz differences were attributed to confirmation lag near real
changes. No hand-labeled gameplay-HUD accuracy baseline was produced.

### Why coaching exposure was turned off

The 2026-10-07 evidence audit found that roster still had more authority than
its validation supported: it affected ranking, was projected to the LLM, was
shown publicly, and was explicitly mentioned in the prompt.

The key counterexample was a sped-up Review/replay-derived gameplay recording.
Its fused trajectory included:

```text
29.5  2v4
31.5  1v4
33.5  1v3
34.0  3v3
36.5  4v3
38.5  3v3
```

Two allies appearing to respawn within 0.5 seconds is implausible, yet those
readings carried confidence near 0.99. The public result still displayed
“Roster at death: 4v3” and “Numbers state: advantage,” and the LLM view carried
the same state. Detector confidence therefore did not express the demonstrated
failure.

The response was one capability boundary rather than deleting the evidence
path:

```yaml
coach:
  roster_evidence: false
```

The default remains false with the explicit instruction to enable it only
after reliability has been demonstrated.

## Validation performed before this baseline

The live-gameplay HUD detector has been checked through:

- consecutive-reading disagreement;
- one-frame reversal counts;
- even/odd 1 Hz phase agreement against the 2 Hz stream;
- downstream pre-death and factor agreement across sampling phases;
- visual inspection of selected score-band crops;
- deterministic plausibility checks on fused trajectories.

Those measurements answer whether the stream behaves sensibly. They do not
answer whether an individual slot, side count, raw AvB, fused AvB, or
pre-death fused AvB is correct. No sealed hand-labeled gameplay-HUD reference
set exists yet.

Review timeline roster validation is not included here. It uses different
geometry, different sampling and a different evidence contract.

## Code and gates that remain

The detector is still enabled and runs at the normal HUD cadence of 2 fps.
The evidence path remains available for research and future validation:

- `vision/player_count.py`: masked-X detector and HUD-visibility gate;
- `vision/state.py`: independent per-side debounce;
- `s3-coach refuse`: re-fusion from stored `frame_results`;
- `analysis/player_count_series.py`: sparse samples and plausibility;
- `ScenarioContext.players`;
- `CoachInput` roster samples/windows;
- roster importance factors and factual claim definitions;
- `coach/prompts/coach_roster.txt`;
- `CoachLlmView._project_roster`.

`coach.roster_evidence=false` closes the coaching boundary:

- roster-derived ranking factors do not rank units;
- roster data is stripped from `CoachInput`/the LLM view;
- roster remains an undetermined topic in the claim basis;
- prompt guidance is omitted;
- validator R2 rejects roster assertions;
- public roster lines are withheld.

## Current baseline questions

The next measurement separates:

1. **Consistency:** does the unchanged 2 Hz detector behave sensibly over
   time?
2. **Ground-truth correctness:** are visible gameplay-HUD slots actually X or
   alive when classified?
3. **Potential product fact:** is the unchanged fused AvB correct at the
   pre-death moment coaching would consume?

The result will retain the progression:

```text
slot classification
-> side count
-> raw AvB
-> fused AvB
-> pre-death fused AvB
```

Live gameplay and replay/Review-derived gameplay footage are reported
separately. No acceptance target, detector change, coaching change or proposed
fix is part of this baseline.
