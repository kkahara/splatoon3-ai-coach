# Experiment F dry run (no LLM calls)

- Provider / model: `cursor` / `gpt-5.6-luna`
- Temperature support: **unsupported** (never sent: the Cursor SDK has no sampling field (AgentOptions has none; model params are context / reasoning / fast only), and the SDK takes one joined message, no separate system prompt)
- Stale inputs: none
- Calls: 26

| death | reworded templates |
| --- | --- |
| public_analysis_014c50601b1541a6917ed6afb7714fbe_death_episode_176_500 | rec_final30_respawn_time, rec_redeath_reset |
| public_analysis_014c50601b1541a6917ed6afb7714fbe_death_episode_188_500 | rec_final30_respawn_time, rec_redeath_reset |
| public_analysis_1828351988f0450cb9529e914840c8f2_death_episode_206_500 | rec_final30_respawn_time |
| public_analysis_1828351988f0450cb9529e914840c8f2_death_episode_223_500 | rec_after_splat_slow_down, rec_final30_respawn_time, rec_redeath_reset |
| en_2026_09_21_23_13_21_death_episode_144_000 | rec_after_splat_slow_down |
| en_2026_09_21_23_13_21_death_episode_158_500 | rec_redeath_reset |
| en_2026_09_21_23_13_21_death_episode_327_500 | rec_final30_respawn_time |
| en_2026_09_21_23_13_21_death_episode_343_000 | rec_final30_respawn_time, rec_redeath_reset |
| ja_hammerhead_bridge_bouya_bomb_2026_09_16_19_19_16_death_episode_272_500 | rec_after_splat_slow_down |
| ja_hammerhead_bridge_bouya_bomb_2026_09_16_19_19_16_death_episode_290_500 | rec_final30_respawn_time, rec_redeath_reset |
| ja_hammerhead_bridge_tenta_missle_2026_09_16_19_12_39_death_episode_285_000 | rec_redeath_reset |
| ja_hammerhead_bridge_tenta_missle_2026_09_16_19_12_39_death_episode_298_000 | rec_final30_respawn_time, rec_redeath_reset |
| ja_hammerhead_bridge_tenta_missle_2026_09_16_19_12_39_death_episode_313_500 | rec_final30_respawn_time, rec_redeath_reset |
