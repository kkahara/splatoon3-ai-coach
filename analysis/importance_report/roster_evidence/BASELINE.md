# Gameplay HUD roster detector: measured baseline

Status: measurement only. No acceptance criteria are set here and no fixes are
proposed. The roster detector, templates, thresholds, fusion settings and
`coach.roster_evidence=false` are unchanged.

Scope: the 2 FPS gameplay-HUD roster X detector (`vision/player_count.py`) and
its production per-side fusion. Review timeline lamps and gameplay-to-Review
alignment were not used anywhere in this baseline.

Live gameplay and replay-derived footage are reported separately. The
replay-derived class is one sped-up replay recording (the Oct 7 counterexample,
`en-preview-full-fast-2026-10-04 21-32-55`); its numbers describe that one
source, not replay footage in general.

## Provenance

| Artifact | Commit / hash |
| --- | --- |
| Reconstruction, tools, consistency run, frozen blank sample | `a8493b7` |
| Completed labels, sealed before the key was opened | `d4ac92d`, labels sha256 `cd10f3b0…a847b` |
| Frozen sample manifest | sha256 `6dc7d574…f8ff113` |
| Detector key | sha256 `3f882b2f…12d2` |
| Config | sha256 `17cf9fa6…9d621` |

Two label clarifications were made before sealing and before the key was opened,
both decided by the labeler:

- Eight replay-derived `o1` cells were first written as `dark silhouette` (a
  solid black opponent icon). They were recorded as `x`, with the original
  wording kept in `notes`.
- R071 was marked `hidden` but every slot was labeled; the image shows a visible
  roster, and it was recorded as `visible`.

## 1. Consistency (no labels)

Source: `baseline/consistency.md`. These are change and disagreement magnitudes,
not errors.

| Measure | Live gameplay | Replay-derived |
| --- | ---: | ---: |
| Matches | 44 | 1 |
| Usable / scheduled readings | 18,854 / 22,153 | 373 / 401 |
| Roster visible | 85.1% | 93.0% |
| Consecutive raw changes | 15.3% (2,780 / 18,162) | 24.3% (89 / 367) |
| Change magnitude 1 / 2+ | 2,511 / 269 | 83 / 6 |
| One-frame A-B-A reversals | 136 | 3 |
| Implausible fused intervals | 44 | 0 |
| Deaths inside an implausible interval | 10 / 172 | 0 / 5 |
| Pre-death fused sample present / missing / unreliable | 161 / 0 / 11 | 5 / 0 / 0 |
| Even vs odd 1 Hz phase disagreement at death | 12.8% (22 / 172) | 60.0% (3 / 5) |

## 2. Hand-labeled correctness

Source: `baseline/labeled.md`. 120 frozen frames: 97 live, 23 replay-derived,
20 per stratum. `?` is human ambiguity and is excluded from accuracy; it is
reported on its own.

### Human-determinability

| Measure | Live gameplay | Replay-derived |
| --- | ---: | ---: |
| Human-determinable slots | 78.2% (607 / 776) | 78.3% (144 / 184) |
| Detector said X / alive on human-ambiguous slots | 4 / 37 | 0 / 8 |

Most ambiguity comes from the `hud_hidden` stratum (0 / 160 slots
determinable) and the `margin` and `flicker` strata (85.0% and 89.4%). The
`pre_death` and `steady` strata were 100% determinable.

### Reliability ladder

Each level is scored only on frames where every input it needs was
human-determinable, so denominators shrink going down the ladder.

| Level | Live gameplay | Replay-derived |
| --- | ---: | ---: |
| Slot X precision | 96.0% | 100.0% |
| Slot X recall | 91.5% | 77.1% |
| Exact side count | 91.4% (138 / 151) | 77.8% (28 / 36) |
| Exact raw AvB | 82.7% (62 / 75) | 55.6% (10 / 18) |
| Exact fused AvB | 74.3% (55 / 74) | 11.1% (2 / 18) |
| Exact pre-death fused AvB | 82.4% (14 / 17) | 25.0% (1 / 4) |
| HUD-visible precision / recall | 93.8% / 100.0% | 94.7% / 100.0% |

HUD-visible recall of 100% means the detector never called a human-visible
roster hidden. The lower precision means it called a roster visible on some
frames the labeler judged hidden.

## 3. Where reliability is lost

### Live gameplay

- **Slots.** 13 slot disagreements among determinable visible slots: 9 human X
  read as alive (5 ally, 4 opponent) and 4 human alive read as X (1 ally, 3
  opponent).
- **Raw to fused.** Of 74 fully determinable live frames:
  - raw and fused both match the human: 51;
  - raw matches, fused differs: 10 (plus 1 with no fused value);
  - raw differs, fused matches: 4;
  - raw and fused both differ: 9.

  Fusion therefore loses more correct frames (11) than it recovers (4) on this
  sample. That is consistent with debounce and hold reporting the previous
  count around real changes, but this baseline measures it and does not
  establish the mechanism.
- **Pre-death.** 3 of 17 determinable pre-death frames differ: R022 (human 3v2,
  raw 3v3, fused 2v3), R043 (human 3v3, raw 3v3, fused 4v4), R101 (human 2v4,
  raw and fused 3v4).

### Replay-derived

- **Slots.** All 8 slot disagreements are the dark-silhouette `o1` cells:
  labeled `x`, read as alive by the detector. There were no other replay slot
  disagreements.
- **Raw to fused.** Of 18 fully determinable replay frames:
  - raw and fused both match: 2;
  - raw matches, fused differs: 8;
  - raw and fused both differ: 8 (the silhouette frames).
- **Pre-death.** 3 of 4 differ: two silhouette frames (R062, R104) and R089
  (human and raw 4v3, fused 4v4).

## Limits of this baseline

- Replay-derived results come from one recording; live results come from 29
  matches in the frozen sample (44 in the consistency run).
- Pre-death fused AvB rests on 17 live and 4 replay frames.
- Strata were deliberately oversampled for hard cases (`margin`, `flicker`,
  `fused_change`, `hud_hidden`), so sample-wide percentages are not estimates
  of match-wide rates. Per-stratum results are in `baseline/labeled.md`.
- Labels come from one labeler, with no second-labeler agreement measured.
- Whether a dark-silhouette icon means the same thing as a roster X marker (for
  example a disconnected player) is a labeling decision recorded above, not a
  verified game rule.

## Artifacts

- `RECONSTRUCTION.md`: how roster state was detected, prior validation, and why
  roster evidence was turned off.
- `baseline/consistency.json`, `baseline/consistency.md`: unlabeled
  consistency.
- `baseline/SAMPLE.md`, `baseline/labels/`: frozen sample, images, sealed
  labels, key.
- `baseline/labeled.json`, `baseline/labeled.md`: scored results, including
  per-stratum breakdowns.
