# Four-mode objective context

`ScenarioContext.objective` is an additive factual inspection path. Its `mode`
discriminator selects a concrete Splat Zones, Tower Control, Rainmaker, or Clam Blitz
payload. Unknown/unsupported modes leave the field null. Builders read
persisted snapshots; they never run detectors or repeat fusion.

## Mode payloads

| Mode | Payload |
| --- | --- |
| `splat_zones` | `score`, `control`, `progress`: the same objects returned by the existing legacy builders |
| `tower_control` | `control`: explicit-message state with provenance; `progress` is always null |
| `rainmaker` | Independent `counts`, `possession`, `checkpoint`; `progress` from the existing native-count adapter |
| `clam_blitz` | Fresh primary `score` and visible penalties; `progress` from primary counts only |

The legacy `score`, `zone_control`, and `objective_progress` fields retain
their existing Splat Zones behavior. No generic objective owner/control is
introduced. Tower raw pods, markers and historical checkpoint experiments are
excluded from this context.

## Sampling and provenance

Tower control, the three Rainmaker streams, and Clam scores use `at_anchor`, `pre_death`,
and `lookback`. Death episodes use the existing pre-death offset and score
lookback settings; other supported scenarios use only the anchor. Each target
selects the latest snapshot at or before it within `context_max_gap_seconds`.
There is no look-ahead, interpolation, or search past an unknown snapshot to
recover a known state. An absent/old snapshot yields null; a snapshot without
supported evidence yields unknown state or null counts. Samples are restricted
to `in_match`. Input packs retain the existing MATCH_END filtering.

Control/possession samples retain both `sample_video_time` and
`state_established_video_time`. `observed` requires the sample frame to be a
supporting frame in the establishing evidence or its grouped message display;
otherwise supported state is `held`. Repeated readable frames in one group
can therefore be observed while preserving the group's original establishing
time. Evidence retains source frames, detector IDs and available template and
language provenance. Times describe accepted observations, not exact gameplay
transition times.

Rainmaker counts remain fresh per-frame readings. Missing detector evidence
leaves counts null. Checkpoints expose only per-team `cleared | unknown` with
direct breakthrough evidence; absence never implies uncleared. Progress uses
the existing Rainmaker adapter's bounded endpoint comparisons, paired with
the existing roster plausibility contract. Count changes do not establish
possession or checkpoint state. Count units are native mode units, not
equivalent gameplay value across modes.

Clam scores use separate snapshot fields, populated only from the opt-in
`clam_score` detector in resolved in-match Clam Blitz with the HUD visible.
The fixed allied-left/opponent-right score-pod association is development
evidence from English and Japanese recordings. The samples are fresh, with
no hold, and retain the detector result ID and source frame. Unreadable scores
and penalties remain null; an absent `+N` is not a zero penalty. The Clam
adapter compares only fresh same-team primary counts within the configured
gap; a decrease produces positive progress, equality produces no progress,
and rises and long gaps remain unknown. It does not use basket state. Score and
penalty detection are development quality, not independently validated accuracy.

## Serialization and coaching boundary

`serialize_scenario_contexts` explicitly includes `objective` in analysis
artifacts (`scenario_contexts.json`); validation restores the discriminated
payload. The field is excluded from default Pydantic serialization so nested
full `CoachInput` dumps and legacy/debug prompts retain their previous shape.
The current LLM projection, scoring and claim builders do not consume it.
An explicit consumer migration is required before coaching can use this path.

For inspection/export use `serialize_scenario_contexts([context])` or dump
`context.objective` directly. Default `context.model_dump()` deliberately
contains only the legacy path. This slice changes no code under `coach/`.
