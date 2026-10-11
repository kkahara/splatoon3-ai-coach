# Review to gameplay clock alignment contract

Maps a **Review remaining-clock value** to a **gameplay video-time interval**
for one operator-declared pair: a Review import (`review_timeline.json`) and
the gameplay analysis directory (`vision_manifest.json`,
`match_identity.json`) of the **same match**. Built by
`alignment/review_alignment.py`; `s3-coach align review-clock` writes
`review_gameplay_alignment.json`. Neither input is ever rewritten.

## Scope

- **No SPECIAL_USE.** No alignment module, alignment tool or alignment gate
  reads `SpecialUseEvent`, `SpecialUseFact` or `special_events`, not even as a
  side statistic. SPECIAL_USE must not help build or judge the mapping it will
  later be evaluated through.
- **No local-player slot.** Team side (`TeamIdentity.ally_row`, from gameplay
  team colour) is team-level identity only.
- **No downstream use.** `ScenarioContext`, `CoachInput`, GameEvents and
  coaching do not consume alignment until a separate bridge assignment;
  `review/` and `coach/` do not import `alignment/`.
- No detector, review-config (`98c2bf53...`) or `GameClock` rule changes.

## Two layers: what is estimated from what

### Layer 1: `GameplayClockCalibration` (gameplay only)

Input: raw usable HUD timer reads from `coach.game_clock.build_game_clock`
(existing rules: integral reads above the timer confidence floor, never held or
smoothed values, never `300 - video_time`), restricted to frames before
`MATCH_END`.

- **HUD edge convention.** When consecutive reads at video times `t_a < t_b`
  show `k` then `k - 1`, the remaining clock crossed `k - 1` in `(t_a, t_b]`.
- With `video_t = c - remaining` (slope fixed at -1), that edge constrains
  `c ∈ (t_a + k - 1, t_b + k - 1]`.
- `c` is the interval shared by the **largest set of mutually consistent
  edges** (maximum overlap). Its width is the calibration precision.
  Inconsistent edges (OCR glitches, a recording cut) are counted.
- The rate check reuses the existing playback-rate classifier
  (`coach.playback_clock.estimate_playback_clock`); its free slope is reported.
- The Review axis plays **no part** in estimating `c`.

### Layer 2: `ReviewGameplayAlignment` (composition, no fitted offset)

Review `r = axis.elapsed_at(x_norm)` is taken to be the same game remaining
clock. With `e_axis = axis.max_absolute_error_seconds`:

```text
video_interval(r) = [c_lo - (r + delta) - e_axis, c_hi - (r + delta) + e_axis]
```

`delta` is the **cross-source clock-semantics correction** (Review label
convention versus HUD convention). It is not fitted per pair.

**Pre-registered null: `delta = 0`.** It changes only if all of:

1. the tuning corpus shows a systematic offset larger than the measurement
   uncertainty, with the same direction in every tuning match;
2. both independent death references (roster-X onset, local DEATH) agree;
   references with different display latencies separate clock semantics from
   reference latency;
3. it **replicates**: at least two distinct tuning matches each have usable
   evidence from both references and each supports the change on its own
   (with two tuning pairs, both must). An aggregate direction is not enough.

A numerical optimum alone is never adopted. Otherwise `delta = 0` stays, or
more tuning data is collected first.

## Roles of the evidence (no double use)

- **Pair check: structural only.** Declared pairing plus: `battle_mode_id`
  agreement when both sides know it, overtime agreement, HUD clock coverage,
  clock-domain compatibility. Death evidence is never part of `pair_status`.
- **Death correspondence: the only cross-source evidence.** On tuning pairs it
  tests `delta = 0`; on held-out pairs, with everything frozen, it is the
  independent validation of accuracy. It never estimates `c`.
- **Mismatched pairs are negative controls, not a PASS criterion.** Reports
  state whether the structural check rejects them. Structural consistency is
  **not proof of match identity**; two unrelated matches can pass every check.
  That is why the operator's declaration stays part of this contract. Pair
  detection is not strengthened here; automatic match identity is later work.

