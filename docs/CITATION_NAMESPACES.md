# Citation namespaces (design note)

Status: proposal. Production prompt, projection and validator are unchanged.
Current violations are pinned in `tests/test_citation_contract.py` (strict
`xfail`, ids `CV-*`).

## The two namespaces

**N1, CoachInput / source paths.** Dotted paths over
`CoachInput.model_dump(mode="json")`: `primary_context.*` (ScenarioContext),
`primary_scenario.*`, `related[i].scenario.*`, `related[i].context.*`,
`durations.*`, `game_clock_samples[label=death].observation.*`,
`score_facts.*`, `zone_control_facts.*`, `playback_clock.*`. The
`[label=death]` selector is not a JSON index. This is the evidence the
coaching unit was built from.

**N2, projected view paths.** Dotted paths over
`CoachLlmView.model_dump(mode="json")`: `death.*`, `clock.*`, `timeline.*`,
`special.*`, `map.*`, `score.*`, `zone_control.*`, `low_ink.*`,
`playback.*`, `related[i].*`, `claim_basis[i].*`, `supporting_evidence[i].*`
(experiments add, e.g., `map_awareness.*`). This is what the model sees.
The projection (`coach/llm_view.py`) renames (`death_time` to
`death_time_video`), flattens (`observation.display` to `clock.display`),
merges sources into one block (death block = `death_episode` + `durations`;
score block = `score_facts`), derives (`low_ink.at_death`) and formats
(`supporting_evidence` display strings).

**`source_path` labels** are strings on view blocks meant to name the N1
origin. The validator (`resolve_evidence_reference`) accepts a citation if it
is a literal N2 path (`view_native`), or if it starts with a label and the
remainder walks the *view* block (`source_path_ref`). The second form is a
hybrid: N1 prefix, N2 remainder.

The production prompt teaches all three: "prefer the exact CoachInput path"
(N1), "prefer `source_path` values" (labels), "cite CoachLlmView paths" and
the `claim_basis` evidence paths (N2). Its examples mix them: example 1
(`primary_context.death_episode.death_to_respawn.value`) is a hybrid that
is not an N1 path (N1 `death_to_respawn` is a float), example 2 is N2,
example 3 is an N1 path no death view carries, and example 4 names an N1
field that is null in every death-unit CoachInput.

## Fix 1 results (`tests/test_citation_contract.py`)

Fixtures: the 23 claim-corpus units plus 3 real units
(`tests/fixtures/citation_contract/`), views rebuilt with current code.

Taught paths in the production prompt (15 plus `e.g`):

| path | kind | result |
| --- | --- | --- |
| `primary_context.death_episode.death_to_respawn.value` | example | resolves (5 views); not an N1 path (`CV-EXAMPLE-NAMESPACE`) |
| `death.death_to_active_again.value` | example | resolves (5); N2 path, not N1 (`CV-EXAMPLE-NAMESPACE`) |
| `primary_context.relations.leads_to_death_episode_id` | example | never resolves (`CV-NOT-PROJECTED`) |
| `primary_context.combat.trade_candidate` | example | never resolves; null in every death CoachInput (`CV-NOT-PROJECTED`) |
| `related[0].context.relations.leads_to_death_episode_id` | example | passes (17) |
| `special.state_at_anchor`, `importance.factor_thresholds`, `map.map_check_before_death`, `low_ink.at_death`, `playback.status`, `score.video_time`, `score.lookback_video_time`, `durations.time_since_previous_death.value` | field reference | pass |
| `map_check_before_death.status`, `relations.leads_to_death_episode_id` | field reference | relative; do not resolve (`CV-RELATIVE`) |

Experiment I rows: top-level `map_awareness.*` passes;
`primary_context.map_awareness.*` (`CV-MAP-AWARENESS-OPEN`),
`game_clock_samples[label=death].observation.display` (`CV-FLATTENED`),
`primary_context.special.state_at_anchor` and
`primary_context.timeline.time_since_previous_death(.value)`
(`CV-UNLABELLED`) fail.

General forms over all leaves of all 26 views:

| form | failures (classes) |
| --- | --- |
| N2 literal ("cite CoachLlmView paths") | single-segment paths uncitable (170); exact prose values rejected by R7 (437) |
| label + remainder ("source_path values") | single-segment labels (75); supporting-evidence label collisions (99); prose (5) |
| `claim_basis` evidence paths | 6 name an object, so R7 cannot check the value |
| label form vs N1 | 893 resolving citations name no N1 field; 61 contradict the N1 value at that path |
| N1 sample table (21 pairs) | 10 pass, 11 fail: renamed, flattened, unlabelled, mixed-source, support label |

