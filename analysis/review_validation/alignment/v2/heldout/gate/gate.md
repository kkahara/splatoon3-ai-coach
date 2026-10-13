# Alignment gate: FAIL

## Criteria

- all_pairs_consistent_and_calibrated: pass
- roster_x_contains_zero_share: FAIL
- roster_x_median_abs_residual: pass
- unknown_values_carry_declared_reason: pass
- roster_x evidence sufficient: True

## Roster-X pooled (pass reference)

```json
{
  "used": 55,
  "counts": {
    "unassociated": 55,
    "used": 55,
    "ambiguous": 66
  },
  "contains_zero_share": 0.6545,
  "median_abs_residual_midpoint": 0.5371
}
```

## Reported only (not pass criteria)

```json
{
  "local_death_pooled": {
    "used": 2,
    "counts": {
      "unassociated": 5,
      "used": 2,
      "ambiguous": 7
    },
    "contains_zero_share": 0.0,
    "median_abs_residual_midpoint": 3.6927
  },
  "glyph_rows": {
    "17-42-59": {
      "bottom": 11,
      "top": 14
    },
    "19-47-46": {
      "top": 8
    },
    "19-59-26": {
      "top": 6
    },
    "20-17-32": {
      "bottom": 20,
      "top": 27
    },
    "20-25-22": {
      "bottom": 22,
      "top": 25
    }
  },
  "negative_controls": [
    {
      "label": "17-42-59 x en-live-playerview-2026-10-12 19-47-46",
      "pair_status": "consistent",
      "reasons": [],
      "structurally_rejected": false,
      "roster_x_pooled": {
        "used": 9,
        "counts": {
          "unassociated": 13,
          "used": 9,
          "ambiguous": 3
        },
        "contains_zero_share": 0.0,
        "median_abs_residual_midpoint": 2.3217
      }
    },
    {
      "label": "19-47-46 x en-live-playerview-2026-10-12 19-59-26",
      "pair_status": "consistent",
      "reasons": [],
      "structurally_rejected": false,
      "roster_x_pooled": {
        "used": 4,
        "counts": {
          "unassociated": 13,
          "used": 4,
          "ambiguous": 3
        },
        "contains_zero_share": 0.25,
        "median_abs_residual_midpoint": 3.6361
      }
    },
    {
      "label": "19-59-26 x en-live-playerview-2026-10-12 20-17-32",
      "pair_status": "consistent",
      "reasons": [],
      "structurally_rejected": false,
      "roster_x_pooled": {
        "used": 3,
        "counts": {
          "unassociated": 47,
          "ambiguous": 1,
          "used": 3
        },
        "contains_zero_share": 0.0,
        "median_abs_residual_midpoint": 2.0488
      }
    },
    {
      "label": "20-17-32 x en-live-playerview-2026-10-12 20-25-22",
      "pair_status": "inconsistent",
      "reasons": [
        "mode_mismatch"
      ],
      "structurally_rejected": true,
      "roster_x_pooled": {
        "used": 0,
        "counts": {
          "unassociated": 50
        }
      }
    },
    {
      "label": "20-25-22 x en-live-playerview-2026-10-12 17-42-59",
      "pair_status": "inconsistent",
      "reasons": [
        "mode_mismatch",
        "overtime_mismatch"
      ],
      "structurally_rejected": true,
      "roster_x_pooled": {
        "used": 0,
        "counts": {
          "unassociated": 30
        }
      }
    }
  ]
}
```
