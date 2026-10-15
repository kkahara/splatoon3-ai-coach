# Roster HUD geometry investigation

Status: development measurement only. Production detector, configuration, fusion and coaching remain unchanged. The 0.03 threshold is unchanged.

## Dataset

- 120 frozen development frames: 97 live gameplay and 23 replay-derived.
- Modes/languages/HUD states are inherited from the frozen stratified sample; this is not a held-out set.
- Automated component measurements are descriptive. They are not ground truth for individual icon boundaries.

## Measured layout

| Source | Frames | Observed components | Mean ally dx/dy | Mean opponent dx/dy | Median within-side gap |
| --- | ---: | ---: | ---: | ---: | ---: |
| live_gameplay | 97 | 758 / 776 | 0.0 / 0.6px | 2.2 / 1.6px | 93.2px |
| replay_derived | 23 | 174 / 184 | -4.5 / 3.7px | -4.2 / 2.9px | 93.2px |

The fitted centers remain close to the calibrated layout: the constrained search is limited to a 16-pixel per-side anchor shift. The observed component stage is intentionally treated as a measurement aid; saturated game effects and black/X icons can merge or disappear, so component boundaries are not used as automatic labels.


### Coverage by source, language and mode

| Source | Language | Mode | Frames |
| --- | --- | --- | ---: |
| live_gameplay | en | splat_zones | 50 |
| live_gameplay | ja | rainmaker | 7 |
| live_gameplay | ja | splat_zones | 40 |
| replay_derived | en | clam_blitz | 23 |
## Geometry models

- **M0 fixed:** the production boxes are the reference layout.
- **M1 shifted fixed layout:** one per-side `(dx, dy)` anchor shift; this was the selected model for all 120 frames.
- **M2 anchor + scale + spacing:** evaluated as a constrained alternative. It did not produce a meaningful improvement over M1 in this sample after the small-complexity penalty.
- **M3 per-slot size variation:** not selected. It is not justified by the automated component measurements; any small residual improvement would be outweighed by unstable boundaries around X and special effects.

The result supports a bounded geometry hypothesis: small common offsets exist, but the survey does not show evidence that per-slot variable geometry is the primary explanation for the raw misses. The next prototype therefore uses M1/M2-style constrained localization, not unconstrained blob assignment.

## Audited known live misses

The following classification is based on the saved overlays, not on automatic segmentation alone. Every known miss had adequate visible icon coverage inside the fixed ROI; no case was classified as clear localization/clipping, visibility, or ambiguous from these panels.

| Sample | Slot(s) | Audit category |
| --- | --- | --- |
| R013 | a1 | classification with adequate icon coverage |
| R019 | o4 | classification with adequate icon coverage |
| R021 | o3 | classification with adequate icon coverage |
| R022 | o3 | classification with adequate icon coverage |
| R023 | a1 | classification with adequate icon coverage |
| R028 | a1 | classification with adequate icon coverage |
| R029 | o1 | classification with adequate icon coverage |
| R036 | a1 | classification with adequate icon coverage |
| R049 | o4 | classification with adequate icon coverage |
| R052 | a1 | classification with adequate icon coverage |
| R057 | o4 | classification with adequate icon coverage |
| R101 | a1 | classification with adequate icon coverage |
| R119 | o3 | classification with adequate icon coverage |

This audit means the 13 known live slot misses are provisionally classification failures with adequate coverage, not demonstrated fixed-ROI localization failures. It does not prove that fixed ROIs never drift; it says these misses do not provide auditable evidence of clipping.

## Artifacts

- `geometry.json`: per-frame fitted boxes, centers, sizes, gaps, observed components and audit fields.
- `known_miss_overlays/*.png`: full-frame audit panels. Green = fixed ROI, blue = constrained dynamic ROI, red = observed component boundary, yellow = masked-X minLoc.
- Primary X classification remains the production 0.03 threshold; no threshold was selected from a sweep.
