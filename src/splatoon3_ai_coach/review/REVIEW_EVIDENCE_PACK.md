# Review evidence pack (`review_evidence.json`)

One deterministic pack per Review import, written beside
`review_timeline.json` by `review/evidence_pack.py`
(`s3-coach review evidence-pack <import_dir>`, or
`s3-coach capture review-import <package>` for a capture package). Review
owns its construction; `capture/` only supplies acquisition provenance.

## What the pack is and is not

- **Review clock only** (`clock = "review_remaining"`). Every time is a
  remaining-clock value: regulation counts down from `regulation_seconds`,
  overtime is negative. Video time appears only as sample provenance.
- **Not an authorization.** `consumption = "not_authorized_by_inclusion"`:
  an evidence type in the pack is not thereby allowed into ScenarioContext,
  CoachInput or any other consumer. Each consumer keeps its own explicit
  allowlist (the first planned bridge allows `special_uses` only).
- No `GameEvent` (never `SPECIAL_USED` / `SPECIAL_READY`), no "user" or local
  slot, no judgments, no causal fields, no interpolation. A second without a
  sample stays a gap.
- The pack never rewrites `review_timeline.json` or `special_use_facts.json`.
  An existing pack is rebuilt only with `--replace`.

## Provenance chain

Every fact has a stable `fact_id` and `source_sample_ids`. The chain is:

1. fact → `samples[]` (`SampleRef`: `sample_id`, `image_name`,
   `image_sha256`, displayed clock, `source_video_time`, `frame_index`,
   `source_pts`);
2. → `source` (`review_timeline.json` sha256, manifest sha256,
   `source_video_identity` from `timeline_samples_manifest.yaml`);
3. → `acquisition` (capture package ID, artifact ID and path, artifact
   `video_identity`, gate contract, status at run, verdicts, `result.json`
   sha256). The builder refuses an acquisition record whose artifact
   identity differs from `source_video_identity`.

Without a package, `acquisition` is `null` and the pack carries
`no_acquisition_provenance`.

| Fact ID | Sources |
|---|---|
| `special_use:NNNN` | glyph-supporting samples; cursor-obscured samples listed separately |
| `slot_run:<slot>:NNNN` | the samples whose lamp read was chosen; for an unreadable lamp, the second's frames |
| `death_marker:<marker_id>` | glyph-supporting samples |
| `score:<second>` | per value; `controller` has none (static control segments record no samples) |

## Blocks

Each block has `validation_status` (`validated` / `limited` /
`experimental` / `not_applicable` / `unavailable`), a `validation_basis`,
and machine-readable `limitations` (closed `code` plus `detail`).

| Block | Status | Notes |
|---|---|---|
| `special_uses` | `validated` (timing) | `SpecialUseFact` unchanged (`SPECIAL_USE_FUSION.md`); type identity is label-confirmed only for `label_confirmed_type`; a refused fact set is `unavailable` with `special_facts_refused` |
| `slot_states` | `limited` | see below |
| `death_markers` | `limited` | per-row `recall_status`; `absence_is_evidence` is always `false` |
| `score_control` | `experimental` | objective modes only; Turf War is `not_applicable`, unknown mode `unavailable` |
| `coverage` | `limited` | copied from the import; `non_monotonic_capture_indexes` is not evidence |
| `team_identity` | `unavailable` without an ally row, else `experimental` | side source: `gameplay_color`, `cli` or `unknown` |

### Slot states: what "alive" means

Roster-lamp validation (184/184) covers **X-marker detection only**
(`validated_scope = "x_marker_detection"`). Slot-state runs have no gate.
`state_semantics` maps `alive` to `no_x_marker_observed`; a disconnected
slot shows no X marker and reads alive (`disconnected_slot_reads_alive`).
`connection_state` is always `not_observed`.

### Death markers: recall

Recall classification uses only previously characterized ranges from the
read-only hue diagnosis: top hue 6–82 is partial; bottom hues 0–2 and
151–167 are partial; bottom hue 120–136 is known zero recall. A measured hue
outside the ranges characterized for its row is `unknown`
(`hue_range_uncharacterized`), and an unavailable hue is also `unknown` with
`hue_not_measured`. C1–C4 never extend or retune these ranges.

`observed_glyph_count` and `diagnostic_lamp_onset_count` are retained on each
row separately. They are diagnostics only and never establish or modify
`recall_status`. No glyph never means no death.

### Side

`side` on special uses comes from `TeamIdentity.ally_row`. Without it, side
is `unknown` (`side_unknown`). A gameplay-colour ally row is
`side_from_unvalidated_calibration` (the calibration can latch on scenery
before the roster HUD, the alignment v2 failure). A declared row is
`side_operator_declared`.
