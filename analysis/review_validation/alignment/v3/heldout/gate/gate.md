# Alignment gate: FAIL

## Criteria

- all_pairs_consistent_and_calibrated: FAIL
- roster_x_contains_zero_share: FAIL
- roster_x_median_abs_residual: pass
- unknown_values_carry_declared_reason: pass
- roster_x evidence sufficient: True

## Roster-X pooled (pass reference)

```json
{
  "used": 134,
  "counts": {
    "unassociated": 195,
    "used": 134,
    "ambiguous": 180
  },
  "contains_zero_share": 0.7313,
  "median_abs_residual_midpoint": 0.4062
}
```

## Reported only (not pass criteria)

```json
{
  "local_death_pooled": {
    "used": 6,
    "counts": {
      "ambiguous": 21,
      "used": 6,
      "no_ally_row": 10,
      "unassociated": 14
    },
    "contains_zero_share": 0.6667,
    "median_abs_residual_midpoint": 0.2137
  },
  "glyph_rows": {
    "pair-001": {
      "bottom": 7,
      "top": 14
    },
    "pair-002": {
      "bottom": 23,
      "top": 21
    },
    "pair-003": {
      "top": 23
    },
    "pair-004": {
      "top": 23
    },
    "pair-005": {
      "bottom": 16,
      "top": 21
    },
    "pair-006": {
      "bottom": 24,
      "top": 28
    },
    "pair-007": {
      "top": 12
    },
    "pair-008": {
      "bottom": 23
    },
    "pair-009": {
      "bottom": 18,
      "top": 31
    },
    "pair-010": {
      "bottom": 19,
      "top": 32
    }
  },
  "negative_controls": [
    {
      "label": "pair-001 x pair-002",
      "pair_status": "inconsistent",
      "reasons": [
        "mode_mismatch",
        "clock_domain_mismatch"
      ],
      "structurally_rejected": true,
      "roster_x_pooled": {
        "used": 0,
        "counts": {
          "unassociated": 54
        }
      }
    },
    {
      "label": "pair-002 x pair-003",
      "pair_status": "inconsistent",
      "reasons": [
        "mode_mismatch"
      ],
      "structurally_rejected": true,
      "roster_x_pooled": {
        "used": 0,
        "counts": {
          "unassociated": 58
        }
      }
    },
    {
      "label": "pair-003 x pair-004",
      "pair_status": "consistent",
      "reasons": [],
      "structurally_rejected": false,
      "roster_x_pooled": {
        "used": 32,
        "counts": {
          "used": 32,
          "unassociated": 14,
          "ambiguous": 11
        },
        "contains_zero_share": 0.1875,
        "median_abs_residual_midpoint": 2.8304
      }
    },
    {
      "label": "pair-004 x pair-005",
      "pair_status": "consistent",
      "reasons": [],
      "structurally_rejected": false,
      "roster_x_pooled": {
        "used": 15,
        "counts": {
          "used": 15,
          "unassociated": 18,
          "ambiguous": 13
        },
        "contains_zero_share": 0.1333,
        "median_abs_residual_midpoint": 3.7738
      }
    },
    {
      "label": "pair-005 x pair-006",
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
    },
    {
      "label": "pair-006 x pair-007",
      "pair_status": "inconsistent",
      "reasons": [
        "mode_mismatch"
      ],
      "structurally_rejected": true,
      "roster_x_pooled": {
        "used": 0,
        "counts": {
          "unassociated": 38
        }
      }
    },
    {
      "label": "pair-007 x pair-008",
      "pair_status": "consistent",
      "reasons": [],
      "structurally_rejected": false,
      "roster_x_pooled": {
        "used": 6,
        "counts": {
          "unassociated": 42,
          "ambiguous": 6,
          "used": 6
        },
        "contains_zero_share": 0.1667,
        "median_abs_residual_midpoint": 1.5972
      }
    },
    {
      "label": "pair-008 x pair-009",
      "pair_status": "inconsistent",
      "reasons": [
        "mode_mismatch",
        "clock_domain_mismatch"
      ],
      "structurally_rejected": true,
      "roster_x_pooled": {
        "used": 0,
        "counts": {
          "unassociated": 64
        }
      }
    },
    {
      "label": "pair-009 x pair-010",
      "pair_status": "inconsistent",
      "reasons": [
        "mode_mismatch"
      ],
      "structurally_rejected": true,
      "roster_x_pooled": {
        "used": 0,
        "counts": {
          "unassociated": 55
        }
      }
    },
    {
      "label": "pair-010 x pair-001",
      "pair_status": "inconsistent",
      "reasons": [
        "mode_mismatch",
        "clock_domain_mismatch"
      ],
      "structurally_rejected": true,
      "roster_x_pooled": {
        "used": 0,
        "counts": {
          "unassociated": 27
        }
      }
    }
  ]
}
```
