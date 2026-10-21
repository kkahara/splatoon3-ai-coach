# N2 citation-compliance experiment: design (pre-registered)

Question: Under the rewritten production prompt, does the model reliably cite supplied evidence using literal scalar CoachLlmView (N2) paths with correct values, without falling back to legacy source_path / CoachInput / hybrid forms?

Citation-interface experiment only: no usefulness or value rating. Experiment H / I artifacts, ratings, validations and determinations are not used or modified. The validator's legacy label fallback is unchanged; it is measured, never counted as compliance.

## Frozen setup

- Code: design computed at `3a3121b8f78052c9a876e823d5cc350878b3645c`; production paths (`src/`, `configs/default.yaml`) unchanged since prompt commit `3a3121b8f78052c9a876e823d5cc350878b3645c`
- Provider / model: `cursor` / `gpt-5.6-luna` (production: `S3_COACH_LLM_PROVIDER=cursor`, config `cursor_model`); temperature unsupported (never sent: the Cursor SDK has no sampling field (AgentOptions has none; model params are context / reasoning / fast only), and the SDK takes one joined message, no separate system prompt)
- System prompt sha256 `e6af339a94732cb16aa54310906b9925b764626a3d38710844c10fd3d30cf8db` (16628 chars, `roster_evidence=false`)
- User preamble sha256 `bf5751e3cdcbf283635945a33439166a03508535db2c49beb8aff626761be3e2`; config sha256 `e0b96fb6ac4d747a74e33a835ecba1effdf32317428125421c7c68799c01945a`
- `LLM_VIEW_SCHEMA_VERSION` 3, `COACH_EVIDENCE_VERSION` 5
- Views equal `s3-coach coach-inputs` output: 12 / 12
- Calls: 12 units x 2 repeats = 24

## Selection

Rule: (a) the 7 deaths whose Experiment I old-prompt outputs carried the historical legacy / object-path citations (named here, rebuilt under current production; Experiment I prompts and outputs are not used); (b) every local death view with low_ink evidence; (c) every local death view with two related units. (b) and (c) are checked against a census of all local death views. Census: 56 death views over 14 local matches.

| # | match | death | group | why selected | families | view sha256 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2026-10-11 18-25-16 | `death_episode:175.500` | experiment_i_history | Experiment I old-prompt outputs on this death cited game_clock_samples[label=death].observation.seconds_remaining; final 30s claim | clock, timeline, special, map, related 0, claims 5, source_path 13 | `db45ff55ee0fc4b4` |
| 2 | 2026-10-11 20-33-31 | `death_episode:199.500` | experiment_i_history | Experiment I old-prompt outputs on this death cited durations.time_since_previous_death and timeline.time_since_previous_death (no .value); quick re-death | clock, timeline, special, map, related 0, claims 6, source_path 12 | `478d372c2c88dd76` |
| 3 | 2026-10-11 22-20-42 | `death_episode:152.000` | experiment_i_history | Experiment I old-prompt outputs on this death cited durations.time_since_previous_death; quick re-death, related engagement | clock, timeline, special, map, related 1, claims 6, source_path 16 | `95dbc6b405e34ca4` |
| 4 | 2026-10-11 22-31-53 | `death_episode:208.500` | experiment_i_history | Experiment I old-prompt outputs on this death cited game_clock_samples[label=death].observation.seconds_remaining; own splat before, related leads_to | clock, timeline, special, map, related 1, claims 6, source_path 15 | `c1d045a29617515b` |
| 5 | 2026-10-11 22-37-47 | `death_episode:58.000` | experiment_i_history | Experiment I old-prompt outputs on this death cited game_clock_samples[label=death].observation.seconds_remaining; score / zone_control, special ready | clock, timeline, special, map, related 1, claims 6, source_path 16, score, zone_control | `423756a4795bb4f3` |
| 6 | 2026-10-11 22-57-30 | `death_episode:308.000` | experiment_i_history | Experiment I old-prompt outputs on this death cited game_clock_samples[label=death].observation.seconds_remaining; score / zone_control, behind in count | clock, timeline, special, map, related 0, claims 6, source_path 15, score, zone_control | `c0b1a9c368e23719` |
| 7 | 2026-10-12 20-17-32 | `death_episode:65.500` | experiment_i_history | Experiment I old-prompt outputs on this death cited durations.time_since_previous_death and timeline.time_since_previous_death (no .value); score / zone_control | clock, timeline, special, map, related 1, claims 6, source_path 17, score, zone_control | `5a6cbf01c32b68be` |
| 8 | 2026-10-10 23-43-31 | `death_episode:205.000` | low_ink | low_ink at death; related leads_to; citation-contract fixture | clock, timeline, special, map, low_ink, related 1, claims 6, source_path 16 | `15abf5596bbeecc6` |
| 9 | 2026-10-10 23-56-41 | `death_episode:46.000` | low_ink | low_ink at death; first 30s claim | clock, timeline, special, map, low_ink, related 1, claims 6, source_path 15 | `b50e8c26df75721a` |
| 10 | 2026-10-12 19-47-46 | `death_episode:80.000` | low_ink | low_ink at death; score / zone_control; 7 claims incl. map overlay false | clock, timeline, special, map, low_ink, related 1, claims 7, source_path 17, score, zone_control | `52f56546e866cf53` |
| 11 | 2026-10-10 23-43-31 | `death_episode:226.000` | related_two | two related units (one leads_to); own splat before | clock, timeline, special, map, related 2, claims 5, source_path 18 | `81431d70ebb8eeb4` |
| 12 | 2026-10-11 22-57-30 | `death_episode:142.500` | related_two | two related units (one leads_to); score / zone_control; most source_path labels | clock, timeline, special, map, related 2, claims 6, source_path 20, score, zone_control | `46a6d5c2ed459d2e` |

Full hashes (view and user prompt) are in `design.json`.

## Scoring and gate

Citation classes (one per `path = value` segment of an entry):

- `n2_scalar_correct`
- `n2_scalar_wrong_value`
- `n2_object_asserted`
- `source_path_field`
- `legacy_label`
- `n1_coachinput`
- `relative_path`
- `unresolved_path`
- `unsupported_invented`
- `malformed`

Gate (frozen; PASS only when every rule holds):

- `all_calls_parsed`: every sealed call has a parseable model reply
- `zero_unsupported_or_invented`: no citation of class unsupported_invented
- `zero_wrong_value`: no citation of class n2_scalar_wrong_value
- `zero_object_path_asserted`: no citation of class n2_object_asserted
- `zero_legacy_form`: no citation of class source_path_field, legacy_label, n1_coachinput
- `zero_malformed_or_unresolved`: no citation of class relative_path, unresolved_path, malformed
- `support_cited`: every output with a recommendation or a selected claim has at least one n2_scalar_correct citation

Gate groups:

- `unsupported_or_invented`: unsupported_invented
- `wrong_value`: n2_scalar_wrong_value
- `object_path_asserted`: n2_object_asserted
- `legacy_form`: source_path_field, legacy_label, n1_coachinput
- `malformed_or_unresolved`: relative_path, unresolved_path, malformed

Value rule: value compared by meaning at the exact location: numbers exactly (no tolerance), booleans and null by JSON value, text exactly; written form (exact / remark / lenient) is a secondary metric.

Secondary metrics (no gate):

- value written form
- prose before the path
- several facts in one entry
- selected claim cited by one of its evidence_paths
- outputs with no citation
- units compliant on every repeat
- production validator verdict and every disagreement with the strict verdict
- other validator hits (recorded only; not part of the gate)
