# Review to gameplay alignment diagnostics

## 17-42-59

- pair: consistent []
- fit: calibrated (None); c (315.5, 316.0) width 0.5; edges 172/172; free slope 1.0
- glyphs: {'aligned': 25}
- roster_x: {"counts": {"unassociated": 18, "used": 10, "ambiguous": 2}, "used": 10, "contains_zero_share": 0.3, "median_residual_midpoint": -0.8517, "median_abs_residual_midpoint": 2.185, "median_half_width": 0.6229, "direction": "negative"}
- local_death: {"counts": {"unassociated": 1}, "used": 0, "direction": "insufficient"}
- roster-X onsets excluded (wide bracket): 0

## 19-47-46

- pair: consistent []
- fit: calibrated (None); c (348.5, 349.0) width 0.5; edges 117/119; free slope 1.0
- glyphs: {'aligned': 8}
- roster_x: {"counts": {"unassociated": 19, "used": 3, "ambiguous": 3}, "used": 3, "contains_zero_share": 0.0, "median_residual_midpoint": -3.1514, "median_abs_residual_midpoint": 5.3886, "median_half_width": 0.5924, "direction": "negative"}
- local_death: {"counts": {"unassociated": 2}, "used": 0, "direction": "insufficient"}
- roster-X onsets excluded (wide bracket): 3

## 19-59-26

- pair: consistent []
- fit: calibrated (None); c (319.5, 320.0) width 0.5; edges 77/77; free slope 1.0
- glyphs: {'aligned': 6}
- roster_x: {"counts": {"unassociated": 12, "used": 6, "ambiguous": 2}, "used": 6, "contains_zero_share": 0.3333, "median_residual_midpoint": -0.7494, "median_abs_residual_midpoint": 2.7494, "median_half_width": 0.5296, "direction": "negative"}
- local_death: {"counts": {"unassociated": 2}, "used": 0, "direction": "insufficient"}
- roster-X onsets excluded (wide bracket): 5

## 20-17-32

- pair: consistent []
- fit: calibrated (None); c (317.5, 318.0) width 0.5; edges 254/255; free slope 1.0
- glyphs: {'aligned': 47}
- roster_x: {"counts": {"used": 19, "ambiguous": 30, "unassociated": 2}, "used": 19, "contains_zero_share": 0.7895, "median_residual_midpoint": 0.428, "median_abs_residual_midpoint": 0.4386, "median_half_width": 0.6914, "direction": "within_uncertainty"}
- local_death: {"counts": {"used": 2, "ambiguous": 5}, "used": 2, "contains_zero_share": 0.0, "median_residual_midpoint": 1.5121, "median_abs_residual_midpoint": 3.6927, "median_half_width": 0.6914, "direction": "insufficient"}
- roster-X onsets excluded (wide bracket): 0

## 20-25-22

- pair: consistent []
- fit: calibrated (None); c (319.5, 320.0) width 0.5; edges 284/284; free slope 1.0
- glyphs: {'aligned': 47}
- roster_x: {"counts": {"ambiguous": 29, "unassociated": 4, "used": 17}, "used": 17, "contains_zero_share": 0.9412, "median_residual_midpoint": 0.448, "median_abs_residual_midpoint": 0.448, "median_half_width": 0.6889, "direction": "within_uncertainty"}
- local_death: {"counts": {"ambiguous": 2}, "used": 0, "direction": "insufficient"}
- roster-X onsets excluded (wide bracket): 2

## delta = 0 test

```json
{
  "per_match": [
    {
      "label": "17-42-59",
      "roster_x": "negative",
      "local_death": "insufficient"
    },
    {
      "label": "19-47-46",
      "roster_x": "negative",
      "local_death": "insufficient"
    },
    {
      "label": "19-59-26",
      "roster_x": "negative",
      "local_death": "insufficient"
    },
    {
      "label": "20-17-32",
      "roster_x": "within_uncertainty",
      "local_death": "insufficient"
    },
    {
      "label": "20-25-22",
      "roster_x": "within_uncertainty",
      "local_death": "insufficient"
    }
  ],
  "matches_with_both_references_usable": 0,
  "nonzero_delta_supported": false,
  "decision": "delta_stays_0"
}
```
