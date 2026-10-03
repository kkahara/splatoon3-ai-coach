# Contributing

Thanks for helping improve splatoon3-ai-coach. This guide gets you from clone
to pull request. For design rules and vocabulary, [AGENTS.md](AGENTS.md) is the
source of truth; for the big picture, see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Set up

```bash
git clone <repo-url>
cd splatoon3-ai-coach
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q          # should pass with some tests skipped
```

Tests that need private recordings or local analysis output are skipped
automatically when that data is absent, so a fresh clone is expected to show
skips, not failures.

To try the pipeline you need your own Splatoon 3 recording (a capture-card or
Switch clip). Output goes under `analysis/`, which is gitignored:

```bash
s3-coach analyze path/to/match.mp4 --out ./analysis/my-match
s3-coach vision-view ./analysis/my-match/vision_manifest.json
```

## The one idea to keep in mind

Everything is **evidence first**. Detectors report what they see; fusion turns
that into state; analysis groups facts; coaching interprets them. A change that
makes coaching "sound smarter" by inventing facts (fight quality, intent,
interpolated state) will be declined. If something can't be observed, it stays
unavailable.

## Where does my change go?

| Change | Location | Also update |
|--------|----------|-------------|
| New HUD/game detector | `src/splatoon3_ai_coach/vision/` + `vision/registry.py` | Reading model in `vision/models.py`, config in `config/models.py`, follow "Adding a vision detector" in AGENTS.md |
| Fusion / lifecycle rule | `vision/state.py`, `vision/lifecycle.py`, `vision/match_phase.py` | `tests/test_vision_state.py` etc. |
| New event type | `vision/events.py` (from snapshots only) | `tests/test_vision_events.py` |
| Scenario evidence | `analysis/*_context.py` | Read the "Scenario / evidence freeze" in AGENTS.md first |
| Coaching prompt / LLM | `coach/prompts/`, `coach/llm_client.py` | `coach/EVIDENCE_CONTRACT.md` if the contract changes |
| Review timeline | `src/splatoon3_ai_coach/review/` | `tests/test_review_*.py` |
| CLI command | `cli/<command>.py`, registered in `cli/app.py` | Keep it thin; logic goes in a pipeline module |
| Config option | `config/models.py` | `configs/default.yaml` |
| Developer script | `tools/<calibration\|diagnostics\|reports>/` | `tools/README.md` |
| Research experiment | `tools/studies/` | Not maintained; do not import from product code |
| Web frontend | `web/public/` or `web/vmv/` (React + Vite) | `npm install && npm run build` in that folder |

## Code style

The full list is in AGENTS.md ("Code quality rules"). The short version:

- Type hints and docstrings on public functions; functions around 50 lines or
  fewer.
- `loguru` for logging, never `print` in library code.
- Pydantic models at module boundaries and for anything written to disk.
- No hardcoded paths: use `splatoon3_ai_coach.config.paths`.
- Language-neutral visual cues first (icons, colors, layout); OCR/text only as
  optional enrichment.
- Comments explain constraints the code can't show, not what the next line
  does.

Run before pushing:

```bash
.venv/bin/ruff check .
.venv/bin/ruff format path/to/files/you/changed.py
.venv/bin/pytest -q
```

Format only the files you touched: part of the repository predates
`ruff format`, and reformatting everything would bury your change in noise.

CI runs `ruff check .` and `pytest -q` on Python 3.12.

## Tests

- Test files mirror the package: `tests/test_<stage>_<module>.py`
  (for example `tests/test_vision_death.py`).
- Prefer synthetic images (NumPy/OpenCV drawings) so tests run without
  recordings. If a test needs real footage, skip it cleanly when the file is
  missing (`pytest.mark.skipif`).
- Test pipeline/service functions rather than CLI wiring where you can.
- `tests/test_video_loader.py` is a small, readable example to copy from.

## Data and privacy

- Never commit recordings, `analysis/` output, `frames/`, or `.env`.
- API keys only via `S3_COACH_*` environment variables.
- Player names visible in footage are personal data; keep them out of fixtures.

## Pull request checklist

- [ ] One focused change per PR, with a description of *what* and *why*.
- [ ] `ruff check .` and `pytest -q` pass locally.
- [ ] New behavior has a test (synthetic where possible).
- [ ] New config options have defaults in `configs/default.yaml`.
- [ ] Docs updated if you changed a command, a stage boundary, or the evidence
      contract.
- [ ] No local artifacts or secrets in the diff.
