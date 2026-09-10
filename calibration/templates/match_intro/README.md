# Match-intro templates (templates only — no OCR)

Layout:

```text
match_intro/
  en/
    battle_modes/
      turf_war.png          # or en-turf_war.png
      splat_zones.png
      tower_control.png
      rainmaker.png
      clam_blitz.png
    stages/
      scorch_gorge.png      # or en-scorch_gorge.png
      … (add stages as you capture them)
  ja/
    battle_modes/ …         # Japanese intro plates
    stages/ …
```

Detector ROIs (config): battle mode center, stage bottom-right.

Filename rules:

- Stem must resolve to `battle_mode_id` / `stage_id` (e.g. `mahi_mahi_resort`).
- Optional prefixes are stripped: `en-`, `ja-`, `jp-`, `eg-` (typo for `en-`).
- Language folder (`en/` / `ja/`) already selects the pack; prefixes are cosmetic.
- Incomplete stage packs are OK — only present templates can match; missing
  stages leave identity unresolved until you add them (map ink stays disabled).
