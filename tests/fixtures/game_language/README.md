# Game-language probe fixtures

Real ROI crops from permitted local recordings (no sealed F11 Arm R media, no
seven-recording corpus, no pilot pools). Each file is the exact pixel crop of
the production ROI from a 1920x1080 analysis frame; tests paste it back into a
black 1080p canvas at the same ROI.

| File | ROI | Source recording | Frame index |
|------|-----|------------------|-------------|
| `intro_en_clam_blitz.png` | `match_intro.battle_mode_roi` | `en-clam-2026-10-18 22-47-35` | 360 |
| `intro_en_rainmaker.png` | `match_intro.battle_mode_roi` | `en_rain-2026-09-20 23-25-42` | 270 |
| `intro_en_splat_zones.png` | `match_intro.battle_mode_roi` | `en-splat_zone-2026-09-21 22-48-34` | 210 |
| `intro_en_tower_control.png` | `match_intro.battle_mode_roi` | `en-tower-2026-09-27 17-26-56` | 1620 |
| `intro_en_turf_war.png` | `match_intro.battle_mode_roi` | `en-turf-2026-09-20 22-43-37` | 3270 |
| `intro_ja_clam_blitz.png` | `match_intro.battle_mode_roi` | `jp-clam-2026-09-20 23-46-44` | 810 |
| `intro_ja_rainmaker.png` | `match_intro.battle_mode_roi` | `ja-rain-2026-09-19 12-38-47` | 1680 |
| `intro_ja_splat_zones.png` | `match_intro.battle_mode_roi` | `jp_area_2026-09-18 18-37-10` | 270 |
| `intro_ja_tower_control.png` | `match_intro.battle_mode_roi` | `ja-tower-2026-09-20 14-39-55` | 480 |
| `intro_ja_turf_war.png` | `match_intro.battle_mode_roi` | `ja-turf-2026-09-20 21-17-29` | 630 |
| `callout_en_come_on.png` | `game_language.roi` | `en-rain--2026-09-10 14-20-12` | 150 |
| `callout_ja_come_on.png` | `game_language.roi` | `ja-2026-09-20 16-03-58` | 2310 |
| `callout_en_dead.png` | `game_language.roi` | `en-hagglefish_market_2026-09-15 20-18-11` | 3690 |
| `callout_ja_dead.png` | `game_language.roi` | `ja-2026-09-20 17-00-29` | 4230 |
| `callout_fr_in_match.png` | `game_language.roi` | `fr_2026-10-26 22-29-36` | 1440 |

The `*_dead` crops show that the local death text ("Ouch..." / やられた)
replaces the signal label in the same slot; `callout_fr_in_match.png` is the
French label ("Par ici!"). All three are negatives: no language is accepted.
