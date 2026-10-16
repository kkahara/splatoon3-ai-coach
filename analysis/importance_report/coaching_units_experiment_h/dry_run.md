# Experiment H dry run (no LLM calls)

- Provider / model: `cursor` / `gpt-5.6-luna`
- Temperature support: **unsupported** (never sent: the Cursor SDK has no sampling field (AgentOptions has none; model params are context / reasoning / fast only), and the SDK takes one joined message, no separate system prompt)
- Stale inputs (rebuilt from production, byte comparison): none
- Invariants (A has no roster block; control B = C; treatment B != C): all hold
- Calls: 132 (2 repeats); model calls made: 0

| arm | inputs | calls | user-prompt chars (per repeat) | system chars |
| --- | --- | --- | --- | --- |
| A | 22 | 44 | 204042 | 16204 |
| B | 22 | 44 | 251631 | 19167 |
| C | 22 | 44 | 253136 | 19167 |

| set | death | change | A roster | B roster | C roster | B = C |
| --- | --- | --- | --- | --- | --- | --- |
| treatment | 2026-10-11 18-25-16 `death_episode:85.000` | at_or_before_death | no roster block | before 4v4, at 4v4, window 3v4 4v4 4v4 3v4 3v4 | before 3v4, at 3v4, window 3v4 4v4 3v4 3v4 3v4 | no |
| treatment | 2026-10-11 20-33-31 `death_episode:199.500` | at_or_before_death | no roster block | before 3v4, at 2v4, window 4v4 3v4 2v4 2v4 3v3 | before 3v4, at 3v4, window 4v4 3v4 3v4 1v4 3v3 | no |
| treatment | 2026-10-11 22-20-42 `death_episode:152.000` | window_only | no roster block | before 3v3, at 3v3, window 3v4 3v4 3v3 3v3 3v4 | before 3v3, at 3v3, window 3v4 3v4 3v3 2v3 3v4 | no |
| treatment | 2026-10-11 22-37-47 `death_episode:210.500` | at_or_before_death | no roster block | before -, at -, window 3v3 - - 3v2 3v2 | before 4v3, at 4v4, window 2v3 2v3 4v4 3v2 2v2 | no |
| treatment | 2026-10-11 22-37-47 `death_episode:58.000` | window_only | no roster block | before 4v3, at 4v3, window 4v4 4v3 4v3 3v2 - | before 4v3, at 4v3, window 4v4 4v3 4v3 3v1 - | no |
| treatment | 2026-10-11 22-57-30 `death_episode:106.500` | window_only | no roster block | before 3v3, at 3v3, window 4v4 3v3 3v3 3v3 - | before 3v3, at 3v3, window 4v4 3v3 3v3 3v2 - | no |
| treatment | 2026-10-11 22-57-30 `death_episode:176.500` | window_only | no roster block | before 3v3, at 3v3, window 3v3 3v3 3v3 2v3 2v3 | before 3v3, at 3v3, window 3v3 3v3 3v3 2v3 1v3 | no |
| treatment | 2026-10-11 22-57-30 `death_episode:278.500` | window_only | no roster block | before 3v3, at 3v3, window 2v4 2v3 3v3 3v3 - | before 3v3, at 3v3, window 2v4 2v3 3v3 3v2 - | no |
| treatment | 2026-10-11 22-57-30 `death_episode:308.000` | at_or_before_death | no roster block | before 3v4, at 3v4, window 3v4 3v4 3v4 - 3v4 | before 2v4, at 2v4, window 3v4 2v4 2v4 - 3v4 | no |
| treatment | 2026-10-11 22-57-30 `death_episode:49.500` | at_or_before_death | no roster block | before 4v4, at 4v4, window 4v4 4v4 4v4 3v4 - | before 3v4, at 3v4, window 4v4 3v4 3v4 2v4 - | no |
| treatment | 2026-10-12 20-17-32 `death_episode:65.500` | window_only | no roster block | before 3v4, at 3v4, window 3v4 2v4 3v4 2v4 3v4 | before 3v4, at 3v4, window 3v4 2v4 3v4 1v4 2v4 | no |
| control | 2026-10-11 18-18-00 `death_episode:197.500` | unchanged | no roster block | before 3v3, at 3v3, window 4v4 3v3 3v3 2v3 - | before 3v3, at 3v3, window 4v4 3v3 3v3 2v3 - | yes |
| control | 2026-10-11 18-25-16 `death_episode:175.500` | unchanged | no roster block | before 3v3, at 3v3, window 3v4 3v3 3v3 2v2 3v2 | before 3v3, at 3v3, window 3v4 3v3 3v3 2v2 3v2 | yes |
| control | 2026-10-11 20-33-31 `death_episode:180.500` | unchanged | no roster block | before 2v4, at 2v4, window 3v4 3v4 2v4 1v4 2v4 | before 2v4, at 2v4, window 3v4 3v4 2v4 1v4 2v4 | yes |
| control | 2026-10-11 22-31-53 `death_episode:208.500` | unchanged | no roster block | before 4v4, at 4v3, window 2v4 4v4 4v3 3v2 3v1 | before 4v4, at 4v3, window 2v4 4v4 4v3 3v2 3v1 | yes |
| control | 2026-10-11 20-33-31 `death_episode:151.000` | unchanged | no roster block | before 4v3, at 4v3, window 2v3 3v3 4v3 3v3 3v3 | before 4v3, at 4v3, window 2v3 3v3 4v3 3v3 3v3 | yes |
| control | 2026-10-11 20-33-31 `death_episode:37.500` | unchanged | no roster block | before 3v3, at 3v3, window 4v2 3v2 3v3 2v3 2v4 | before 3v3, at 3v3, window 4v2 3v2 3v3 2v3 2v4 | yes |
| control | 2026-10-11 22-37-47 `death_episode:301.500` | unchanged | no roster block | before 4v4, at 4v4, window 4v4 4v4 4v4 3v4 - | before 4v4, at 4v4, window 4v4 4v4 4v4 3v4 - | yes |
| control | 2026-10-11 22-57-30 `death_episode:244.000` | unchanged | no roster block | before 4v3, at 4v3, window 3v3 3v3 4v3 3v3 - | before 4v3, at 4v3, window 3v3 3v3 4v3 3v3 - | yes |
| control | 2026-10-12 20-17-32 `death_episode:179.500` | unchanged | no roster block | before 3v4, at 3v4, window 3v4 3v4 3v4 2v4 3v4 | before 3v4, at 3v4, window 3v4 3v4 3v4 2v4 3v4 | yes |
| control | 2026-10-12 20-17-32 `death_episode:221.000` | unchanged | no roster block | before 4v4, at 4v4, window 4v2 4v4 4v4 3v4 2v3 | before 4v4, at 4v4, window 4v2 4v4 4v4 3v4 2v3 | yes |
| control | 2026-10-12 20-17-32 `death_episode:257.500` | unchanged | no roster block | before 4v4, at 4v4, window 4v4 4v4 4v4 3v4 - | before 4v4, at 4v4, window 4v4 4v4 4v4 3v4 - | yes |
