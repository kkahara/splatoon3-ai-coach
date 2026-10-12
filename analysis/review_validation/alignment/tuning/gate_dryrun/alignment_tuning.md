# Review to gameplay alignment diagnostics

## 23-43-31

- pair: consistent []
- fit: calibrated (None); c (319.0, 319.5) width 0.5; edges 288/288; free slope 1.0
- glyphs: {'aligned': 46, 'unknown': 4}
- roster_x: {"counts": {"unassociated": 5, "used": 16, "ambiguous": 30}, "used": 16, "contains_zero_share": 0.9375, "median_residual_midpoint": 0.3502, "median_abs_residual_midpoint": 0.3502, "median_half_width": 0.6289, "direction": "within_uncertainty"}
- local_death: {"counts": {"ambiguous": 3, "used": 1, "unassociated": 1}, "used": 1, "contains_zero_share": 1.0, "median_residual_midpoint": 0.3637, "median_abs_residual_midpoint": 0.3637, "median_half_width": 0.6289, "direction": "insufficient"}
- roster-X onsets excluded (wide bracket): 2

## 23-56-41

- pair: consistent []
- fit: calibrated (None); c (319.0, 319.5) width 0.5; edges 286/286; free slope 1.0
- glyphs: {'aligned': 25}
- roster_x: {"counts": {"used": 11, "unassociated": 36, "ambiguous": 15}, "used": 11, "contains_zero_share": 0.9091, "median_residual_midpoint": 0.3832, "median_abs_residual_midpoint": 0.3832, "median_half_width": 0.7107, "direction": "within_uncertainty"}
- local_death: {"counts": {"unassociated": 5}, "used": 0, "direction": "insufficient"}
- roster-X onsets excluded (wide bracket): 0

## 18-18-00

- pair: consistent []
- fit: calibrated (None); c (318.0, 318.5) width 0.5; edges 284/284; free slope 1.0
- glyphs: {'aligned': 13}
- roster_x: {"counts": {"used": 12, "unassociated": 36, "ambiguous": 3}, "used": 12, "contains_zero_share": 0.5833, "median_residual_midpoint": 0.6434, "median_abs_residual_midpoint": 0.6618, "median_half_width": 0.7165, "direction": "within_uncertainty"}
- local_death: {"counts": {"used": 2, "ambiguous": 1}, "used": 2, "contains_zero_share": 1.0, "median_residual_midpoint": 0.2366, "median_abs_residual_midpoint": 0.2366, "median_half_width": 0.7165, "direction": "insufficient"}
- roster-X onsets excluded (wide bracket): 1

## 18-25-16

- pair: consistent []
- fit: calibrated (None); c (197.5, 198.0) width 0.5; edges 171/171; free slope 1.0
- glyphs: {'aligned': 19}
- roster_x: {"counts": {"unassociated": 2, "used": 8, "ambiguous": 13}, "used": 8, "contains_zero_share": 1.0, "median_residual_midpoint": 0.1046, "median_abs_residual_midpoint": 0.1057, "median_half_width": 0.6373, "direction": "within_uncertainty"}
- local_death: {"counts": {"ambiguous": 3}, "used": 0, "direction": "insufficient"}
- roster-X onsets excluded (wide bracket): 0

## 20-33-31

- pair: consistent []
- fit: calibrated (None); c (312.0, 312.5) width 0.5; edges 171/171; free slope 1.0
- glyphs: {'aligned': 14}
- roster_x: {"counts": {"ambiguous": 10, "unassociated": 26, "used": 5}, "used": 5, "contains_zero_share": 1.0, "median_residual_midpoint": 0.2387, "median_abs_residual_midpoint": 0.2387, "median_half_width": 0.5835, "direction": "within_uncertainty"}
- local_death: {"counts": {"unassociated": 6}, "used": 0, "direction": "insufficient"}
- roster-X onsets excluded (wide bracket): 0

## delta = 0 test

```json
{
  "per_match": [
    {
      "label": "23-43-31",
      "roster_x": "within_uncertainty",
      "local_death": "insufficient"
    },
    {
      "label": "23-56-41",
      "roster_x": "within_uncertainty",
      "local_death": "insufficient"
    },
    {
      "label": "18-18-00",
      "roster_x": "within_uncertainty",
      "local_death": "insufficient"
    },
    {
      "label": "18-25-16",
      "roster_x": "within_uncertainty",
      "local_death": "insufficient"
    },
    {
      "label": "20-33-31",
      "roster_x": "within_uncertainty",
      "local_death": "insufficient"
    }
  ],
  "matches_with_both_references_usable": 0,
  "nonzero_delta_supported": false,
  "decision": "delta_stays_0"
}
```
