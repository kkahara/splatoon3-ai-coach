# Recon polygons on gameplay overlays

2026-10-30. Read-only diagnostic requested before further polygon work.
Existing polygons and production geometry were not changed by this check.

## Conclusion

The drawn Recon polygons are useful outlines, but cannot be assumed to transfer
unchanged to gameplay overlay coordinates. All three checked stages show
misalignment. Flounder Heights is relatively close; Bluefin Depot and Inkblot
show more visible boundary errors. Normalizing vertices by image width/height
only handles image resolution; it does not handle a different map position,
scale, rotation, projection or mode layout.

There is insufficient evidence for one universal shrink factor. Do not discard
the drawings, automatically resize them, or claim strict exterior exclusion of
the actual stage until they are checked against the intended overlay layout.
The software strictly excludes the outside of the drawn polygon; that alone
does not establish that the polygon follows the gameplay stage.

## Frames examined

All images are 1920x1080. References are the corresponding PNGs in
`calibration/reference/stage_map/recon/`. Gameplay source recordings are under
`~/Movies/`. Frames were decoded at these video times and visually confirmed
to show an open map. Some initial candidates from old map observations showed
normal gameplay instead and were rejected; no fit on those frames is evidence.

| Stage | Gameplay recording | Mode from stored identity | Time |
| --- | --- | --- | ---: |
| Bluefin Depot | en-live-playerview-2026-10-12 17-42-59.mov | Splat Zones | 158.0 s |
| Flounder Heights | en-live-playerview-2026-10-12 20-25-22.mov | Tower Control | 312.5 s |
| Inkblot Art Academy | en-live-playerview-2026-10-12 20-17-32.mov | Splat Zones | 262.5 s |

A second Flounder frame at 186.0 s is included as a supplementary comparison;
its map UI is fading and it should not define a canonical stable layout.
Recon-mode identity is not established here. These are representative
coordinate-transfer checks, not matched-mode validation of stage geometry.

## Comparison sheets

Files under `analysis/map_ink_validation/recon_transfer/`:

- `bluefin_depot_comparison.jpg`
- `flounder_heights_comparison.jpg`
- `inkblot_art_academy_comparison.jpg`
- `measurements.json`

Each sheet shows: reference and drawn polygon on the left; the identical
normalized polygon on live footage in the middle; an approximate reference
registration in green on the right, with the original in thin cyan. The right
panel is diagnostic only and has not been applied to any configuration.

## Approximate registration measurements

SIFT features were matched with mutual nearest-neighbor ratio filtering at
0.7. A RANSAC similarity transform (translation, rotation, uniform scale;
3-pixel residual threshold) provides a rough estimate of coordinate mismatch.
Reference features were restricted to the drawn stage plus a 15-pixel border;
gameplay features to a central image region. This is not a full silhouette
fit, a ground-truth boundary measurement or a proposed production algorithm.

| Stage/time | Estimated live/reference scale | Rotation | Inlier matches | Median predicted vertex movement |
| --- | ---: | ---: | ---: | ---: |
| Bluefin / 158.0 | 0.974 | +1.50 degrees | 9 | 29 pixels |
| Flounder / 312.5 | 0.990 | +0.71 degrees | 11 | 14 pixels |
| Inkblot / 262.5 | 0.990 | -3.43 degrees | 9 | 73 pixels |
| Flounder / 186.0 (fading) | 1.002 | +0.14 degrees | 22 | 11 pixels |

Median inlier residuals are approximately 0.7, 1.5, 1.4 and 0.6 pixels,
respectively. Low residuals establish local landmark consistency, not accurate
registration of every outer edge. In particular, Inkblot's inliers cluster in
the upper part of the map, so its full-polygon displacement is an extrapolated
estimate. Inspect the unchanged cyan polygon on the real overlay for the
direct evidence of mismatch. The drawings' own approximation error and actual
mode-specific geometry differences are not separated by this test.

## Implications for the current drawing work

- Preserve the Recon outlines as operator-drawn source material.
- The first three new drawings (MakoMart, Wahoo World, Shipshape Cargo Co.)
  used real gameplay frames; the later sequence beginning with Bluefin used
  Recon references. Those two groups have different coordinate provenance.
- A small positional error still matters for the requested strict background
  exclusion. A blanket shrink does not resolve rotation, translation, missing
  geometry or mode variants.
- Decide between adjusting the existing outlines on real gameplay frames and
  a separately planned conversion workflow before further drawing or code.
  No conversion implementation is included in this diagnostic.
- Mahi-Mahi's currently opened Recon reference is low tide, as identified by
  the operator. Its output is `stage_mask_low_tide.yaml`; the high-tide image
  will be supplied later. Do not infer tide state from the directory name or
  register either variant for automatic selection yet.

Further polygon drawing is paused at this decision point; no object-exclusion,
color-conversion or runtime layout-selection work was started.
