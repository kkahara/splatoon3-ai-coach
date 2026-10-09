# Review event-lane templates

One PNG per lane glyph, named exactly as in `mapping.yaml` (canonical IDs:
`reefslider`, `super_chump`, `tenta_missiles`, `killer_wail_5.1`, ...).
`tests/test_review_event_lane.py` fails if a file and `mapping.yaml` disagree.
The detector never downloads or synthesizes a missing template; a missing file
is skipped and its glyphs come out as `unknown`.

`death.png` is the event-lane death marker. The other 19 files are the complete
Splatoon 3 special vocabulary.

## Capture

Templates are tight crops from a **1080p** Review timeline frame. At load they
are resized by `frame_height / 1080 × scale` for each entry in
`review.special_template_scales`. Either team's tint works for specials; see
the representation note below for deaths.

## Method (`review/lane_glyphs.py`)

1. **Discover**: per icon row (`review.event_lane_row_centers`, ±
   `event_lane_row_half_height`), slide every template horizontally
   (`TM_CCOEFF_NORMED` on the configured glyph representation). Peaks above
   `special_discovery_floor` whose grayscale window has texture
   (`event_glyph_min_std`) become identity-free candidates. Much weaker
   windows over the same glyph on the same or an adjacent row are dropped
   (icons are taller than the row pitch).
2. **Score**: best and runner-up template within ±3 px.
3. **Accept**: specials need `special_match_floor` and `special_match_margin`;
   the death marker has its own `death_match_floor` / `death_match_margin`.
   Anything else is retained as `unknown`.

Response windows overlapping the cursor band (`cursor_exclusion_half_width`)
are invalidated; the image and templates are never modified. A frame where the
cursor covered a glyph only adds `cursor_obscured_sample_ids`; the event is
recovered from other, clear frames or stays unavailable.

The timestamp anchor (`event_timestamp_anchor`) defaults to the glyph center.

Death-best glyphs become `TimelineEventMarker`s only, never `SpecialUseEvent`s.
A slot showing two different specials raises `special_slot_conflict:<slot>`;
events are not rewritten.

## Calibration

`tools/calibration/review_special_sheet.py` writes contact sheets and draft
labels; `tools/calibration/review_special_report.py` compares representation,
scales, cursor width, anchor and thresholds on tuning recordings and checks
them on held-out recordings.

Frozen from held-out results (see
`analysis/review_validation/specials/calibration_report/DECISION.md`):
grayscale, special gate 0.50 / 0.15, death gate 0.60 / 0.05. Whiteness
(`V·(1−S)`) was rejected: the colored death marker has almost no white, and
it admitted false specials at the lower floor. `tests/test_review_lane_corpus.py`
locks the behavior on the reviewed corpus.
