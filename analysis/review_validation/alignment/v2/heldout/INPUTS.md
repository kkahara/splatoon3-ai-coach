# v2 held-out inputs (committed before processing)

Declared by the operator on 2026-10-12 20:52 (UTC-5), after the v2
pre-registration commit `a66a042`. Pairs matched by recording timestamp. None
of these recordings was seen by v1 or v2 tuning or by the v1 held-out gate.

## Declared deviation from `../PREREGISTRATION.md`

- **Five pairs instead of four.** The operator recorded five consecutive
  matches and declared all five before any processing. Selection was made
  without looking at any recording's content: none was opened, imported or
  analyzed. Pass criteria are pooled, so they are unchanged. Cyclic negative
  controls become five, as listed below.
- **No exclusions.** The 19-59-26 gameplay recording is 134.9 s long. The
  operator declares the match ended early by knockout and that the recording
  is complete from before GO through results. It stays in.

Frozen-system check at declaration (HEAD `544a029`): no diff since the v2
method commit `70c2eeb` in `src/splatoon3_ai_coach/`, `configs/` or
`tools/diagnostics/review_gameplay_alignment.py`. `configs/default.yaml`
sha256 `17cf9fa6…19621`, `alignment` block `463787c0…3f`, `review` block
`98c2bf53…5e24`, as pre-registered.

## Files

| Pair | File | Bytes | Duration s | sha256 |
|---|---|---|---|---|
| 17-42-59 | `en-live-playerview-2026-10-12 17-42-59.mov` | 250277449 | 222.73 | `9aae572011d1c25352ab17665f41047fae9cf2e0c4857e8a9917968fb09079d4` |
| 17-42-59 | `en-review-timeline-2026-10-12 17-42-59.mov` | 21303472 | 36.80 | `d443304da86af8253e1ce8b84857bed7cf314944803541535d7df605e98063f7` |
| 19-47-46 | `en-live-playerview-2026-10-12 19-47-46.mov` | 197176680 | 203.72 | `c3d9eeb95036b21b9bfb7520a92c3a919eac290a0aaf5adb7c45ae1d01312ac3` |
| 19-47-46 | `en-review-timeline-2026-10-12 19-47-46.mov` | 19653881 | 56.37 | `30bd23920096bfaf98e49d6cfa9c1f2449283baf72580c6466ef31b187bbf77d` |
| 19-59-26 | `en-live-playerview-2026-10-12 19-59-26.mov` | 127669020 | 134.93 | `5c4dbac92702d098b53f656f1727707d41481c9a6146bc9fc2b4a4e13e309c06` |
| 19-59-26 | `en-review-timeline-2026-10-12 19-59-26.mov` | 19423280 | 38.33 | `dbb43cd8a6218a9299be5478ce8eacbc7050a21480dc57bfea0f9ec3a3761849` |
| 20-17-32 | `en-live-playerview-2026-10-12 20-17-32.mov` | 341001710 | 329.15 | `e829eba1e065c25cd78464d9b57aac2b3795f20aaa5aaa002bbdb0a01ea38e1b` |
| 20-17-32 | `en-review-timeline-2026-10-12 20-17-32.mov` | 21682331 | 45.43 | `76a7079404c351417d026d0335e6e9d2b90d6b0accf04c261d575302c48f9ec7` |
| 20-25-22 | `en-live-playerview-2026-10-12 20-25-22.mov` | 451573707 | 353.33 | `e312a2b8beeb38bcac6ca7e7147e78e0c32aa624932b290076d8ff24ffab1029` |
| 20-25-22 | `en-review-timeline-2026-10-12 20-25-22.mov` | 21327649 | 43.48 | `8ff722a9feea39813c2990d1885a012785fae0adf2ded38a8aeaa481266f96aa` |

## Negative controls (cyclic, declared order)

Review 17-42-59 with gameplay 19-47-46, 19-47-46 with 19-59-26, 19-59-26
with 20-17-32, 20-17-32 with 20-25-22, 20-25-22 with 17-42-59. Reported
only, not pass criteria.
