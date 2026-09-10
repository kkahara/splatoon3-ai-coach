# Special Gauge (vision v1)

Sparse HUD observation of the player Special dial. Evidence only — not a
`GameEvent`, not fused into `GameStateSnapshot`, not wired to CoachInput yet.

## Pipeline (v1)

```text
video frame
  → SpecialGaugeDetector
  → SpecialGaugeReading
  → (later) sparse CoachInput samples
```

Do **not** emit `SPECIAL_READY` / `SPECIAL_USED` or build `SPECIAL_OPPORTUNITY`
scenarios in this phase.

## ROI

Configured under `vision.special_gauge` (not `extraction.hud.special_gauge`).

| Field | Default | Meaning |
|-------|---------|---------|
| `roi` | `[0.86, 0.01, 0.995, 0.18]` | Top-right circular dial |
| `prompt_roi` | `[0.94, 0.02, 0.999, 0.16]` | Activation prompt strip (押しこみ / R) |

The extraction HUD box `[0.35, 0.00, 0.65, 0.12]` is a **misaligned** top-center
change-trigger ROI and must not be reused for this detector.

## Algorithm

1. Crop `roi`; locate dial with Hough circle (fallback: crop-relative center).
2. **Visibility** (`dial_score`): dark circular core + rim edge/contrast.
   Magenta Finish!-style banners force unusable.
3. **Fill**: sample angular sectors on an annulus; exclude the ~10 o’clock
   yellow sub/status badge window. Sector “lit” via yellow/orange/pale HSV.
   `fill_fraction = lit_usable / usable_sectors` (approximate).
4. **Ready** (conservative): near-full continuous lit ring **or** strong
   activation-prompt score while the dial is visible. Ready UI may pulse with
   only a few lit rim segments — prompt covers that case. The yellow badge
   alone never sets ready.
5. When `visible=False`, `fill_fraction=None` (never invent `0.0`).

## Accuracy limits

- Fill is coarse (±1–2 sectors / ~5–10%). Prefer low / partial / high / ready
  distinctions over exact percentages.
- Team ink color, resolution, and ready-pulse animation affect lit masks.
- Prompt scoring is language-neutral (brightness/edges/cyan stick chrome);
  JA 「押しこみ」 helps but is not OCR’d.
- Map overlay, death, respawn, intro, and post-match usually fail visibility.

## Enable

Off by default. Add `"special_gauge"` to `vision.enabled_detectors` after
real-video validation.

## Diagnostics

```bash
.venv/bin/python tools/special_gauge_diagnose.py
```

Writes overlays under `analysis/special_gauge_survey/diagnostics/`.

## Next (out of scope here)

Sparse samples on CoachInput (mirror `player_count_samples` / `GameClock`).
Only then consider READY/USED events if transitions are reliable.
