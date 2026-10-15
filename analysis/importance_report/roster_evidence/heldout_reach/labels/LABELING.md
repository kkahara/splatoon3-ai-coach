# Held-out roster labels

Frozen held-out sample for the A1 +16 / O3 +8 validation (see
`../FREEZE.md`), seed 20261015, 199 frames.

For each row in `labels.csv`, inspect `images/<sample_id>.png` or the contact
sheets. Set `hud_visible` to `visible`, `hidden`, or `?`. Set each `a1..a4`
and `o1..o4` to `x`, `alive`, or `?`. Use `?` whenever you cannot determine
the slot; ambiguity is a result, not an error. Dark-silhouette icons are
labeled `x`. Do not open `key.json` before labels are sealed.

Each image shows the top HUD, then the ally row enlarged, then the opponent
row enlarged. A = ally row, O = opponent row, slots left-to-right. Do not add,
delete, reorder, or replace samples.