## Statuses (three distinct concepts)

| Status | Values | Reasons |
|---|---|---|
| `pair_status` | `consistent` / `inconsistent` | `mode_mismatch`, `overtime_mismatch`, `insufficient_clock_coverage`, `clock_domain_mismatch` |
| `fit_status` (Layer 1) | `calibrated` / `unknown` | `insufficient_gameplay_clock`, `inconsistent_gameplay_clock` |
| per-value `alignment_status` | `aligned` / `unknown` | `no_review_axis`, `pair_inconsistent`, `fit_unknown`, `outside_observed_gameplay_clock`, `overtime_no_gameplay_timer` |

- An overtime match can be `consistent` and `calibrated` while every value
  `r < 0` is `unknown` / `overtime_no_gameplay_timer`. That is not a failure
  to align the match.
- `outside_observed_gameplay_clock`: `r` is not bracketed by consistent HUD
  edges within `max_edge_gap_seconds` on both sides. No extrapolation across
  recording cuts or unobserved stretches.

### Structural checks

- **Overtime.** Review: any `+M:SS` label. Gameplay: no `MATCH_END` with
  `reason = clock_zero` and the lowest HUD read is `<= 1` (the OVERTIME banner
  replaces the digits). A knockout (lowest read `> 1`) is not overtime.
- **Coverage.** Raw HUD edges must span at least `min_coverage_fraction` of
  the Review regulation clock range.
- **Clock domain.** The highest Review regulation label and the highest HUD
  read (the frozen opening value) must agree within
  `clock_domain_tolerance_seconds`.
- **Mode.** Compared only when both sides know it. When the Review import took
  its mode from the same gameplay analysis this check is trivially true; it is
  still reported.

## Mapping output (intervals end to end)

`AlignedReviewTime` holds only the transformation: `review_remaining`,
`video_interval` (`[lo, hi]`), `video_seconds` (midpoint), `alignment_error_s`
(half-width), `alignment_status`, `reason`. There is **no** `game_remaining`.
An observed HUD value near a mapped time is validation evidence; it may appear
only in diagnostics, named `nearest_observed_game_clock`.

Later consumers answer "before or after?" from the intervals; overlapping
intervals mean `unknown`, never "whichever midpoint is earlier".

Clock direction: `r` is **remaining** time. A larger `r` is earlier in the
match and maps to an earlier video interval.

## Death-reference association (diagnostics; fixed before any residual)

Residuals are `gameplay bracket - mapped Review interval` (seconds,
interval-valued). Association never chooses by residual:

- **Local DEATH** (`GameEvent` DEATH, bracket `(start - 1/cadence, start]`):
  candidates are Review **ally-row** death glyphs whose mapped interval
  intersects the DEATH bracket widened by `association_window_seconds`. It
  means "some ally died here", never a slot assignment.
- **Roster-X onset** (gameplay `PlayerCountReading`, roster visible): a slot
  whose X is absent on one visible frame and present on the next two visible
  frames gives an onset bracket between them; brackets wider than
  `max_onset_bracket_seconds` are excluded and counted. Candidates are Review
  death glyphs on the matching row (ally to `ally_row`, opponent to the other);
  with no `ally_row`, both rows, and the onset is `ambiguous` whenever glyphs
  from both rows qualify.
- **Exactly one** candidate: its residual is used. **None**: `unassociated`.
  **Several**: `ambiguous`, excluded from residual statistics. Both are
  counted and reported.
- The window, the persistence rule and the ambiguity rule are not tuned after
  residuals are seen.

## Forbidden

- SPECIAL_USE inputs anywhere in alignment, including tools and the gate.
- "user" or a local slot.
- Interpolated or held game clock; `hud_loss` treated as a clock reading.
- Emitting any GameEvent.
- Fitting `c` from Review data or from deaths; fitting `delta` per pair.
