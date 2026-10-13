# Review death glyphs: species vs team hue under the frozen template (read-only)

Status: **diagnosis only.** The frozen detector, `death.png`, thresholds and
config are unchanged. No template was selected. Measurement is the frozen
`death.png` track score; candidate templates are reported secondarily.

## Inputs

- 14 Review imports: the nine alignment tuning and v1 held-out recordings and
  the five v2 held-out recordings.
- Expected deaths: 613 roster-lamp alive -> dead brackets (at most 20 px)
  outside the cursor band (`review_row_hue_trace.expected_glyphs`), never
  from the glyph detector.
- Species: operator visual labels per slot per match (`species_labels.yaml`),
  112 slots (69 squid, 43 octopus, none unclear), labelled from colour crops
  (`sheet_<id>.png`) without any template score.
- Frozen score: `death.png` track maximum within ±10 px over non-cursor
  static frames. Produced: a production death marker on the same slot
  within 10 px.

## 1. Squid vs octopus on rows that are not blue/violet

| Species | n | Median | Q25 to Q75 | Below 0.60 | Produced |
|---|---|---|---|---|---|
| octopus | 198 | 0.843 | 0.804 to 0.863 | 17 (8.6%) | 181 (91.4%) |
| squid | 254 | 0.708 | 0.685 to 0.727 | 29 (11.4%) | 225 (88.6%) |

Squid scores are systematically lower: the interquartile ranges do not
overlap, and in all 16 rows containing both species the squid median is
lower (gap 0.05 to 0.16). Squid deaths still mostly clear the 0.60 floor, but
with about 0.11 of headroom against about 0.24 for octopus. The effect on
misses is modest: squid are 56% of these expected deaths and 63% of the
below-floor ones.

## 2. Score vs team hue within each species

| Hue band | Species | n | Median | Q25 to Q75 | Below 0.60 | Produced |
|---|---|---|---|---|---|---|
| 119 to 136 | octopus | 60 | 0.553 | 0.330 to 0.658 | 39 | **0** |
| 119 to 136 | squid | 101 | 0.519 | 0.255 to 0.658 | 57 | **0** |
| other (2 to 82, 139 to 167) | octopus | 198 | 0.843 | 0.804 to 0.863 | 17 | 181 |
| other | squid | 254 | 0.708 | 0.685 to 0.727 | 29 | 225 |

- In the 119–136 band both species fail completely (0 of 161 produced). The
  species gap shrinks to 0.03 there: colour dominates.
- Severity varies by recording within the band. Medians range from 0.19 to
  0.27 (20-33-31 at hue 125, 18-18-00 squid) up to 0.64 to 0.70 (19-47-46,
  19-59-26, 23-56-41). Even at 0.64 to 0.70 nothing is produced: there the
  brightness-variation gate (`event_glyph_min_std`) rejects the glyph, as in
  `../row_hue_diagnosis/REPORT.md`.
- 20-25-22's bottom row at hue 139 behaves like the other hues (22 of 26
  produced). The failing band's upper edge in this corpus is therefore
  between 136 and 139. This is narrower than the 115–140 class used in the
  tool's first summary table (`species_summary.md`), which wrongly includes
  hue 139.

## 3. What the expected deaths below 0.60 and the misses split into

| | Hue 119–136 | Other hues | Total |
|---|---|---|---|
| Below 0.60, octopus | 39 | 17 | 56 |
| Below 0.60, squid | 57 | 29 | 86 |
| At or above 0.60 but not produced (variation gate) | 65 | 0 | 65 |
| **Not produced** | **161** | **46** | **207** |

- Colour accounts for 161 of 207 misses (78%) and 96 of 142 below-floor
  scores (68%).
- Outside the blue/violet band, 46 misses remain (10.2% of 452), split 29
  squid and 17 octopus: about 11.4% against 8.6% of each species.
- The hypothesis that the roughly 10% non-blue misses are squid-related is
  only **weakly supported**. Squid score lower and miss slightly more often,
  but octopus deaths also miss at 8.6%, so species explains a minority of
  the non-blue misses.

## Secondary: candidate templates (reported only, not a selection)

Median scores of `variant_candidates/death_*.png` on the same windows:

| Group | octopus_warm | octopus_cool | squid_warm | squid_cool | frozen |
|---|---|---|---|---|---|
| other / octopus | 0.837 | 0.255 | 0.620 | 0.708 | 0.843 |
| other / squid | 0.702 | 0.238 | 0.790 | 0.889 | 0.708 |
| 119–136 / octopus | 0.512 | 0.336 | 0.409 | 0.520 | 0.553 |
| 119–136 / squid | 0.477 | 0.323 | 0.481 | 0.575 | 0.519 |

- `octopus_warm` (byte-identical to `death.png`) reproduces the frozen score
  (0.837 against 0.843), a consistency check of the secondary scoring.
- Species-matched candidates score higher on their own species outside the
  blue band.
- No candidate restores the blue/violet band. The blue-tinted
  `death_octopus_cool` (hue 125) scores lowest everywhere, including on blue
  rows. That fits the grayscale contrast mechanism: a coloured template does
  not restore variation the grayscale image no longer has.
- `death_squid_cool` is hue 80 (green), not blue/violet.

## Implication for a later pre-registration (not designed here)

- The primary defect is **colour**: the blue/violet band (observed 119 to
  136) loses both the template score and the variation gate. Template
  variants alone are unlikely to fix it; the preprocessing or the gate is
  implicated.
- **Species** is a secondary, real effect: about 0.13 lower score for squid
  under the octopus-only template. It narrows the margin to the floor and is
  a plausible contributor to non-blue misses, but not the main one.
- Answer to "species, colour, both, or neither?": **colour first, species
  second.** Both should be variables in the colour-robust investigation, with
  hue treated as measured (the failing band is observed at 119 to 136, and
  139 works) rather than as warm/cool names.

## Reproduce

```bash
A=analysis/review_validation/alignment
PYTHONPATH=tools/diagnostics .venv/bin/python tools/diagnostics/review_death_species_trace.py sheet \
  $A/tuning/{23-43-31,23-56-41,18-18-00,18-25-16,20-33-31} \
  $A/heldout/{22-20-42,22-31-53,22-37-47,22-57-30} \
  $A/v2/heldout/{17-42-59,19-47-46,19-59-26,20-17-32,20-25-22} \
  --out analysis/review_validation/death_species
PYTHONPATH=tools/diagnostics .venv/bin/python tools/diagnostics/review_death_species_trace.py analyze \
  --out analysis/review_validation/death_species
```

The per-recording table, the per-band produced counts and the secondary
medians in this report were computed inline from `death_scores.json`,
`species_labels.yaml` and each `review_timeline.json`.
