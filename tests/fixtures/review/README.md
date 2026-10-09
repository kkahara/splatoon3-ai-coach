# Review timeline regression fixtures

These full-resolution frames come from the local Review validation recordings
`2026-09-30 21-49-20` and `2026-09-30 21-56-52`.

- `timeline_21-49-20.jpg` and `timeline_21-56-52.jpg` are colored-control-band
  positives for timeline structure detection.
- `player_view_21-56-52.jpg` is a non-timeline negative.
- Files ending in `_left`, `_middle`, or `_right` validate cursor-following
  roster geometry. Their eight hand-checked lamp labels are encoded in
  `tests/test_review_roster.py`.

- `timeline_penalty_both.png` shows both penalty pills at once: teal remaining
  78 with penalty 14, red remaining 40 with penalty 32, and the clock reads 3:06.
  The displayed penalty numbers are values, not durations.

Event-lane detection (`tests/test_review_event_lane.py`) uses the authoritative
templates in `calibration/templates/review/specials/` pasted into synthetic
lanes, plus a real-frame regression on `timeline_21-49-20.jpg` and
`timeline_21-56-52.jpg`: every accepted special must be its slot's own kit
special (hand-checked from the frames). Death counts are not asserted there
because they depend on the glyph representation chosen by the calibration
report.

Per-event special/death ground truth is not fabricated. It comes from
reviewed `review_special_sheet.py` labels under
`analysis/review_validation/specials/<recording>/sheet/labels.yaml`.

`lane_corpus/<recording>/` freezes the three static frames production selects
for each calibration recording (bytes unchanged) and `corpus.yaml` (axis,
cursor x, confirmed labels, and whether production accepts each label).
Tuning: 21-49-20, 21-23-30; held-out: 21-56-52, 21-41-07. Rebuild with
`tools/calibration/review_special_fixture.py` only after a deliberate,
documented calibration change (see
`analysis/review_validation/specials/calibration_report/DECISION.md`).
Classification-aware echo suppression
(`analysis/review_validation/experiments/echo_keep_deaths/REPORT.md`) flipped
five death labels to accepted and added two reviewed deaths (21-49-20, from
extraction windows W02/W22) that the old rule removed before labelling.

`roster_grid/` holds three full frames (21-23-30 at cursor x 1480 and 1668,
21-41-07 at x 1228) where the old roster offset search moved the top-row slot
grid off the cursor-centered position (+14.5, −14.5 and +58 px). Their
hand-checked top/bottom lamp states are encoded in
`tests/test_review_roster.py`.

`clock_labels/` holds clock-label crops (the exact `_read_clock_from_image`
ROI around the cursor) from 21-23-30: an orange overtime `+0:28`, a dim
pre-GO `-0:02` whose minus was previously missed, and two regulation labels.
Used by `tests/test_review_clock_sign.py`.
