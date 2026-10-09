# tools/

Developer tooling that lives **outside** the installable `splatoon3_ai_coach`
package. Nothing in `src/` may import from `tools/` except the CLI commands that
launch the web apps below.

Run scripts from the repository root with the project virtualenv:

```bash
.venv/bin/python tools/<folder>/<script>.py --help
```

Most scripts read local analysis output under `analysis/` (gitignored), so they
need you to have run `s3-coach analyze` on your own recordings first.

## Layout

| Folder | What it holds | Maintained? |
|--------|---------------|-------------|
| `calibration/` | Interactive/offline helpers for drawing and checking ROIs, stage masks, and colors | Yes |
| `diagnostics/` | Validators and diagnostics run against analysis artifacts | Yes |
| `reports/` | Death-importance ranking reports over a library of analyzed matches | Yes |
| `studies/` | One-off research scripts that informed a design decision | **No** (see below) |
| `vision_manifest_viewer/` | Read-only HTML viewer for `vision_manifest.json` (`s3-coach vision-view`) | Yes |
| `vmv_site/` | Local analysis platform API (`s3-coach vmv-site`) | Yes |
| `public_site/` | Public coaching submission site (`s3-coach public-site`) | Yes |

The three web packages stay at the top level because the CLI imports them by
putting `tools/` on `sys.path`.

### Public-site telemetry

`public_site/telemetry.py` records best-effort production operations in
PostgreSQL; `public_site/admin.py` exposes bounded, server-authorized reads. It
may summarize existing analysis and coaching artifacts, but no coaching or
evidence path may read telemetry.

Telemetry distinguishes a submission from an execution run, uses database wall
time for lifecycle timestamps and monotonic time for durations, and accepts
only allowlisted JSON models. It stores no identity inside events or summaries.
`job_runs.created_at` is when the submission POST has persisted its records and
opened attempt 1; this is before the browser performs its upload PUT. The admin
UI therefore labels `created_at → finished_at` as **Job elapsed**, not generic
processing time.
When a submission expires, every run loses its `user_id`; operational timings
and provenance remain. `users.last_seen_at` is the last authenticated API
request observed at five-minute resolution, not the last browser page view.

### calibration/

| Script | Purpose |
|--------|---------|
| `roi_calibrate.py` | Interactive ROI calibration for normalized `[x1,y1,x2,y2]` boxes (shared helpers used by the other calibration scripts) |
| `roi_visualize.py` | Draw normalized ROI boxes on an image or video frame |
| `score_roi_calibrate.py` | Calibrate Splat Zones score digit ROIs |
| `stage_mask_calibrate.py` | Interactive playable-stage polygon calibration |
| `stage_mask_ab_compare.py` | A/B paint %: stage polygon mask vs ROI-union fallback |
| `map_ink_roi_preview.py` | Preview stage-map ROI coverage on a frame |
| `map_ink_color_audit.py` | Offline map ink color audit |
| `team_color_probe.py` | Print the team color probe for a `vision_manifest.json` |
| `review_special_icon_harvest.py` | Crop Review event-lane icon candidates with source and player-slot metadata |
| `review_special_sheet.py` | Contact sheets, cluster diagnostics and draft labels for Review event-lane specials/deaths (permissive: all representations, scales, cursor overlap flagged) |
| `review_special_fixture.py` | Freeze reviewed calibration recordings (selected frames, axis, labels, accepted/known-miss split) into `tests/fixtures/review/lane_corpus/` |
| `review_gate_label_sheet.py` | Pre-filled GO-gate label drafts (with `frame_index`) and review sheets (clock, cursor tick, roster rows) for a current Review import |
| `review_timeline_experiment.py` | One-factor Review timeline experiments (scale, cursor, anchor, overtime-in-axis) per recording: axis fit, deaths vs roster-lamp bounds, special shift, event gate, GO gate |
| `review_special_gate_labels.py` | Convert reviewed event-lane labels (confirmed only) into quality-gate labels for `diagnostics/validate_review_timeline.py` |
| `review_special_report.py` | Calibration report over confirmed labels: picks representation, scales, cursor width, anchor and thresholds on tuning recordings; confirms on held-out recordings (slot-aware metrics) |
| `review_sync_boundary_sheet.py` | Roster-transition boundary cases at Review specials: `sheet` builds the corpus (X-flip cases + respawn supplement), review sheets and draft labels (alive / dead / indeterminate at the special); `score` turns reviewed labels into per-distance outcomes and the empirical reliability window; `gate` runs the pre-registered held-out gate: repeated `--batch CASES LABELS`, a blind `--recheck` merged over the raw labels (both kept, `label_source`), pre-registered `--known-failures`, timing vs non-timing failures, lag / lead direction, the bias test (>= 3 failures, >= 80% one way) and PASS / FAIL, written to `gate.json` / `gate.md` with `preregistration_git_sha` and `gate_run_git_sha`. Validation only; never used to tune. Run with `PYTHONPATH=tools/diagnostics` |
| `review_lane_variant.py` | One-factor event-lane variant (`--set key=json`): re-imports baseline and variant per recording, diffs every marker/special across the whole recording, reports special/death/GO gates; artifacts feed `diagnostics/review_extraction_audit.py --artifact-root` |
| `review_extraction_sheet.py` | Detector-independent event-lane label sheets (all 8 rows, no detector output drawn) for windows chosen from roster-lamp transitions (isolated, unmarked, named), plus an empty label template |