## Proposed invariant

For every fact supplied in the view and every citation form the prompt
teaches for it:

1. the validator binds the citation to exactly that fact and accepts exactly
   its value; a citation of a different, absent or contradicted value is
   rejected;
2. one citation string names one fact (no label collisions);
3. a path presented as N1 names that N1 field, and its view value equals the
   N1 value (or a declared transform of it);
4. every path the prompt and `claim_basis` teach is in the inventory and
   resolves on fixture views.

## Fixes 2, 3, 4 compared

| | Fix 2: exact labels | Fix 3: N2 only | Fix 4: generated N1 to N2 map |
| --- | --- | --- | --- |
| model cites | N1 through labels (or N2) | N2 only | N1 or N2 |
| validator | label prefix, remainder must be N1-shaped: a label per renamed / flattened / merged leaf, supporting evidence stops reusing N1 labels | literal view walk; labels become audit metadata only | translates N1 to its N2 leaf, then R7 on that leaf; hybrid label paths retired |
| silent invalidation by projection change | yes if labels stay hand-written beside the projection; no if emitted by the code that copies the field | prompt prose and examples can drift from the view; the inventory test catches it | no, if the map is a by-product of projection |
| contract test enforces | label exactness (3), uniqueness (2), every leaf reachable by its N1 path | inventory (4): every taught path is an N2 leaf; no N1 wording left in the prompt | map completeness: every N2 leaf has an N1 source or is declared view-only; N1 citation binds that leaf with equal value |
| prompt change / fresh calls | label wording only; fresh calls for any behavior claim | yes: rewrite citation rule and examples | none needed for resolution; the prompt's N1 examples must still be real N1 paths |
| view schema bump | yes | no (labels may stay) | yes if provenance is emitted into the view |

Fix 4 cannot be generated from metadata today: view blocks are
`dict[str, Any]` built imperatively in the `_project_*` functions, with no
field-level provenance. Two ways to generate it, both making the map a
by-product of projection rather than a maintained table:

- projection helpers that copy a field and record `(N2 path, N1 path,
  transform)` as they build the block, returned beside the view;
- a declarative projection spec (rows of N2 path, N1 path, transform) that
  drives `_project_*` and is the map.

Derived leaves (`low_ink.at_death`), display strings (`supporting_evidence`)
and experiment blocks are declared view-only, so an N1 citation of them is
rejected rather than guessed. A manual alias table (fix 5) and value-matching
inference are rejected: both drift or guess.

## Independent of the namespace choice

These are validator correctness issues and need fixing under any of 2, 3, 4:

- **Duplicate labels / R7** (`CV-SUPPORT-LABEL`): `supporting_evidence`
  reuses `primary_context.special.nearest_before_anchor`,
  `primary_context.map.map_check_status`,
  `durations.time_since_previous_death`, `primary_scenario.start_time` and
  `game_clock_samples[label=death].observation.seconds_remaining`.
  Resolution may bind the display item while R7 walks the first block with
  the label, so a wrong value is accepted unchecked.
- **Contradictions** (`CV-CONTRADICTS-COACHINPUT`): the validator accepts
  `primary_context.death_episode.death_to_respawn = null` where CoachInput
  has 7.0 (view null when playback is unverified) and
  `primary_context.map.map_check_before_death = null` where CoachInput has
  `true`.
- **Object leaves** (`CV-OBJECT-LEAF`): `claim_basis` evidence paths that
  name a clocked-duration object; R7 skips objects.
- **Prose values** (`CV-PROSE-VALUE`) and **single-segment paths**
  (`CV-SINGLE-SEGMENT`): lower priority; prose leaves are rarely evidence.

## Open design question: map awareness

Where does the derived "seconds back in play / map checks since back in
play" fact live?

- a coaching-layer CoachInput fact (an N1 home such as `score_facts`), with
  a projected N2 block;
- view-only (N2 `map_awareness.*` only, never cited as N1);
- ScenarioContext is constrained by the evidence freeze in `AGENTS.md`.

Until this is decided, `primary_context.map_awareness.*` is neither a valid
nor an invalid N1 path: no namespace owns the fact. No label or alias for it
should be added before the decision.

## Order of work

Fix 1 (this) -> choose the authoritative namespace -> fix 2 vs 3 vs 4 ->
duplicate-label / R7 correctness separately -> map-awareness ownership ->
rating methodology -> only then consider an Experiment I successor.
