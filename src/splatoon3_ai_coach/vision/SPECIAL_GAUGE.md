# Special Gauge (vision v1)

Sparse HUD observation of the player Special dial. Evidence only — not a
`GameEvent`, not fused into `GameStateSnapshot`, not wired to CoachInput yet.

Architecture source of truth for this detector. See also the Special-gauge
pointer in `AGENTS.md`.

## Layer split

```text
Detector says what is true on this frame.
Fusion will eventually decide what changed.
```

```text
video frame
  → SpecialGaugeDetector
  → SpecialGaugeReading   (status / evidence — current layer)
  → (later) fusion state
  → (later) SPECIAL_READY / SPECIAL_USED transitions
  → (later) scenarios / coaching
```

Do **not** collapse those layers. Do **not** emit `SPECIAL_READY` /
`SPECIAL_USED`, build special scenarios, or wire CoachInput in this phase.
`GameEventType.SPECIAL_READY` / `SPECIAL_USED` are reserved unused placeholders.

### Must not conflate

| Observation | Is not |
|-------------|--------|
| `SpecialGaugeReading.ready == True` | `SPECIAL_READY` |
| `fill_fraction` dropping | `SPECIAL_USED` |
| `GameEventType.DEATH` | `SPECIAL_USED` |

## Reading vs event

`SpecialGaugeReading.ready` is **per-frame status** — what the detector sees
now:

```text
ready = charged_match OR press_match OR strong_ring
```

Example of ordinary repeated status (not an event stream):

```text
t=40.0  ready=false
t=45.0  ready=true
t=46.0  ready=true
t=47.0  ready=true
```

Repeated `ready=True` remains ordinary readings. There is only one conceptual
transition (`false → true`) that a **future** fusion layer could turn into
`SPECIAL_READY`. The detector must not emit that event.

### VMV presentation marker

VMV may visualize a SPECIAL READY marker from observed `ready` false→true
(or first observation already true). Presentation-only; not an authoritative
`GameEvent`. Authoritative `SPECIAL_READY` remains future fusion.
**VMV marker ≠ GameEvent.**

## Death ≠ special used

Splatted players lose some special charge, but not necessarily all of it:

```text
t=45.0  ready=True   (full charge)
t=50.0  DEATH        (charge drops, e.g. ~75%)
t=51.0  ready=True   (still usable — valid)
```

Do **not** implement `DEATH → special ready=False`. Do **not** treat a charge /
`fill_fraction` drop as `SPECIAL_USED`. A death penalty is a charge adjustment,
not confirmed special consumption.

## Future state sketch (conceptual only)

Illustrative fusion thinking — **not rules to implement now**. We have not
established reliable evidence distinguishing actual special activation from a
death-related charge reduction.

```text
special_ready = false
        ↓
        ↓  (conceptually: ready becomes true)
        ↓
special_ready = true
        ↓
        ├── death → charge penalty; may remain true
        │
        └── confirmed special activation  ← requires evidence we do not have yet
                ↓
        special_ready = false   ← “activation clears” is conceptual only
```

If/when fusion is justified by real-video validation:

- `false → true` ⇒ candidate `SPECIAL_READY`
- `true → false` **because special was actually activated** ⇒ candidate
  `SPECIAL_USED`

Until then, `SPECIAL_*` stay unused.

## Validation checklist (before any fusion work)

| | Question |
|---|----------|
| **A** Ready onset | Can we reliably see not-ready → ready (Charged!/Press or strong-ring)? |
| **B** Persistence | While the special stays usable, does `ready=True` stay stable? |
| **C** Death penalty | When splatted while ready, does the reading stay ready if still charged enough? |
| **D** Actual use | What does the gauge trajectory look like on real activation? Observe only — do not encode `SPECIAL_USED` yet. |

Capture evidence that could later separate death penalty from actual use. Do
not invent that distinction in code until it is unambiguous on real footage.

## ROI

Configured under `vision.special_gauge` (not `extraction.hud.special_gauge`).

| Field | Default | Meaning |
|-------|---------|---------|
| `roi` | `[0.875, 0.026, 0.977, 0.205]` | Top-right circular dial |
| `press_roi` | `[0.949479, 0.115741, 0.986979, 0.147222]` | Press / 押しこみ stick prompt |
| `charged_roi` | `[0.790104, 0.143519, 0.904167, 0.205556]` | Charged! / フルチャージ！ flash |
| `template_dir` | `calibration/templates/specials` | Language packs under `en/` / `ja/` |

The extraction HUD box `[0.35, 0.00, 0.65, 0.12]` is a **misaligned** top-center
change-trigger ROI and must not be reused for this detector.

## Algorithm

1. Crop `roi`; locate dial with Hough circle (fallback: crop-relative center).
2. **Visibility** (`dial_score`): dark circular core + rim edge/contrast.
   Magenta Finish!-style banners force unusable.
3. **Fill**: sample angular sectors on an annulus; exclude the ~10 o’clock
   yellow sub/status badge window. Sector “lit” via yellow/orange/pale HSV.
   `fill_fraction = lit_usable / usable_sectors` (approximate).
4. **Ready** (status evidence, not an event):
   - Charged! template match in `charged_roi` (`*charged*` stems), or
   - Press / 押しこみ template match in `press_roi` (`*press*` stems), or
   - near-full continuous lit ring (`ready_fill_threshold` + continuity)
5. Templates load only from `template_dir / {VisionConfig.language}` (no
   cross-language fallback). Scores: `charged_score`, `press_score`;
   `ready_prompt_score = max(charged, press)` for viewer compatibility.
6. When `visible=False`, `fill_fraction=None` (never invent `0.0`). Template
   matches may still set `ready=True`.

## Accuracy limits

- Fill is coarse (±1–2 sectors / ~5–10%). Prefer low / partial / high / ready
  distinctions over exact percentages.
- Team ink color, resolution, and ready-pulse animation affect lit masks.
- Charged! is a short flash; Press / 押しこみ persists while special is usable.
- Map overlay, death, respawn, intro, and post-match usually fail dial visibility.

## Enable

Add `"special_gauge"` to `vision.enabled_detectors` (on in `configs/default.yaml`
and `configs/player_count_validate.yaml`).

## Diagnostics

```bash
.venv/bin/python tools/special_gauge_diagnose.py
```

Writes overlays under `analysis/special_gauge_survey/diagnostics/`.

## Out of scope (do not implement here)

- Emitting `SPECIAL_READY` / `SPECIAL_USED`
- Fusion sticky `special_ready` or “activation clears ready”
- `DEATH → ready=False` or fill-drop → used
- Special scenarios, CoachInput samples, coaching judgments
- Changing thresholds / templates / ROIs for event invention
