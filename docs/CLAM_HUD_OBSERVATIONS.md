# Clam Blitz HUD observations

`clam_score` and `clam_basket_state` are experimental, opt-in detectors. Add
those names to `vision.enabled_detectors` to record per-frame observations.
`clam_score` now feeds a separate Clam snapshot observation and the additive
ScenarioContext objective inspection path. It does not change the legacy score
fields, Scenario ownership, ranking, coaching, or events. Basket observations
remain independent of score fusion.

`clam_score` reuses the shared score digit and penalty ROIs, templates, and
`+`-glyph requirement. It adds Clam-specific white-on-color segmentation.
`ScoreReading.battle_mode_id` identifies the reader configuration; it is not
independent evidence of the actual match mode. The score digit ROIs were
independently calibrated in `analysis/clam_blitz_score_survey/rois.yaml` and
match the Splat Zones settings. The reader matched both counters in all 12
labeled EN/JA study frames. One supplied 80–53 screenshot produced 80, 53,
and a right penalty of +11. Another supplied 80–32 screenshot contains red
lines across the digit crops; its penalties were read as +10 and +16. Penalty
accuracy across independent clean Clam footage remains unvalidated.

## Score association and persistence

The existing English and Japanese development frames show the allied HUD bank
and count on the left, and the opposing bank and count on the right. For a
resolved in-match Clam Blitz frame, `clam_score` is associated using those
fixed sides. The same source result ID and canonical source frame identify all
observations. Results must be unique and digit matches must meet the configured
threshold. A visible `+N` may be observed independently of the main count;
missing or unreadable values are unknown, never zero. Map-overlay frames are
unknown. No value is held or backfilled, no Splat Zones score-rate rules are
applied, and no basket state is used to establish score or progress.

An opt-in detector is required: add `clam_score` to `vision.enabled_detectors`.
Refusion can populate these observations from stored `clam_score` readings;
it cannot create absent detector observations. The detector's labeled 12-frame
main-count study and the two reviewed English/Japanese penalty examples are
development checks. They do not independently validate penalty accuracy,
single-digit score accuracy, or generalization to every palette or condition.

## Basket visual state

The `vision.clam_blitz.basket_state` ROI is normalized
`[0.475, 0.112963, 0.525521, 0.199074]`, approximately `[912, 122, 1009, 215]`
at 1920×1080. The user-declared rule is: the small white anchor means
`closed`; the large colored or black circle means `open`. The black circle can
return to a colored presentation when a previously thrown clam reaches the
basket, so it must not be treated as closed. Missing or unreadable HUD is
`unknown`, not an inference from the absence of the white icon.

The detector uses a white-anchor template for `closed`, colored sectors or a
dark-anchor template within the large circle for `open`, and otherwise returns
`unknown`. Templates are stored under `calibration/templates/clam_basket_state`.
The user has adopted the HUD rule; that decision is distinct from a measured
accuracy claim for the implementation. Current example crops come from one
match and are regression coverage, not an independent accuracy evaluation.
Recognized states return a categorical confidence signal of 0.7, and unknown
returns 0.0. Fusion's 0.5 threshold accepts that signal; neither value is a
calibrated probability or a ranking of recognition quality.

Vision snapshots carry `basket_state` and `basket_state_evidence_ids` only for
usable readings in independently resolved Clam Blitz gameplay. Missing readings,
map overlays, other modes, and post-match frames are `unknown`. There is no
holding, episode construction, event emission, or inference of timer duration.
The state does not flow into ScenarioContext, ranking, or coaching.

Hue and hue concentration are raw color measurements. Team association remains
independent, and stays `unknown` because color-to-team association has not been
validated. In particular, a black circle does not identify a team.

## Future temporal evidence

A later temporal layer may use video timestamps to bracket transitions using
last confirmed closed, first confirmed open, last confirmed open, and first
confirmed closed observations. Such intervals bound transitions rather than
invent exact opening or closing instants. Video time is the primary basis for
an observed open episode; game-clock readings can provide secondary context.
A future evidence layer could compare an episode with player-event timestamps.
An episode can span an extension using observed states and does not need a
radial timer model. No episode, duration, remaining-time/count estimate, or
player-relative timing is implemented here.
