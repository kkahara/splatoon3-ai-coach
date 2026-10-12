# Alignment gate: PASS

## Criteria

- all_pairs_consistent_and_calibrated: pass
- roster_x_contains_zero_share: pass
- roster_x_median_abs_residual: pass
- unknown_values_carry_declared_reason: pass
- roster_x evidence sufficient: True

## Roster-X pooled (pass reference)

```json
{
  "used": 52,
  "counts": {
    "unassociated": 105,
    "used": 52,
    "ambiguous": 71
  },
  "contains_zero_share": 0.8654,
  "median_abs_residual_midpoint": 0.3761
}
```

## Reported only (not pass criteria)

```json
{
  "local_death_pooled": {
    "used": 3,
    "counts": {
      "ambiguous": 7,
      "used": 3,
      "unassociated": 12
    },
    "contains_zero_share": 1.0,
    "median_abs_residual_midpoint": 0.3114
  },
  "glyph_rows": {
    "23-43-31": {
      "bottom": 23,
      "top": 27
    },
    "23-56-41": {
      "top": 25
    },
    "18-18-00": {
      "top": 13
    },
    "18-25-16": {
      "bottom": 10,
      "top": 9
    },
    "20-33-31": {
      "top": 14
    }
  },
  "negative_controls": [
    {
      "label": "23-43-31 x en-live-playerview-2026-10-10 23-56-41",
      "pair_status": "inconsistent",
      "reasons": [
        "mode_mismatch"
      ],
      "structurally_rejected": true,
      "roster_x_pooled": {
        "used": 0,
        "counts": {
          "unassociated": 62
        }
      }
    },
    {
      "label": "18-25-16 x en-live-playerview-2026-10-11 18-18-00",
      "pair_status": "inconsistent",
      "reasons": [
        "mode_mismatch",
        "clock_domain_mismatch"
      ],
      "structurally_rejected": true,
      "roster_x_pooled": {
        "used": 0,
        "counts": {
          "unassociated": 51
        }
      }
    }
  ]
}
```
