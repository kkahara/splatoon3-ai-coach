# Ready? templates (templates only — no OCR)

Layout:

```text
ready/
  en/
    en-ready.png          # English “Ready?”
  ja/
    ja-ready.png          # Japanese ready plate (add when captured)
```

Detector ROI (config): center band covering the Ready? glyph.

The pipeline only runs this detector after stage identity is known and stops
once the match clock first leaves the frozen opening values (5:00 / 3:00).
