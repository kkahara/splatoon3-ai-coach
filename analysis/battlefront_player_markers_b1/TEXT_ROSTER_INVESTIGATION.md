# B2 text-first and roster-graphic investigation

**Status:** inspection-only probe, 2026-11-05. No replacement detector, training, tuning, tracking, or production integration was implemented. The saved B1 annotations and raw outputs were read only. The 150s/160s holdouts and frozen battle-line baseline were excluded.

## Scope and provenance

- Recording: Scorch Gorge top-down replay; recording SHA-256 `640368895f93ac129beb93025b81d24c5eb740137b310922ad87dbee5ff3aaaf`.
- Stage identity and map registration: verified for this recording; source polygon SHA-256 `4da7193fbedaaf436ba8c3f5da5802301003509285beaf89ad4cb7812ab00f6f`.
- Text-model evaluation: the 14 frames whose manifest split is `development`, with exact pinned image hashes checked before inference. No other frames were loaded by the evaluation script.
- Preserved B1 point-detector context: 100 clear human centers, 35 matched centers, 65 misses, 1,317 unmatched proposals, 2.6% apparent precision, and 35.0% recall. These prior results were not used as ground truth for the text model.

## Text-region test

OpenCV 5.0.0 includes `TextDetectionModel_DB`. No pretrained DBNet or CRAFT weights were present in the repository or the checked local caches. I downloaded the DB_TD500 ResNet-18 weights from the OpenCV-documented model link to `/tmp`, not into the repository. The failed OpenCV Zoo URL returned 404; the successful weights file is 46.5 MiB with SHA-256 `ad4952eaa2e383dfc448858ed492d51ed159ac83daeff160fee94d3d9e3f2d7d`.

The DBNet run used the documented fixed OpenCV settings: input 736×736, binary threshold 0.3, polygon threshold 0.5, maximum 200 candidates, unclip ratio 2.0. No parameter search, language-specific tuning, OCR, or post-filtering was performed. The model returns text-shaped polygons and scores; it does not recognize a username or classify a proposal as a player.

It produced **125 regions** across the 14 frames, from 7 to 11 per frame. Visual review shows:

- At 130s, 9 text regions are proposed. One region covers a Japanese player-name label among 7 saved player centers. Most player-name labels in this frame are not boxed. Other boxes cover the timer, progress/score UI, and right-side controls. This is an illustrative visual count only, not precision or recall: the annotations have player centers, not text-box boundaries.
- At 120s, the detector boxes the large “They lost the tower!” notification, in separate text pieces. It also finds HUD and control text. This is a clear false semantic candidate if the target class is “player name,” even though it is a valid text detection.
- Across the other frames, boxes also land on clock, scoreboard, map progress, and button-help text, while many small player labels are not boxed. Detection score does not reliably distinguish a name from these UI text regions.

DBNet therefore demonstrates some utility as a **text-like candidate generator**, but it is not sufficient as a name detector on these frames by itself. The fixed-point count is not a fair quantitative text-model evaluation because there are no name-region annotations. The study does not measure username OCR or the special case of a one-period name; no such case was individually labeled in this development set.

CRAFT was investigated but not run: neither PyTorch nor CRAFT weights are locally installed. Its character-region plus character-affinity formulation is relevant to short or irregular strings; it still requires empirical testing of thresholds and postprocessing for a single tiny glyph. CRAFT’s official general pretrained model uses multilingual training sources, while the DB TD500 weights used here are documented for English and Chinese. The DB result on one Japanese label is evidence that this example produced a region, not evidence of Japanese coverage.

## Roster graphic comparison

The 130s screenshot visibly contains four Orange-side and four Purple-side HUD roster cards, with weapon graphics. Some cards show an X overlay, which can obscure the weapon artwork. On the gameplay map, many saved player centers have a small weapon-like graphic, a name label, and a downward pointer nearby. This visual resemblance supports investigating roster-derived templates, but team-color or HUD position alone does not establish which map graphic belongs to which roster slot.

