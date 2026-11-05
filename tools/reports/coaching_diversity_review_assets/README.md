# Local coaching comparison evaluator

Start the evaluator from the repository root:

```sh
.venv/bin/python tools/reports/coaching_report_diversity.py review-ui
```

Open http://127.0.0.1:8766. Use `review-ui --port 8767` for another port.
The server binds to loopback and needs no external service.

Each pair starts with an exact-text comparison, corresponding moments, and
three simple questions. Open the full reports to read them in their original
wording. Advice with different wording is listed separately even if its
meaning is similar. A moment coached in only one report is identified without
claiming that the other report omitted the underlying observation.

Select A, B, tie, or request gameplay. Choose a reason and optionally add a
comment. Answers save automatically. The preference and comments use the
existing CSV; the reason and evidence requests are stored in
`blind/review_ui_state.json`. An evidence request preserves any earlier
preference but marks it unresolved. Scores are never inferred. Open
**Detailed evaluation** and complete all original fields for both reports
before a pair is ready for analysis. The original scale remains unchanged,
including redundancy where 5 means very repetitive.

Wait for “All changes saved” before closing. Restart the command to resume.
An explicit edit creates `blind/rating_sheet.pre_ui.csv` as a backup before
changing the existing CSV. Stale browser edits are rejected. The overview
shows which pairs still need detailed answers. Neither the overview nor
completion performs analysis or opens the key.

## Evidence and the original protocol

The original blinded instructions say not to open files outside `blind/`
until the sheet is complete. The current user explicitly requested access to
the gameplay during evaluation; that is a change to the rater's permitted
material and should be reviewed as a protocol amendment before ratings start.
The original instructions remain accessible from the interface and unchanged.

The first UI reported all 136 A/B moment entries unavailable without looking
for source media. The new neutral lookup uses the original pair shuffle only
for pair-to-recording association, reads no A/B policy assignments or key,
and validates recording content identity against the vision manifest. The
stored death video timestamp is matched to its observed timer label; ambiguous
matches are not assigned. The resulting local mapping is stored outside the
blinded packets in `review_ui_media.json`. Only neutral pair IDs, timestamps,
and neutral media routes reach the browser.

All nine original recordings are present and content-verified, with 68 unique
moment timestamps. Eight source videos use HEVC and one uses H.264. The player
starts five seconds before the stored event; browser codec support determines
whether HEVC plays. A direct recording link is provided if the player fails.
Fifteen existing screenshots are available for two pairs; 53 moments have no
saved screenshot and remain in the evidence queue as optional missing images.
No screenshot or footage has been fabricated or marked reviewed.

The media mapping contains local absolute paths. If source files move, the
corresponding queue entries become unavailable; do not replace them with a
file based only on its name. The content identity must be verified again.
