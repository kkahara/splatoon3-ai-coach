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

### diagnostics/

| Script | Purpose |
|--------|---------|
| `validate_review_timeline.py` | Deterministic GO/NO-GO quality gate for a Review timeline artifact |
| `map_ink_geometry_validate.py` | Multi-frame map ink geometry validation (needs local paint-map references) |
| `special_gauge_diagnose.py` | Special gauge diagnostics on survey representative frames |

### reports/

`importance_report.py` provides shared loaders used by the `ranking_*.py`
scripts, so keep these together.

| Script | Purpose |
|--------|---------|
| `importance_report.py` | Re-score every analyzed match and compare with its last ranking |
| `ranking_competition.py` | Competition among death candidates in the importance ranking |
| `ranking_cases.py` | Per-death case analysis of the importance factors |
| `ranking_episodes.py` | Deaths versus coaching episodes as the selection unit |

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
