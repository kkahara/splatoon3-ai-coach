# Review SPECIAL_USE fusion contract

A detected SPECIAL_USE glyph (`SpecialUseEvent` in `review_timeline.json`) is a
frozen observation: the timing lane was validated by gate D and tagged
`special-use-timing-frozen`. This contract defines what one glyph means once it
is fused with the other Review evidence of the **same match**: roster lamps,
own death glyphs and own death episodes.

The output is one `SpecialUseFact` per glyph in `special_use_facts.json`, next
to `review_timeline.json`, built by `review/special_fusion.py`
(`s3-coach review special-facts <import_dir>`). The import itself is never
rewritten.

## Scope

- **Review clock only.** No mapping to gameplay video time, `GameClock`,
  `PlayerLifecycle`, `GameEvent`s or `DEATH_EPISODE`.
- **Slots, not players.** Review names slots `top_1..4` / `bottom_1..4`. It has
  no notion of which slot is the local player; no field, enum or reason here
  says "user".
- **Not consumed downstream.** `analysis/` and `coach/` do not read these facts
  until a separate integration assignment. `coach.roster_evidence` stays
  `false`.

## Fact fields

### Observation (always stands)

Copied from the glyph unchanged: `special_index` (position in
`special_events`), `slot_id`, `team_row`, `special_id`, `score`,
`runner_up_margin`, `template_id`, `x_norm`, `glyph_x` (px on the 1920-wide
frame). These are recorded facts whatever `integration_valid` says.

### Clock

- `remaining_estimate` is the semantic name exposed by `SpecialUseFact`: the
  value is the **remaining** game clock at the glyph centre.
- It is copied from `SpecialUseEvent.elapsed_estimate`. That frozen dataset
  field keeps its name; nothing in `ReviewTimelineDataset` is renamed.
- `phase` is `regulation` or `overtime`; there is no `countdown` value. A
  SPECIAL_USE cannot occur before GO, and this is **enforced, not assumed**:
  fact construction fails closed (`OutsidePlayableInterval`, no artifact
  written) unless the glyph lies in the playable Review interval.
  - `remaining_estimate > regulation_seconds`: before GO, refused.
  - `0 <= remaining_estimate <= regulation_seconds`: `regulation`.
  - `remaining_estimate < 0`: the pre-GO countdown (`-M:SS`) is also negative
    but is not on the match axis, so a negative value is `overtime` only if
    overtime labels (`+M:SS`) were observed and the value is no more than one
    display second past the most negative of them. Otherwise refused.
- Glyph px and clock both come from the import's axis:
  `remaining_estimate == axis.elapsed_at(x_norm)` and `glyph_x = x_norm *
  1920`. Construction refuses an import whose cursor `x` is not
  `x_norm * 1920` (the X = 0 rule compares the two).

### Slot state at use (validated X = 0 rule)

`slot_state_at_use` is `alive`, `dead` or `unknown` for the slot that used the
special, from `review/slot_state.py` (a parity-tested port of the rule
validated by gates B+C and D). Let `g` be the glyph centre in px:

- the slot's lamp frames are merged per cursor x (mixed reads → unknown);
- the last known frame at `x <= g` and the first at `x > g` must both lie
  within `SEARCH_PX` (40 px), else `unknown` / `no_lamp_frames`;
- if they disagree, or `g` sits on a lamp transition, `unknown` /
  `near_roster_transition`;
- otherwise the agreed state, `slot_state_reason = null`.

`slot_state_reason` is exactly what the algorithm returns. Fusion has **no**
disconnected-player classifier: a blank or disconnected-looking slot is
`no_lamp_frames` only if the algorithm finds no usable lamp frame; if stray
lamp reads exist, whatever the algorithm returns stands.

`alive` is the expected case. `dead` is a contradiction between the glyph and
the lamp; it is kept and blocked from integration, never "resolved".

### Own-transition context (raw numbers only)

All in Review-clock seconds, match-forward, for the same slot. The source
values are **remaining** time: later in the match is a *smaller* value. Tests
named `test_direction_*` pin this; do not "fix" the inequalities.

- `own_transition_distance_s`: px distance from the glyph to the nearest lamp
  transition bracket `(x_a, x_b]` of the slot (`slot_state.nearest_transition_px`;
  0 inside a bracket, nearest of several wins), converted with the axis
  mapping `|elapsed_at((g + px) / 1920) - elapsed_at(g / 1920)|`, which equals
  `|slope| * px / 1920`. `null` without a transition or without axis
  calibration (the slot state still stands).
- `seconds_to_next_own_death`: `remaining_estimate - death.elapsed_estimate`
  for the nearest own death glyph at or after the use, i.e. with
  `death.elapsed_estimate <= remaining_estimate`
  (`next_own_death_marker_id`); `null` if none. Never extrapolated. Example:
  use at 100, death at 95 gives 5; a death at 105 is earlier and ignored.
- `seconds_since_own_recovery_observed`: `next_alive_elapsed -
  remaining_estimate` for the latest own death episode whose first alive
  observation is at or before the use (`next_alive_elapsed >=
  remaining_estimate`). Example: recovery at 120, use at 100 gives 20; a
  recovery at 90 is later and ignored. Integer-second resolution: the real
  respawn lies between `last_dead_elapsed` and `next_alive_elapsed`. `null` if
  none.

No windows and no "near death" flag. Thresholds belong to the coaching layer.

### Identity

`identity_status`:

- `label_confirmed_type`: `special_id` is in `LABEL_CONFIRMED_SPECIALS`, the
  frozen list of types label-confirmed in the tuning set
  (`analysis/review_validation/heldout/sync/REPORT.md`, special-type coverage).
- `unconfirmed_type`: any other detected type (reefslider,
  splattercolor_screen, super_chump, tenta_missiles, zipcaster, ...).
- `unknown_type`: `special_id == "unknown"`.

This is the status of the **type**, never "correct identity" of this glyph.

### Side

`ally` / `opponent` from `TeamIdentity.ally_row` versus `team_row`; `unknown`
when `ally_row` is not set.

### Integration validity (narrow)

`integration_valid` says only whether the fact may flow to a later integration
step. It is `true` iff `integration_blockers` is empty. Closed blocker set:

| Blocker | When |
|---|---|
| `slot_dead_at_use` | `slot_state_at_use == "dead"` |
| `slot_state_unknown` | `slot_state_at_use == "unknown"` |
| `unknown_type` | `identity_status == "unknown_type"` |

It does not judge the observation. Blocked facts stay in the artifact with
every field.

## Forbidden

- Judgments: "wasted", "should have used", "good/bad special", "trade",
  "fight won", "caused".
- `SPECIAL_USED` / `SPECIAL_READY` GameEvents or any GameEvent.
- Interpolated state; extrapolated death or respawn times.
- Inferring which slot is the local player; "user" in any name.
- `fact_valid` or any whole-record validity judgment.
- Roster counts or other slots' states (`coach.roster_evidence` stays `false`).
