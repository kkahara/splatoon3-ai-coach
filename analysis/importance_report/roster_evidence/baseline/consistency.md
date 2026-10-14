# Gameplay HUD roster consistency baseline

No labels are used here. Changes and disagreements are not called errors.
Live gameplay and replay-derived footage are never aggregated.

## Live Gameplay

- Matches: 44
- Scheduled / usable readings: 22153 / 18854
- Visibility rate: 85.1%
- Consecutive raw changes: 2780 / 18162 (15.3%)
- Change magnitude 1 / 2+: 2511 / 269
- One-frame A-B-A reversals: 136
- Implausible intervals: 44
- Deaths in implausible intervals: 10 / 172
- Pre-death sample / missing / unreliable: 161 / 0 / 11
- Even/odd 1 Hz disagreements: 22 / 172 (12.8%)
- Disagreement magnitude 1 / 2+: 19 / 3
- Matches with readings inconsistent with the current 0.03 rule: 0

## Replay Derived

- Matches: 1
- Scheduled / usable readings: 401 / 373
- Visibility rate: 93.0%
- Consecutive raw changes: 89 / 367 (24.3%)
- Change magnitude 1 / 2+: 83 / 6
- One-frame A-B-A reversals: 3
- Implausible intervals: 0
- Deaths in implausible intervals: 0 / 5
- Pre-death sample / missing / unreliable: 5 / 0 / 0
- Even/odd 1 Hz disagreements: 3 / 5 (60.0%)
- Disagreement magnitude 1 / 2+: 2 / 1
- Matches with readings inconsistent with the current 0.03 rule: 0

## Per match

