# Experiment J: amendments

`DESIGN.md` and `design.json` stay byte-identical to the freeze (`8819c9b`;
hashes in `SHA256SUMS`). These amendments override them where they
conflict.

## Amendment 1 (2026-10-21): stop point 2 split into 2A / 2B / 2C

**Made before:** `census_order.json` was written and before any recording was
analyzed. No census, label or output had been seen.

**Requested by:** the rater, at the freeze review.

Stop point 2 becomes three sealed stops.

- **2A: source and order freeze.**
  - Enumerate the full candidate pool.
  - Apply only the recording-level rules that need no gameplay analysis.
  - Sort by the frozen timestamp/path rule.
  - Write `census_order.json`, hash it, commit it and stop.
  - No `analyze`.
- **2B: deterministic census and worksheet freeze.**
  - Walk exactly that order. Analyze recordings until **the recording that
    contains the 50th mechanically eligible death has been processed
    completely**. The terminal recording is finished, not stopped halfway.
    This clarifies "Analysis stops once the 50th is found" in `DESIGN.md`.
  - From the analyzed recordings, build:
    - the first 50 eligible deaths (frozen death order);
    - the complete not-eligible frame with `sufficient` map observability,
      through the terminal recording;
    - the seeded sample of up to 50 not-eligible deaths;
    - the blinded, shuffled worksheet;
    - the separate key;
    - the 20-death P3 draw.
  - Seal all of it before any relevance label is written.
- **2C: relevance labelling.**
  - The rater labels the worksheet, and the labels are sealed.
  - Only then is the key joined and the J populations revealed.
  - The go/no-go (at least 20 relevant = yes) is evaluated here.

Stop points 3–5 are unchanged.
