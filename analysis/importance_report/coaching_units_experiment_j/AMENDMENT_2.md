# Experiment J: Amendment 2 (2026-10-21), prior judgment-bearing recordings

`DESIGN.md`, `design.json`, `AMENDMENTS.md` (Amendment 1) and
`census_order.json` stay byte-identical (hashes in `SHA256SUMS`). This
amendment overrides them where they conflict. The rater requested it as
"Amendment 1". It is numbered 2 because Amendment 1, the 2A/2B/2C split, was
already sealed.

## Context

The frozen design intended a fresh held-out population. Its exclusion rule
named Experiments H and I, the coaching gap study, the N2 citation
experiment and the citation-contract fixtures. It did not exhaustively name
all earlier coaching or death-judgment work.

**When it was made.** This amendment was made during stop point 2A, after
the recording universe and order were enumerated and sealed in
`census_order.json` (commit `8571bd8`). It was made before any of the
following had been seen:

- any J `analyze` run;
- any mechanical eligibility result;
- any relevance label;
- any model output.

At that point, 14 ordered recordings were identified as having prior
judgment-bearing use:

- **9** were previously used in coaching-output rating work: Experiments
  D–G, the early coaching-unit rating sheet, the deferred units and the
  layout-fix work.
- **5** were previously used only in death-importance ranking studies.

## Rule

- All 14 recordings are excluded from Experiment J's 2B census and from
  every downstream J population.
- **The sealed recording order is unchanged.** The exclusion is applied from
  the sealed prior-use annotations (`other_prior_references` in
  `census_order.json`). No recording is removed or reordered
  retrospectively, and the remaining recordings keep their sealed order
  positions.
- **The 85-recording roster-detector corpus (`roster_evidence`) is not
  excluded solely for roster-detector use.** That work was vision and
  detector measurement, not judgment of coaching output or death
  importance. A recording in that corpus is excluded only if it
  independently meets one of the two categories above. The same applies to
  the design-only `final30_score_gate` reference.
- **No other prior-use category is added after J analysis begins.** Any
  contamination discovered after analysis starts is reported. It does not
  silently expand this amendment.

Categories as applied (`prior_judgment_category` in
`tools/reports/coaching_units_experiment_j.py`):

- **coaching_output:** the recording is referenced by any of
  `coaching_units_experiment_d`, `coaching_units_experiment_e`,
  `coaching_units_experiment_f`, `coaching_units_experiment_g`,
  `coaching_units_rating_sheet.csv`, `coaching_units_rating_key.csv`,
  `coaching_units_deferred` or `assessment_layout_fix`.
- **death_importance:** the recording has no coaching_output reference, but
  is referenced by another `analysis/importance_report` entry other than
  `roster_evidence` and `final30_score_gate`. The entries involved are the
  death-importance ranking studies (`1hz_experiment`, `rescore_v2`,
  `rescore_v2_rules`, `v2_1hz`, `roster_detector_fix`, the `report-*.md`
  ranking reports, `cases.md`).

## The 14 excluded recordings (sealed order positions)

| order | recording | category | video_identity (sha256) |
| --- | --- | --- | --- |
| 23 | `en-2026-09-04 21-39-09.mov` | death_importance | `9d77090ae6ac…` |
| 45 | `ja_hammerhead_bridge_tenta_missle_2026-09-16 19-12-39.mov` | coaching_output | `c53faf5db727…` |
| 46 | `ja_hammerhead_bridge_bouya_bomb_2026-09-16 19-19-16.mov` | coaching_output | `d48928a0ded4…` |
| 48 | `ja_brinewater_springs_crab_tank_2026-09-16 19-49-36.mp4` | coaching_output | `a2e9dcede1b4…` |
| 49 | `ja_mahi_mahi_resort_ultra_stamp_2026-09-16 19-49-36.mp4` | coaching_output | `f004e35156d9…` |
| 50 | `ja_mahi_mahi_reef_slider_2026-09-16 20-00-03.mov` | coaching_output | `64f91749ede2…` |
| 51 | `ja_brinewater_springs_triple_inkstrike_2026-09-16 20-06-30.mov` | coaching_output | `1d4c3bc45d52…` |
| 52 | `ja_brinewater_springs_sumper_chump_2026-09-16 20-13-03.mov` | coaching_output | `7bd26d247bc3…` |
| 53 | `ja_mahi_mahi_kraken_2026-09-16 20-18-04.mov` | death_importance | `3e03f298e3de…` |
| 54 | `ja_brinewater_springs_triple_splashdown_2026-09-16 20-27-13.mov` | death_importance | `d25fd31d6f3e…` |
| 55 | `ja_mahi_mahi_resort_splattercolor_screen_2026-09-16 20-31-24.mov` | coaching_output | `5338767e2640…` |
| 56 | `ja_inkblot_art_academy_big_bubbler_2026-09-16 20-53-49.mov` | death_importance | `885ed8c7be73…` |
| 85 | `jp-2026-09-20 23-46-44.mov` | death_importance | `68bd96360c60…` |
| 91 | `en-2026-09-21 23-13-21.mov` | coaching_output | `83d9eb2fbda0…` |

Full identities and prior references are in `census_exclusions.json`. That
file is derived from `census_order.json` by
`coaching_units_experiment_j.py census-exclusions` and records the order
file's sha256.

## Resulting 2B sequence

**109 recordings:** the 123 sealed recordings minus these 14, kept in
sealed order. The sequence runs from order 1 (`en-2026-06-30 21-17-03.mov`)
to order 123 (`en_SW2--2026-10-19 23-20-28.mov`) and is listed in
`census_exclusions.json` (`sequence_2b`).

This is the predetermined 2B sequence. 2B walks it in order and applies the
frozen rules, as amended, unchanged.