### diagnostics/

| Script | Purpose |
|--------|---------|
| `validate_review_timeline.py` | Deterministic GO/NO-GO quality gate for a Review timeline artifact |
| `review_extraction_audit.py` | Death-marker recall, false positives, duplicates, wrong-row and lamp/slot agreement against reviewed extraction labels (refuses drafts unless `--allow-draft`) |
| `review_extraction_misses.py` | Read-only stage trace (cursor / floor / NMS / std / neighbour-row echo / scored) of every reviewed confirmed death glyph through the frozen lane detector on the production static frames |
| `review_roster_timing.py` | Interval-censored roster-lamp alive->dead timing against death glyphs (per recording / team / slot) plus dead->alive death-state durations |
| `review_special_glow_timing.py` | Interval-censored special-use timing: roster special-ready glow end against special glyph centres. Run with `PYTHONPATH=tools/diagnostics` |
| `review_special_roster_sync.py` | All eight roster lamps at each Review special with a required reporting window (`unknown` / `near_roster_transition`), an X sweep checked against event-lane order, and stratified manual-review sheets. Run with `PYTHONPATH=tools/diagnostics` |
| `map_ink_geometry_validate.py` | Multi-frame map ink geometry validation (needs local paint-map references) |
| `special_gauge_diagnose.py` | Special gauge diagnostics on survey representative frames |
| `trace_fixture.py` | Writes a trimmed golden-trace fixture (`tests/fixtures/traces/`) from a real analysis directory |
| `claim_corpus.py` | Freezes saved LLM outputs into the claim-validity corpus (`tests/fixtures/claim_corpus/`) and reports per-rule metrics under current validation |

### reports/

`importance_report.py` provides shared loaders used by the `ranking_*.py`
scripts, so keep these together.

| Script | Purpose |
|--------|---------|
| `importance_report.py` | Re-score every analyzed match and compare with its last ranking |
| `ranking_competition.py` | Competition among death candidates in the importance ranking |
| `ranking_cases.py` | Per-death case analysis of the importance factors |
| `ranking_episodes.py` | Deaths versus coaching episodes as the selection unit |
| `coaching_units.py` | Research comparison of coaching units (deaths, episodes, episode-by-factor; engagement and non-death feasibility); writes `coaching_units.md`, a blind rating sheet with a separate key, and the deferred Experiment D inputs (never calls a model). Sections live in `coaching_units_sections.py` |

### studies/

Research scripts kept so past findings stay reproducible. They are **not
maintained**: they may hardcode analysis folder names, expect specific local
recordings, or describe a stage of work that has since shipped. Do not import
them from new code. If a study's logic becomes part of the product, move it into
`src/` with tests instead of extending the script.

Studies cover Splat Zones / Clam Blitz score reading and trajectories, zone
control, special activation, the Review-feature survey (Phase 0), and score
fusion validation.

## Adding a tool

- Put it in the folder that matches its purpose; prefer `diagnostics/` for
  anything that checks an artifact and can fail.
- Resolve the repository root with
  `from splatoon3_ai_coach.config.paths import PROJECT_ROOT` rather than
  counting `Path(__file__).parents`.
- Use `argparse` (or Typer) so `--help` works without local data.
- If tests import it, the folder must be listed in `pythonpath` under
  `[tool.pytest.ini_options]` in `pyproject.toml` (all four folders already are).
