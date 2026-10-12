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
  "used": 115,
  "counts": {
    "unassociated": 163,
    "used": 115,
    "ambiguous": 160
  },
  "contains_zero_share": 0.8522,
  "median_abs_residual_midpoint": 0.3337
}
```

## Reported only (not pass criteria)

```json
{
  "local_death_pooled": {
    "used": 11,
    "counts": {
      "ambiguous": 14,
      "used": 11,
      "unassociated": 17
    },
    "contains_zero_share": 0.8182,
    "median_abs_residual_midpoint": 0.2707
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
    },
    "22-20-42": {
      "top": 27
    },
    "22-31-53": {
      "top": 11
    },
    "22-37-47": {
      "bottom": 23,
      "top": 27
    },
    "22-57-30": {
      "bottom": 35,
      "top": 29
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
    },
    {
      "label": "22-20-42 x en-live-playerview-2026-10-11 22-31-53",
      "pair_status": "inconsistent",
      "reasons": [
        "mode_mismatch",
        "clock_domain_mismatch"
      ],
      "structurally_rejected": true,
      "roster_x_pooled": {
        "used": 0,
        "counts": {
          "unassociated": 29
        }
      }
    },
    {
      "label": "22-31-53 x en-live-playerview-2026-10-11 22-37-47",
      "pair_status": "inconsistent",
      "reasons": [
        "mode_mismatch",
        "clock_domain_mismatch"
      ],
      "structurally_rejected": true,
      "roster_x_pooled": {
        "used": 0,
        "counts": {
          "unassociated": 59
        }
      }
    },
    {
      "label": "22-37-47 x en-live-playerview-2026-10-11 22-57-30",
      "pair_status": "consistent",
      "reasons": [],
      "structurally_rejected": false,
      "roster_x_pooled": {
        "used": 32,
        "counts": {
          "unassociated": 23,
          "used": 32,
          "ambiguous": 11
        },
        "contains_zero_share": 0.125,
        "median_abs_residual_midpoint": 2.1947
      }
    },
    {
      "label": "22-57-30 x en-live-playerview-2026-10-11 22-20-42",
      "pair_status": "inconsistent",
      "reasons": [
        "mode_mismatch"
      ],
      "structurally_rejected": true,
      "roster_x_pooled": {
        "used": 0,
        "counts": {
          "unassociated": 56
        }
      }
    }
  ]
}
```
