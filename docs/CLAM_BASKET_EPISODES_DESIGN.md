# Bounded Clam basket observed-open runs

Status: the analysis-layer run builder and explicit artifact writer are
implemented. There is no ScenarioContext or coaching consumer.

## Name, layer, and input

Use **`BasketOpenRun`** for the low-level artifact: it is a contiguous sequence
of observed open samples under the rules below, not proof of one uninterrupted
gameplay episode. Put the builder and versioned Pydantic models in an
`analysis/` component neighboring the existing objective-temporal work. Do not
force basket state into `ObjectiveProgressObservation`: it does not describe
objective progress. The builder consumes ordered fused `GameStateSnapshot`
observations with `basket_state` and `basket_state_evidence_ids`, plus explicit
basket observation scheduling/evaluation information. Video time is canonical.

Only resolved Clam Blitz matches participate. The artifact is not a GameEvent,
Scenario, ScenarioContext input, ranking factor, or coaching claim. Do not infer
player or team from the large black circle. Separate authorization is required
for downstream consumption.

## Scheduling is part of the input contract

A fused snapshot with `basket_state="unknown"` and no evidence IDs does **not**
by itself say whether a basket observation was scheduled. The present detector
is normally scheduled on every cadence frame, and the new field described below
persists that decision. `run_detectors` still omits a result when a detector
returns `None`, so future subsampling, incomplete manifests, and refusion must
not be guessed from detection presence alone.

The implementation must provide or derive a trustworthy per-frame status:

- `scheduled`: the detector was expected on this frame. A usable fused `open`
  or `closed` sample is an observation. An explicit unknown, failed/low-quality
  reading, or a missing expected result is an **unknown break**.
- `not_scheduled`: no basket observation was expected on this snapshot. Skip it;
  it does not break an observed-open run.
- `schedule_unknown`: the manifest does not establish whether a reading was
  expected. Do not classify it as `not_scheduled` or join runs across it;
  preserve it as a continuity break or mark the output unavailable.

The cadence boundary persists `VisionFrameResult.basket_observation_scheduled`
as `true` or `false`; `null` means scheduling provenance is unavailable. For
legacy frames only, a stored basket detector result positively establishes that
the detector was scheduled. A missing legacy result stays schedule-unknown.
A `false` flag contradicting a stored basket result is also schedule-unknown.
Never invent missed timestamps from a nominal FPS. Detector execution cadence
does not itself establish continuous basket state.

## Deterministic unknown-breaks-continuity rule

Process scheduled observations in video-time order, without extrapolation:

```text
CLOSED → OPEN → OPEN → CLOSED             one bounded open run
CLOSED → OPEN → UNKNOWN → OPEN → CLOSED   two observed-open runs
```

A missing expected basket observation or explicit/otherwise unusable unknown
breaks an observed-open run. A snapshot at which no basket observation was
scheduled does not. Each subsequent open observation after a break begins a new
run. A sequence of colored-circle → black-circle → colored-circle readings is
one run when each is observed open and no break intervenes; no extra closure is
invented. An open run is contiguous in the scheduled observation sequence; it
is not proof that the basket stayed continuously open between samples.

An unknown break does not establish a closure. It right-censors the run before
it and left-censors the next run unless a new observed closed state independently
provides the relevant bound. Never join runs merely because an unknown interval
is short or because the same team color reappears. No arbitrary maximum gap or
time-based hold is needed for this rule.

## Bounds, provenance, and censoring

A run records its first and last observed open times and supporting reading IDs.
If the immediately preceding usable scheduled state is closed, record the
opening bracket using that closed observation and the first open observation.
If the next usable scheduled state is closed without an intervening unknown
break, record the closing bracket using the last open and that closed
observation. Unscheduled snapshots are skipped for this adjacency test.

Without a qualifying preceding closed observation, the opening is left-censored;
without a qualifying following closed observation, the closing is right-censored.
Keep the observed closed timestamps and IDs for any bounded side. In the example
with an unknown, the first run's closing and second run's opening are censored;
the earlier and later closed observations may bound their respective outer sides.
Do not treat a closed sample across an unknown break as the adjacent transition
for a run. An observed transition bracket does not locate the exact transition
or rule out unobserved changes between samples.

Video start, video end, and MATCH_END do not prove a transition. Drop every
sample at or after MATCH_END; without MATCH_END, use the video domain. Retain
source sample IDs, video timestamps, mode and scheduling provenance, explicit
censoring reasons, and the unknown break that ended a run when available.
Do not compute duration or action-relative coaching facts in this slice.

## Storage and refusion

The explicit builder `analysis.basket_open_runs.write_basket_open_runs` writes
`basket_open_runs.json` under its supplied output directory. To build it from an
existing analysis, run:

```sh
python tools/diagnostics/build_basket_open_runs.py ANALYSIS_DIR --out OUTPUT_DIR
```

The artifact records schema and schedule-contract versions, source analysis ID,
mode, and a SHA-256 fingerprint of the complete source manifest. `available`
means the relevant evidence is complete, including when ordinary boundary
censoring leaves a run open at the analyzed domain edge. `partial` means useful
runs were produced but some relevant scheduling or continuity evidence is
unknown. If gaps prevent producing any useful run, the artifact is
`unavailable`, not partial. Missing mode, detector, scheduling, or
frame/snapshot provenance can also make it unavailable. Unknown or missing
expected observations always break continuity. An analysis with no relevant
scheduled observations is unavailable. Unjoinable or duplicate frame/snapshot
identities make the artifact unavailable.

This is an explicit derived operation; `analyze` and `refuse` do not silently
write or update this file. After refusion or any source-manifest change, rebuild
the artifact. `basket_open_runs_are_current` checks its fingerprint before a
future consumer uses it. A missing or stale artifact is not evidence of no runs.

## Verification plan

1. Test bounded closed→open→open→closed and split closed→open→unknown→open→closed,
   including correct outer bounds, inner censoring, break provenance, and IDs.
2. Test missing expected observations, explicit unknown, low confidence,
   overlays, and unknown schedule as breaks; test genuinely unscheduled snapshots
   as non-breaks, including manifest serialization and refusion.
3. Cover first observation open, video end, MATCH_END, no post-match evidence,
   resolved/non-Clam/unknown modes, color→black→color, deterministic ordering,
   and older manifests without trustworthy scheduling metadata.
4. Use recorded traces with expected results selected independently of the
   builder. Assert no GameEvents, Scenario ownership or intervals, ranking,
   claims, or coaching inputs change.
