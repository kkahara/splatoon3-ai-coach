# Review to gameplay alignment diagnostics

## 22-20-42

- pair: consistent []
- fit: calibrated (None); c (324.5, 325.0) width 0.5; edges 284/284; free slope 1.0
- glyphs: {'aligned': 27}
- roster_x: {"counts": {"unassociated": 27, "used": 11, "ambiguous": 18}, "used": 11, "contains_zero_share": 0.9091, "median_residual_midpoint": 0.0569, "median_abs_residual_midpoint": 0.2707, "median_half_width": 0.7107, "direction": "within_uncertainty"}
- local_death: {"counts": {"unassociated": 5}, "used": 0, "direction": "insufficient"}
- roster-X onsets excluded (wide bracket): 0

## 22-31-53

- pair: inconsistent ['clock_domain_mismatch']
- fit: calibrated (None); c (219.5, 220.0) width 0.5; edges 158/158; free slope 1.0
- glyphs: {'unknown': 11}
- roster_x: {"counts": {"unassociated": 29}, "used": 0, "direction": "insufficient"}
- local_death: {"counts": {"unassociated": 3}, "used": 0, "direction": "insufficient"}
- roster-X onsets excluded (wide bracket): 1

## 22-37-47

- pair: consistent []
- fit: calibrated (None); c (340.5, 341.0) width 0.5; edges 270/270; free slope 1.0
- glyphs: {'aligned': 50}
- roster_x: {"counts": {"unassociated": 9, "ambiguous": 28, "used": 22}, "used": 22, "contains_zero_share": 0.7727, "median_residual_midpoint": 0.4307, "median_abs_residual_midpoint": 0.493, "median_half_width": 0.6917, "direction": "within_uncertainty"}
- local_death: {"counts": {"used": 2, "ambiguous": 1}, "used": 2, "contains_zero_share": 0.5, "median_residual_midpoint": -1.6358, "median_abs_residual_midpoint": 2.0724, "median_half_width": 0.6917, "direction": "insufficient"}
- roster-X onsets excluded (wide bracket): 5

## 22-57-30

- pair: consistent []
- fit: calibrated (None); c (316.0, 316.5) width 0.5; edges 257/257; free slope 1.0
- glyphs: {'aligned': 64}
- roster_x: {"counts": {"unassociated": 4, "ambiguous": 39, "used": 23}, "used": 23, "contains_zero_share": 0.8696, "median_residual_midpoint": 0.2082, "median_abs_residual_midpoint": 0.2579, "median_half_width": 0.7169, "direction": "within_uncertainty"}
- local_death: {"counts": {"used": 4, "ambiguous": 5}, "used": 4, "contains_zero_share": 0.75, "median_residual_midpoint": 0.2115, "median_abs_residual_midpoint": 0.2115, "median_half_width": 0.7169, "direction": "within_uncertainty"}
- roster-X onsets excluded (wide bracket): 7

## delta = 0 test

```json
{
  "per_match": [
    {
      "label": "22-20-42",
      "roster_x": "within_uncertainty",
      "local_death": "insufficient"
    },
    {
      "label": "22-31-53",
      "roster_x": "insufficient",
      "local_death": "insufficient"
    },
    {
      "label": "22-37-47",
      "roster_x": "within_uncertainty",
      "local_death": "insufficient"
    },
    {
      "label": "22-57-30",
      "roster_x": "within_uncertainty",
      "local_death": "within_uncertainty"
    }
  ],
  "matches_with_both_references_usable": 1,
  "nonzero_delta_supported": false,
  "decision": "delta_stays_0"
}
```