One manually selected Orange-side example was measured with grayscale OpenCV normalized cross-correlation across scales 0.30–0.70 in 0.025 increments. A crop covering an 88×30 px weapon subregion from a roster graphic achieved a best score of **0.733 at scale 0.40**, at map-image location `(769, 287)`, on a small weapon-like graphic above the human center `(785, 357)`. A larger 92×94 px full roster-card crop achieved a best local score of **0.586** elsewhere in the same map search window. This favors comparing the weapon/squid artwork subregion over the colored roster-card backing for this one pair.

This is a single manually selected positive example, not a validated matcher. The crop boundaries were selected visually; no independent negative-pair set or matching threshold was evaluated. Other map graphics vary in apparent shape and size, and several overlap labels or visual effects. The observed best scale is not a fixed transformation. No reliable eight-slot-to-map correspondence or team mapping is established yet.

## Practical combination recommendation

Proceed with an investigation prototype only after CRAFT weights or a suitable multilingual text model are available:

1. Run a text-region generator for high-recall nameplate candidates. Restrict or down-rank HUD and notification regions using the verified map registration, but do not treat the map polygon alone as proof of a name.
2. Use the eight roster weapon/squid subregions as independent appearance templates. Search across a modest scale range and compare against candidate map-side graphic regions. Keep slot identity unknown unless a separate visual association is demonstrated.
3. Group text and graphic candidates when their geometry is compatible; allow graphic-only candidates when text detection misses a short, occluded, or symbol-only name.
4. Use the downward triangle only as optional confirmation and position refinement. Keep presence score, position uncertainty, team evidence, supporting cues, and occlusion/ambiguity as distinct fields.
5. Assign Orange/Purple/Unknown separately. Do not infer team from ink color or roster horizontal order until the relationship is verified. Do not introduce username OCR or player identity in this stage.

The next useful bounded study would compare DBNet and CRAFT on the same pinned development frames, manually label only a small set of text boxes for qualitative error attribution (including short/symbol names where already present), and check roster subregion matches against both positive and negative graphic pairs. Do not use the temporal holdouts or frozen battle-line labels for tuning. No larger annotation campaign is requested here.

## Browser view and implementation boundary

The local review server now serves `http://127.0.0.1:8776/text-roster-investigation`. It displays each development source frame, DBNet text polygons, saved human center marks, the top HUD row, eight individual roster-slot crops, and map crops around the saved centers. Model scores are available in a collapsible list. It does not display or change the triangle-first outputs.

The versioned DBNet artifact is `analysis/battlefront_player_markers_b1/text_roster_investigation/dbnet_text_regions.json`; visual previews are in the sibling `previews/` directory. The result artifact records model and source-manifest hashes, parameters, exact source-frame hashes and the development-only split. The input source frames, B1 annotations, B1 detector outputs, holdouts, and frozen battle-line baseline were not rewritten.

## Files added or changed

- Added `tools/diagnostics/battlefront_player_markers_b1/run_text_model_investigation.py`: DBNet-only evaluation runner with source-hash checks and Pydantic result validation.
- Added `tools/diagnostics/battlefront_player_markers_b1/text_roster_investigation.html`: read-only visual comparison page.
- Updated `tools/diagnostics/battlefront_player_markers_b1/review_server.py`: serves the page and development-only result JSON.
- Updated `tools/diagnostics/battlefront_player_markers_b1/review.html`: links to the investigation page.
- Generated ignored analysis artifacts under `analysis/battlefront_player_markers_b1/text_roster_investigation/`.

Python compilation passed for the runner and review server. The page JavaScript passed `node --check`. The local review page returned HTTP 200, and its JSON endpoint returned HTTP 200 with 14 development records. No pytest suite was run. No detector tuning, CRAFT inference, human reannotation, B1 artifact change, production behavior change, commit, push, or deployment occurred.
