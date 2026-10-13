# Experiment D dry run (no LLM calls)

- Provider / model: `cursor` / `gpt-5.6-luna`
- Requested temperature 0.0, seed 0
- Temperature support: **unsupported** (never sent: the Cursor SDK has no sampling field (AgentOptions has none; model params are context / reasoning / fast only), and the SDK takes one joined message, no separate system prompt)
- Stale inputs: none

| arm | calls | user-prompt chars | total input chars |
| --- | --- | --- | --- |
| individual | 13 | 109890 | 309453 |
| episode | 6 | 107240 | 199346 |
| episode_by_factor | 11 | 109160 | 278021 |

Total calls: 30