| source | match | mode | readings | visible | changed pairs | reversals | implausible intervals | deaths unreliable | 1 Hz disagreements |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| live_gameplay | `9d472ec6060b4607ac1f57e3ae2b7adf` | rainmaker | 811 | 70.4% | 88/530 | 4 | 2 | 0/6 | 1/6 |
| live_gameplay | `014c50601b1541a6917ed6afb7714fbe` | turf_war | 398 | 86.9% | 44/336 | 1 | 0 | 0/5 | 1/5 |
| live_gameplay | `162a810d490d439998e804c416379b33` | splat_zones | 278 | 81.3% | 31/210 | 2 | 0 | 0/4 | 0/4 |
| live_gameplay | `1828351988f0450cb9529e914840c8f2` | turf_war | 467 | 76.4% | 44/342 | 3 | 4 | 1/4 | 1/4 |
| live_gameplay | `27bdd0218168413f94d33596e8533007` | turf_war | 465 | 80.9% | 39/360 | 5 | 1 | 0/4 | 3/4 |
| live_gameplay | `4679269a0c11412a97e38960dc4e491a` | splat_zones | 358 | 82.4% | 63/287 | 6 | 0 | 0/2 | 0/2 |
| live_gameplay | `7b3a6bcd1c554713bc0db6fefb6a769d` | turf_war | 405 | 92.6% | 41/369 | 1 | 1 | 0/1 | 0/1 |
| live_gameplay | `7cb3caf39175428ba0f834127cf8135e` | clam_blitz | 345 | 82.9% | 55/274 | 3 | 1 | 0/2 | 0/2 |
| live_gameplay | `81526af6b30f4f9c8868a8844284e6e2` | rainmaker | 692 | 88.2% | 87/591 | 2 | 1 | 0/3 | 0/3 |
| live_gameplay | `e82f4884a01a499ea0b619cce7eca05b` | tower_control | 207 | 87.0% | 22/176 | 1 | 0 | 0/0 | 0/0 |
| live_gameplay | `ef43f14ca16c49bdbc7fa39e7232d752` | unknown | 490 | 94.3% | 104/443 | 10 | 2 | 0/4 | 0/4 |
| live_gameplay | `en_2026-09-04_21-39-09` | unknown | 604 | 90.7% | 86/530 | 6 | 0 | 0/5 | 1/5 |
| live_gameplay | `ja_2026-09-05_09-34-24` | clam_blitz | 742 | 83.2% | 104/588 | 3 | 1 | 0/8 | 1/8 |
| live_gameplay | `en-2026-09-21 23-13-21` | splat_zones | 799 | 78.0% | 119/585 | 4 | 2 | 1/9 | 3/9 |
| live_gameplay | `ja_brinewater_springs_crab_tank_2026-09-16 19-49-36` | splat_zones | 442 | 86.4% | 51/369 | 4 | 1 | 0/5 | 1/5 |
| live_gameplay | `ja_brinewater_springs_sumper_chump_2026-09-16 20-13-03` | splat_zones | 467 | 84.4% | 60/379 | 5 | 3 | 1/4 | 0/4 |
| live_gameplay | `ja_brinewater_springs_triple_inkstrike_2026-09-16 20-06-30` | splat_zones | 645 | 90.7% | 92/567 | 3 | 2 | 1/5 | 0/5 |
| live_gameplay | `ja_hammerhead_bridge_bouya_bomb_2026-09-16 19-19-16` | rainmaker | 670 | 89.0% | 86/574 | 2 | 3 | 0/7 | 2/7 |
| live_gameplay | `ja_hammerhead_bridge_tenta_missle_2026-09-16 19-12-39` | rainmaker | 639 | 88.6% | 103/545 | 6 | 1 | 1/8 | 0/8 |
| live_gameplay | `ja_mahi_mahi_kraken_2026-09-16 20-18-04` | splat_zones | 364 | 91.2% | 36/321 | 1 | 1 | 0/2 | 0/2 |
| live_gameplay | `ja_mahi_mahi_reef_slider_2026-09-16 20-00-03` | splat_zones | 636 | 87.6% | 80/531 | 1 | 0 | 0/9 | 1/9 |
| live_gameplay | `ja_mahi_mahi_resort_splattercolor_screen_2026-09-16 20-31-24` | splat_zones | 649 | 92.8% | 68/592 | 1 | 0 | 0/5 | 0/5 |
| live_gameplay | `ja_mahi_mahi_resort_ultra_stamp_2026-09-16 19-49-36` | splat_zones | 284 | 73.9% | 34/203 | 0 | 2 | 0/1 | 0/1 |
| live_gameplay | `2026-07-06 22-37-08` | splat_zones | 666 | 86.5% | 74/549 | 4 | 1 | 1/8 | 0/8 |
| live_gameplay | `2026-07-07 23-09-07` | splat_zones | 578 | 71.6% | 67/408 | 4 | 0 | 0/0 | 0/0 |
| live_gameplay | `2026-07-07 23-30-14` | splat_zones | 708 | 84.3% | 98/579 | 3 | 0 | 0/4 | 2/4 |
| live_gameplay | `2026-07-07 23-37-00` | splat_zones | 703 | 91.3% | 86/621 | 5 | 2 | 0/5 | 1/5 |
| live_gameplay | `2026-07-07 23-49-06` | splat_zones | 479 | 90.0% | 53/422 | 2 | 2 | 1/4 | 0/4 |
| live_gameplay | `2026-09-04 22-05-25` | splat_zones | 603 | 92.7% | 78/543 | 2 | 0 | 0/5 | 2/5 |
| live_gameplay | `2026-09-04 22-12-51` | splat_zones | 452 | 90.5% | 54/404 | 1 | 0 | 0/0 | 0/0 |
| live_gameplay | `2026-09-06 22-15-08` | splat_zones | 380 | 91.1% | 46/336 | 2 | 2 | 0/2 | 0/2 |
| live_gameplay | `2026-09-09 21-56-22` | splat_zones | 239 | 92.5% | 32/219 | 2 | 0 | 0/0 | 0/0 |
| live_gameplay | `2026-09-14 21-02-41` | splat_zones | 252 | 83.3% | 28/196 | 3 | 0 | 0/3 | 0/3 |
| live_gameplay | `2026-09-15 19-42-35` | splat_zones | 657 | 91.9% | 80/595 | 3 | 0 | 0/1 | 0/1 |
| live_gameplay | `2026-09-18 18-37-10` | splat_zones | 329 | 89.1% | 54/288 | 7 | 1 | 0/2 | 0/2 |
| live_gameplay | `2026-09-18 18-45-40` | splat_zones | 304 | 84.9% | 36/246 | 1 | 0 | 0/3 | 0/3 |
| live_gameplay | `2026-09-19 12-24-32` | splat_zones | 685 | 71.2% | 67/457 | 1 | 0 | 0/3 | 1/3 |
| live_gameplay | `2026-09-20 16-03-58` | splat_zones | 566 | 79.9% | 58/437 | 0 | 0 | 0/2 | 0/2 |
| live_gameplay | `2026-09-20 16-09-34` | splat_zones | 629 | 85.1% | 64/498 | 7 | 0 | 0/13 | 1/13 |
| live_gameplay | `en-2026-07-05 17-13-47` | splat_zones | 367 | 82.0% | 33/282 | 0 | 2 | 1/4 | 0/4 |
| live_gameplay | `en-2026-07-05 21-30-18` | splat_zones | 329 | 85.1% | 51/262 | 4 | 2 | 1/3 | 0/3 |
| live_gameplay | `en-2026-09-21 22-48-34` | splat_zones | 654 | 92.5% | 95/591 | 5 | 2 | 1/4 | 0/4 |
| live_gameplay | `en-live-playerview-2026-10-12 17-42-59` | splat_zones | 446 | 79.6% | 54/351 | 3 | 0 | 0/1 | 0/1 |
| live_gameplay | `en-live-playerview-2026-10-12 19-59-26` | splat_zones | 270 | 68.9% | 35/176 | 3 | 2 | 0/2 | 0/2 |
| replay_derived | `en-preview-full-fast-2026-10-04 21-32-55` | clam_blitz | 401 | 93.0% | 89/367 | 3 | 0 | 0/5 | 3/5 |
