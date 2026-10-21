# Citation namespaces (design note)

Status: **N2 decided** (literal CoachLlmView paths are the model-facing
citation namespace). The validator follows it. The production prompt,
projection, view contents and coaching behavior are unchanged; the prompt
rewrite (Fix 3's prompt part) is not done yet. Violations are pinned in
`tests/test_citation_contract.py` (`VIOLATIONS`, ids `CV-*`, each with a
classification and status; open ones are strict `xfail`). Literal-path
regressions live in `tests/test_claim_validation_n2.py`.

## Decision

> A model citation names the exact fact supplied in its CoachLlmView using
> that fact's literal view path, and the cited value must match the value at
> that path.

Three things, kept apart:

| | What it is | Status |
| --- | --- | --- |
| **N2 model-facing citations** | literal paths over `CoachLlmView.model_dump(mode="json")`, e.g. `death.death_time_video = 205.0`, `clock.display = "0:23"`, `related[0].scenario_type`, `claim_basis[1].evidence_paths`, top-level scalars such as `roster_evidence` | authoritative; validated by `resolve_view_path` + R7 at that location |
| **N1 provenance / audit metadata** | CoachInput paths (`primary_context.*`, `durations.*`, `game_clock_samples[label=death].observation.*`, …) naming where a view fact came from | not a model contract; may later become generated provenance beside the view (never cited by the model, never a second accepted namespace) |
| **Legacy hybrid / label behavior** | `source_path` labels on view blocks, and the validator's `source_path_ref` form (label prefix + view-shaped remainder) | kept only for stored outputs and the current prompt; scheduled for cleanup with the prompt rewrite; never consulted when the literal path exists |

Fix 4's "either namespace" model contract is not adopted. Legacy labels are
not repaired to conform to CoachInput: under N2 they are not a contract.

## The two namespaces (reference)

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
and top-level scalars (`schema_version`, `roster_evidence`, …); experiments
add, e.g., `map_awareness.*`. This is what the model sees. The projection
(`coach/llm_view.py`) renames (`death_time` to `death_time_video`), flattens
(`observation.display` to `clock.display`), merges sources into one block
(death block = `death_episode` + `durations`; score block = `score_facts`),
derives (`low_ink.at_death`) and formats (`supporting_evidence` display
strings).

**`source_path` labels** are strings on view blocks meant to name the N1
origin. The legacy form accepts a citation that starts with a label and
whose remainder walks the *view* block: N1 prefix, N2 remainder.

## Validator semantics (`coach/claim_validation.py`)

1. **Path.** If the entry starts with `path =` or `path :` and that path
   exists in the view, it is the citation path, including single-segment
   top-level keys (`roster_evidence = false`, `low_ink = null`). Otherwise
   the first dotted path in the entry is used. A single segment that is not
   a view key (`Note: …`, `durations = 12.0`) stays `malformed`.
2. **Resolution.** `resolve_view_path` walks the view literally and never
   reads labels. If it resolves, the reference is `view_native` with
   `location = path`. Only when the literal path does not exist is the
   legacy label form tried (`source_path_ref`, with the concrete view
   `location` it bound). A label that is itself a different view path is
   never used as a legacy prefix, so a label cannot capture, redirect or
   weaken a literal citation.
3. **R7.** The cited value is compared with the value at the resolved
   `location`, the same place resolution bound, not the first block that
   carries a label.
4. **Values.** A JSON string literal is decoded and compared exactly. An
   unquoted value is read up to a trailing remark (` (`, `;`, `,`); the
   full text is also tried, so prose leaves that contain those characters
   match. A sentence-final period is ignored on both sides. Numbers,
   booleans and `null` compare by value. Wrong values are still rejected.
5. **Objects and lists: leaf only.** A path denotes exactly the value at
   that location, with no implicit child. Any value asserted on an object
   or list location (`death.death_to_active_again = 8.5`, `= null`, or a
   serialized object) is an R7 hit with `check = "object_path_value"`
   (counts as a violation), for literal and legacy label citations alike.
   The scalar leaf (`death.death_to_active_again.value = 8.5`) is
   value-checked as usual. Nothing compares against `claim_basis.values`.
6. **Reference vs assertion.** A citation asserts a value only when `=` or
   `:` directly follows the path. Otherwise (`death.death_to_active_again`,
   `see death.death_to_active_again`, `death.death_to_active_again was
   8.5s`) it is a reference: it must resolve, and it is not value-checked,
   whatever the location holds. Limitation, not expanded here
   (`CV-BARE-OBJECT-REF`): a reference without `=` / `:` needs a dotted
   path, so a bare single-segment object path (`death`, `related[0]`) is
   `malformed`; with `=` / `:` it resolves and the value fails as above.

Effect on stored outputs: over the claim corpus (95 citations, 24 R7 hits)
the resolution kind and reason of every citation, the R7 hits and the
validation status are identical before and after this change, and after
the leaf-only rule (no corpus output asserts a value on an object path).

The leaf-only rule does change the result of re-validating Experiment I's
stored calls (read-only; stored validation is not rewritten). At `2dac13b`
all 88 recomputed statuses equal the stored ones. Under leaf only, 20 calls
gain one `object_path_value` hit and 15 move from `validated` to
`contains_unverified_wording` (non-blocking):

- 18 legacy label citations whose whole text is a `supporting_evidence`
  label (`game_clock_samples[label=death].observation.seconds_remaining =
  23`: 14; `durations.time_since_previous_death = 19.0`: 4). The label
  form binds the display item, an object, so its value was never checked
  (a wrong number passed too); the N2 citations are
  `clock.seconds_remaining` and `timeline.time_since_previous_death.value`.
- 2 literal `timeline.time_since_previous_death = …` (object path; the
  leaf is `.value`).

The public site recomputes this status when it renders a review, so
comparable outputs will display the non-blocking status instead of
`validated`; prose is still shown.

Re-running the
Experiment I postmortem tool into a scratch directory reproduces all 33
evidence-path rows with identical classifications; only the display of the
cited value changes (`4:35` instead of `"4:35"`, decoded JSON literal) and
the recorded head commit. Experiment I and postmortem artifacts are not
rewritten.

## Findings, classified

| id | classification | status | note |
| --- | --- | --- | --- |
| `CV-R7-LOCATION` | correctness / safety | **fixed** | R7 walked the first block with the label while resolution bound another (e.g. a `supporting_evidence` item), so a wrong value through a duplicated label passed unchecked |
| `CV-PROSE-VALUE` | correctness | **fixed** | exact prose values rejected (period stripped from one side only; quoted values cut at ` (` / `;`) |
| `CV-SINGLE-SEGMENT` | syntax limitation | **fixed** | the path grammar needed a dot, so top-level scalars and null blocks were uncitable |
| `CV-OBJECT-LEAF` | correctness / safety (design decided: leaf only) | **fixed** | R7 skipped any value asserted on an object path, so a wrong scalar passed unchecked; now `object_path_value` |
| `CV-CLAIM-OBJECT-PATH` | content (claim_basis migration) | open | 6 fixture claims name an object path while asserting scalars at its leaves; see below |
| `CV-BARE-OBJECT-REF` | syntax limitation | open | path-only reference to a single-segment object path is malformed; grammar not expanded |
| `CV-MAP-AWARENESS-OPEN` | unresolved design question | open | experiment-only fact with no N1 home |
| `CV-RELATIVE` | syntax (prompt) | open | prompt prose names fields without their block; fixed by the prompt rewrite, not the validator |
| `CV-CONTRADICTS-COACHINPUT` | legacy N1/label | open | label form `primary_context.death_episode.death_to_respawn = null` (view null: playback unverified; CoachInput 7.0) and `primary_context.map.map_check_before_death = null` (CoachInput `true`). The N2 citations `death.death_to_respawn = null` and `map.map_check_before_death = null` correctly name the supplied facts; `= 7.0` / `= true` on those paths are rejected |
| `CV-SUPPORT-LABEL` | legacy N1/label | open | `supporting_evidence` items reuse N1 labels; one label string names two facts. No longer a safety issue (R7 checks the bound location) |
| `CV-NOT-COACHINPUT`, `CV-EXAMPLE-NAMESPACE`, `CV-NOT-PROJECTED`, `CV-UNLABELLED`, `CV-FLATTENED`, `CV-RENAMED`, `CV-MIXED-SOURCE`, `CV-BARE-LABEL` | legacy N1/label | open | label/N1 exactness; irrelevant to N2 validation; removed by the prompt rewrite and label cleanup, not by aliases |

Measured counts over the 26 fixture views (Fix 1 at `db5fa8c`, recorded in
`HISTORY_DB5FA8C`, versus now):

| form | db5fa8c | now |
| --- | --- | --- |
| N2 literal, every view leaf | single-segment 170, prose 437 | 0 |
| label + remainder | single-segment 75, support label 99, prose 5 | bare label 57, bare object reference 18, support-label collision 37 |
| `claim_basis` evidence paths | object leaf 6 (value unchecked) | claim object path 6 (value rejected) |
| label form vs N1 | not-N1 893, contradicts 61 | not-N1 955, contradicts 61 |

Not-N1 rises by 62 because Fix 1 measured label citations at the first
block carrying the label (the `CV-R7-LOCATION` bug): `.label` / `.value`
of `supporting_evidence` items cited through the shared special and map
labels were missed then and are counted now. The label-form single-segment
75 are now 57 bare legacy labels (`durations`, `playback_clock`,
`game_clock_samples[label=death]`) and 18 `related[i]` labels: those name
objects, are cited as path-only references under leaf only, and hit
`CV-BARE-OBJECT-REF` (before, `related[0] = {…}` passed only because R7
skipped objects).

The seven N2 properties and their tests:

| property | tests |
| --- | --- |
| one path, one view location | `test_resolve_view_path_ignores_labels`, `test_every_view_leaf_is_a_valid_n2_citation` |
| value checked at that location | `test_duplicate_label_wrong_value_is_rejected`, `test_wrong_value_through_duplicated_label_is_rejected`, `test_wrong_value_on_any_scalar_leaf_is_rejected` |
| no pass by skipped validation | the same, plus `test_contradiction_is_legacy_label_form`, `test_value_asserted_on_object_path_fails`, `test_legacy_label_to_object_cannot_bypass` |
| explicit object rule | leaf only: `test_scalar_leaf_of_object_is_value_checked`, `test_value_asserted_on_object_path_fails`, `test_object_path_reference_without_value_is_allowed`, `test_object_path_claim_is_cited_by_its_leaves` |
| prose punctuation | `test_correct_prose_value_is_accepted`, `test_wrong_prose_value_is_still_rejected`, `test_scalar_with_trailing_remark_still_accepted` |
| every permitted scalar leaf, incl. top level | `test_top_level_scalar_paths_are_citable`, `test_single_segment_must_be_a_view_key` |
| labels never influence literal resolution | `test_literal_path_is_never_redirected_by_a_label`, `test_label_naming_another_view_path_cannot_capture_citations`, `test_legacy_label_only_when_literal_path_is_absent`, `test_no_label_shadows_a_view_path` |

## Object-valued evidence: leaf only

Decided: alternative A of the four considered (leaf only). B (canonical
child per object type), C (compare with `claim_basis.values`) and D (keep
skipping, annotate) were rejected; the literal rule gives an object path
no scalar meaning.

### claim_basis migration (`CV-CLAIM-OBJECT-PATH`, fixed)

Production `claim_basis.evidence_paths` now name the literal scalar leaf of
every value a claim carries; none needs an object-to-leaf interpretation.
Leaves follow each claim's value source, not a blanket `.value`:

| claim(s) | old path(s) | new leaf path(s) |
| --- | --- | --- |
| `death_out_of_play` | `death.death_to_active_again` | `death.death_to_active_again.value`, `death.death_to_active_again.clock` (`span.value`, `span.clock`) |
| `death_own_splat_before` | `death.time_since_previous_splat` | `death.time_since_previous_splat.video_seconds` (`own_splat_gap` is the raw video gap; `.value` is null without a verified playback clock) |
| `death_quick_redeath`, `principle_redeath_out_of_play` | `timeline.time_since_previous_death` | `timeline.time_since_previous_death.value`, `.clock` (`gap.value`, `gap.clock`) |
| `death_while_outnumbered`, `death_while_ahead_in_numbers` (roster off in production) | `roster.before_death` | `roster.before_death.ally_alive_count`, `.opponent_alive_count` |
| `death_final_30s`, `death_first_30s` | `clock.seconds_remaining` | + `clock.display` (the frame quotes `{display}`) |
| `death_opponent_counter_ticked` | `score.opponent_counter_decreased_before_death` | + `score.ally_remaining`, `score.opponent_remaining` (its values) |
| `death_low_ink`, `process_ink_before_engaging` | `low_ink.at_death` | + `death.death_time_video` and the unit's `low_ink.intervals[i].start_time` (`unit_paths`) |

Claim ids, frames, eligibility and values are unchanged. The 6 fixture
claims that named an object (`MIGRATED_CLAIMS` in
`tests/test_citation_contract.py`):

| fixture unit | claim | old path | new leaves |
| --- | --- | --- | --- |
| `contract/2026-10-10_23-43-31_death_episode_205.000` | `claim_basis[1]` `death_out_of_play` (8.5, `video`) | `death.death_to_active_again` | `.value`, `.clock` |
| `contract/2026-10-11_22-37-47_death_episode_58.000` | `claim_basis[0]` `death_out_of_play` (8.5, `video`) | `death.death_to_active_again` | `.value`, `.clock` |
| `contract/2026-10-11_22-57-30_death_episode_308.000` | `claim_basis[0]` `death_out_of_play` (17.5, `video`) | `death.death_to_active_again` | `.value`, `.clock` |
| `corpus/5d42_145.000` | `claim_basis[0]` `death_own_splat_before` (1.0) | `death.time_since_previous_splat` | `.video_seconds` |
| `corpus/ed95_156.000` | `claim_basis[0]` `death_out_of_play` (8.5, `video`) | `death.death_to_active_again` | `.value`, `.clock` |
| `corpus/ed95_72.000` | `claim_basis[1]` `death_out_of_play` (9.0, `video`) | `death.death_to_active_again` | `.value`, `.clock` |

Checked on the 26 fixture views (`test_every_claim_value_traces_to_an_evidence_leaf`)
and, once, on all 56 death views rebuilt from the local analyses: every
evidence path is a literal scalar leaf and every claim value equals the
value at one of them. The two roster claims are not exercised (roster off).

Versions: the view's shape is unchanged (`LLM_VIEW_SCHEMA_VERSION` stays 3);
`COACH_EVIDENCE_VERSION` 4 -> 5 because `claim_basis` content changed, so
existing coach-input artifacts read as stale and `coach-prototype` refuses
them without `--allow-stale`. No golden fixture stores a serialized view
(citation-contract and claim-corpus views are rebuilt from CoachInput).
Sealed Experiment H / I prompts no longer equal current production; the
tests pin that they differ only by this change (`tests/sealed_drift.py`).

## History: Fix 1 results (`db5fa8c`)

Kept as the evidence for the decision. Fixtures: the 23 claim-corpus units
plus 3 real units (`tests/fixtures/citation_contract/`), views rebuilt with
current code.

The production prompt teaches all three forms: "prefer the exact CoachInput
path" (N1), "prefer `source_path` values" (labels), "cite CoachLlmView
paths" and the `claim_basis` evidence paths (N2). Its examples mix them:
example 1 (`primary_context.death_episode.death_to_respawn.value`) is a
hybrid that is not an N1 path (N1 `death_to_respawn` is a float), example 2
is N2, example 3 is an N1 path no death view carries, and example 4 names an
N1 field that is null in every death-unit CoachInput.

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

General forms over all leaves of all 26 views at `db5fa8c`:

| form | failures (classes) |
| --- | --- |
| N2 literal ("cite CoachLlmView paths") | single-segment paths uncitable (170); exact prose values rejected by R7 (437) |
| label + remainder ("source_path values") | single-segment labels (75); supporting-evidence label collisions (99); prose (5) |
| `claim_basis` evidence paths | 6 name an object, so R7 cannot check the value |
| label form vs N1 | 893 resolving citations name no N1 field; 61 contradict the N1 value at that path |
| N1 sample table (21 pairs) | 10 pass, 11 fail: renamed, flattened, unlabelled, mixed-source, support label |

### Invariant proposed at Fix 1

For every fact supplied in the view and every citation form the prompt
teaches for it: (1) the validator binds the citation to exactly that fact
and accepts exactly its value; (2) one citation string names one fact;
(3) a path presented as N1 names that N1 field with an equal value; (4)
every taught path is in the inventory and resolves on fixture views. Under
N2, (1), (2) and (4) apply to literal view paths; (3) no longer applies to
model citations and moves to N1 provenance, if it is ever generated.

### Fixes 2, 3, 4 as compared at Fix 1

The decision takes Fix 3's namespace (N2 only, labels as audit metadata).
Its prompt rewrite is deferred. Fix 2 (exact labels) is not pursued. Fix 4's
generated map survives only as possible N1 provenance, not as a second
accepted model namespace.

| | Fix 2: exact labels | Fix 3: N2 only | Fix 4: generated N1 to N2 map |
| --- | --- | --- | --- |
| model cites | N1 through labels (or N2) | N2 only | N1 or N2 |
| validator | label prefix, remainder must be N1-shaped: a label per renamed / flattened / merged leaf, supporting evidence stops reusing N1 labels | literal view walk; labels become audit metadata only | translates N1 to its N2 leaf, then R7 on that leaf; hybrid label paths retired |
| silent invalidation by projection change | yes if labels stay hand-written beside the projection; no if emitted by the code that copies the field | prompt prose and examples can drift from the view; the inventory test catches it | no, if the map is a by-product of projection |
| contract test enforces | label exactness (3), uniqueness (2), every leaf reachable by its N1 path | inventory (4): every taught path is an N2 leaf; no N1 wording left in the prompt | map completeness: every N2 leaf has an N1 source or is declared view-only; N1 citation binds that leaf with equal value |
| prompt change / fresh calls | label wording only; fresh calls for any behavior claim | yes: rewrite citation rule and examples | none needed for resolution; the prompt's N1 examples must still be real N1 paths |
| view schema bump | yes | no (labels may stay) | yes if provenance is emitted into the view |

N1 provenance cannot be generated from metadata today: view blocks are
`dict[str, Any]` built imperatively in the `_project_*` functions, with no
field-level provenance. Two ways to generate it, both making the map a
by-product of projection rather than a maintained table: projection helpers
that record `(N2 path, N1 path, transform)` as they copy a field, or a
declarative projection spec that drives `_project_*` and is the map.
Derived leaves (`low_ink.at_death`), display strings (`supporting_evidence`)
and experiment blocks are view-only. A manual alias table and value-matching
inference are rejected: both drift or guess.

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

Fix 1 (done) -> authoritative namespace: N2 (done) -> validator correctness
for N2 (done) -> object-valued evidence: leaf only (done) -> claim_basis
evidence paths to scalar leaves (done) -> prompt rewrite to N2 -> legacy
label cleanup -> map-awareness ownership -> rating methodology -> only then
consider an Experiment I successor.
