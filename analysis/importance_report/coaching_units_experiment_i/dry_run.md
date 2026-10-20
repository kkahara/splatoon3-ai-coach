# Experiment I dry run (no LLM calls)

- Provider / model: `cursor` / `gpt-5.6-luna`; temperature **unsupported** (never sent: the Cursor SDK has no sampling field (AgentOptions has none; model params are context / reasoning / fast only), and the SDK takes one joined message, no separate system prompt)
- Derived facts vs frozen design: none
- Stale inputs (byte comparison with a rebuild): none
- Invariants (A = production = Experiment H arm A; M = A + map_awareness, claim only when eligible; system M = A + section): all hold
- Calls: 88 (2 repeats); eligible deaths 14 of 22; model calls made: 0
- System prompt chars: A 16204, M 17004

## Validator smoke test (synthetic outputs, frozen before any call)

| case | view | production hits | R5 window exempt | adjusted | rules hit | map-rule violations | value errors | mentions map |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| intended | M (eligible) | 1 | 1 | 0 | R5:map_absence | - | 0 | True |
| intended | M without claim | 3 | 1 | 2 | R10:ungrounded_template, R5:map_absence, R7:unknown_claim | - | 0 | True |
| causal | M (eligible) | 2 | 1 | 1 | R5:map_absence, causal_map_claim | causal | 0 | True |
| map_content | M (eligible) | 0 | 0 | 0 |  | map_content | 0 | True |
| wrong_seconds | M (eligible) | 1 | 1 | 0 | R5:map_absence | - | 1 | True |
| advice_not_eligible | M (not_eligible) | 1 | 0 | 1 | R10:ungrounded_template | advice_not_eligible | 0 | True |

## Amendments (before any call)

1. (2026-10-20) At stop point 2, before any model call, the production validator was run on synthetic arm-M outputs. Two production rules encode the respawn-map concept this experiment replaces. (a) Map advice is an unsupported recommendation (and fails R10) unless a claim grounds rec_map_after_respawn: as pre-registered, the experiment-only claim no_map_check_since_back_in_play is added to arm M's claim_basis on eligible deaths, grounding that production template. (b) R5 map_absence flags any 'no map check' sentence unless map.map_check_before_death.status is not_observed, and that status counts the respawn map, so the frozen arm-M statement is a validator hit by construction. The validator is not changed; each record keeps the production count. A second, adjusted count removes only R5 map_absence hits where the call's own map_awareness block is non-null with map_checks_since_back_in_play == 0 and the sentence ties the absence to the back-in-play window ('since', back in play, respawn, return, rejoin, came back, or the seconds_back_in_play value). Arm A has no block, so the adjustment cannot apply to it. The safety gate uses the adjusted count; both are reported. No output existed; gates and thresholds are unchanged. Approved by the rater before any call.
2. (2026-10-20) Building the views showed that the death table and the frozen observability rule disagree on 2026-10-11 20-33-31 death_episode:199.500: its episode map coverage is insufficient (no respawn overlay detected for its own respawn), so the rule makes its map_awareness block null with no map-check coaching, while the table lists it as eligible. The rater chose the rule. Eligible 14, not eligible 8 (the derived facts are unchanged and still reproduce design.json). The uptake gate keeps its 50% rate: at least 14 of 28 eligible arm-M outputs. The value gate uses the 14 eligible deaths; all other gates and thresholds are unchanged. No output existed.

## Deaths

| match | death | back in play from | seconds back | alive checks | eligible | A chars | M chars |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-10-11 18-18-00 | `death_episode:197.500` | previous_respawn | 74.5 | 0 | Y | 9259 | 9983 |
| 2026-10-11 18-25-16 | `death_episode:175.500` | previous_respawn | 81.5 | 1 | N | 8510 | 8738 |
| 2026-10-11 18-25-16 | `death_episode:85.000` | match_start | 66.0 | 1 | N | 8190 | 8413 |
| 2026-10-11 20-33-31 | `death_episode:151.000` | previous_respawn | 19.0 | 0 | Y | 8044 | 8768 |
| 2026-10-11 20-33-31 | `death_episode:180.500` | previous_respawn | 21.5 | 1 | N | 7808 | 8035 |
| 2026-10-11 20-33-31 | `death_episode:199.500` | null block | None | None | N | 8728 | 8749 |
| 2026-10-11 20-33-31 | `death_episode:37.500` | match_start | 24.0 | 0 | Y | 8001 | 8720 |
| 2026-10-11 22-20-42 | `death_episode:152.000` | previous_respawn | 4.0 | 0 | N | 9374 | 9601 |
| 2026-10-11 22-31-53 | `death_episode:208.500` | previous_respawn | 18.0 | 0 | Y | 9388 | 10112 |
| 2026-10-11 22-37-47 | `death_episode:210.500` | previous_respawn | 143.5 | 1 | N | 9363 | 9592 |
| 2026-10-11 22-37-47 | `death_episode:301.500` | previous_respawn | 81.5 | 0 | Y | 8932 | 9656 |
| 2026-10-11 22-37-47 | `death_episode:58.000` | match_start | 16.0 | 0 | Y | 9903 | 10622 |
| 2026-10-11 22-57-30 | `death_episode:106.500` | previous_respawn | 33.0 | 0 | Y | 9849 | 10573 |
| 2026-10-11 22-57-30 | `death_episode:176.500` | previous_respawn | 25.0 | 0 | Y | 10438 | 11162 |
| 2026-10-11 22-57-30 | `death_episode:244.000` | previous_respawn | 56.5 | 0 | Y | 9871 | 10595 |
| 2026-10-11 22-57-30 | `death_episode:278.500` | previous_respawn | 23.5 | 0 | Y | 10040 | 10764 |
| 2026-10-11 22-57-30 | `death_episode:308.000` | previous_respawn | 20.5 | 0 | Y | 10070 | 10794 |
| 2026-10-11 22-57-30 | `death_episode:49.500` | match_start | 32.0 | 1 | N | 10323 | 10546 |
| 2026-10-12 20-17-32 | `death_episode:179.500` | previous_respawn | 19.5 | 0 | Y | 9699 | 10423 |
| 2026-10-12 20-17-32 | `death_episode:221.000` | previous_respawn | 32.5 | 0 | Y | 9417 | 10141 |
| 2026-10-12 20-17-32 | `death_episode:257.500` | previous_respawn | 28.0 | 0 | Y | 8684 | 9408 |
| 2026-10-12 20-17-32 | `death_episode:65.500` | previous_respawn | 7.0 | 0 | N | 10151 | 10378 |
