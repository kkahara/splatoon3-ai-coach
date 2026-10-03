# splatoon3-ai-coach

A community Python project that analyzes Splatoon 3 gameplay video and produces
**evidence-based** coaching: every coaching statement must trace back to
something the vision pipeline actually observed in the footage.

- Reads a recorded match video, detects HUD/game events (deaths, splats, respawns,
  timer, roster, score, map ink, specials), and fuses them into a timeline.
- Groups events into scenarios (death episodes, engagements) with factual context.
- Builds per-scenario coaching inputs and, optionally, asks an LLM to coach from
  that evidence only.
- Imports Splatoon 3's in-game Review timeline (screenshots or video) as a
  second, elapsed-clock-indexed evidence source.

New here? Read [CONTRIBUTING.md](CONTRIBUTING.md) and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Setup

Requires Python 3.11+ (CI uses 3.12). Install `ffmpeg` on your PATH; CI and the
Docker image both do.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Optional extras:

| Extra | Adds |
|-------|------|
| `display` | OpenCV GUI backend for interactive calibration windows |
| `web` | FastAPI stack for `vmv-site` and `public-site` |
| `cursor` | Cursor SDK as an LLM provider |

```bash
.venv/bin/pip install -e ".[dev,display]"
```

## Quick start

```bash
s3-coach inspect path/to/match.mp4                 # media metadata
s3-coach analyze path/to/match.mp4 --out ./analysis/my-match
s3-coach vision-view ./analysis/my-match/vision_manifest.json  # inspect results
s3-coach coach-inputs ./analysis/my-match             # evidence units, no LLM
s3-coach coach-prototype ./analysis/my-match          # prompts only
s3-coach coach-prototype ./analysis/my-match --call-llm   # call the provider
```

Run any command with `--help` for its options. Add `-v` for verbose logging.

## Commands

| Command | What it does |
|---------|--------------|
| `inspect` | Print video media metadata |
| `analyze` | Run cadence vision analysis and write `vision_manifest.json` |
| `refuse` | Re-run fusion from stored readings (no video decode) after changing fusion settings |
| `extract` | Optional change-triggered JPEG dumps for debugging (not needed by `analyze`) |
| `calibrate-timer` | Sample timer glyph tiles for manual labeling |
| `vision-view` | Open the read-only Vision Manifest Viewer |
| `coach-inputs` | Build `CoachInput` evidence units + importance-scored candidates (no LLM) |
| `coach-prototype` | Build prompts; call the LLM only with `--call-llm` |
| `coach-reannotate-flags` | Rewrite claim-pattern flag files from existing assessments |
| `review timeline-import` | Import a folder of Review timeline screenshots |
| `review timeline-video-import` | Adapt a Review timeline video and import it |
| `vmv-site` | Local analysis platform (API + React admin UI) |
| `public-site` / `public-monitor` | Public coaching submission site and its status monitor |

## Configuration and secrets

- Defaults live in [`configs/default.yaml`](configs/default.yaml) and are found
  regardless of your working directory. Relative paths inside YAML resolve
  against the YAML file's folder. Pass `--config` to use your own file.
- Secrets come **only** from environment variables with the `S3_COACH_` prefix
  (for example `S3_COACH_NVIDIA_API_KEY`, `S3_COACH_OPENAI_API_KEY`,
  `S3_COACH_CURSOR_API_KEY`), optionally loaded from a gitignored `.env`.
  Never put keys in YAML or commit `.env`.
- The LLM provider defaults to a local Ollama server (`coach.provider` in the
  YAML).

## Local artifacts

`--out ./analysis/` and `--out ./frames/` write **local-only** output
(manifests, debug snapshots, JPEGs). Those trees are gitignored; only
`analysis/.gitkeep` and `frames/.gitkeep` are tracked. Do not commit session
dumps or recordings.

## Repository layout

```text
src/splatoon3_ai_coach/   the installable package
├── config/       typed YAML schema, loader, path resolution, env secrets
├── media/        video decode, video identity, manifest read/write
├── extraction/   optional change-triggered frame dumps
├── vision/       detectors → readings → fused state → GameEvents
├── analysis/     GameSession, scenarios, ScenarioContext evidence
├── coach/        CoachInput, evidence contract, prompts, LLM clients
├── review/       Review timeline screenshots/video → elapsed-clock evidence
└── cli/          thin Typer commands (s3-coach)

configs/          default.yaml, per-stage map geometry
calibration/      glyph templates and reference images used at runtime
tests/            pytest suite (mirrors the package layout)
tools/            developer tools, web apps, and research studies (see tools/README.md)
web/              React/Vite frontends: public/ (public site), vmv/ (local platform)
training/         YOLO datasets and training scripts (not in the wheel)
simulate/         personal shell helpers for local runs (paths are machine-specific)
docs/             architecture overview
```

## Development

```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format path/to/files/you/changed.py
```

CI runs `ruff check .` and `pytest -q` on every push and pull request.

## Further reading

- [CONTRIBUTING.md](CONTRIBUTING.md): how to set up, where things go, PR checklist
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): pipeline stages and data flow
- [AGENTS.md](AGENTS.md): authoritative terminology and design rules
- [`coach/EVIDENCE_CONTRACT.md`](src/splatoon3_ai_coach/coach/EVIDENCE_CONTRACT.md):
  what coaching may and may not claim
- [`vision/MAP_INK.md`](src/splatoon3_ai_coach/vision/MAP_INK.md),
  [`vision/SPECIAL_GAUGE.md`](src/splatoon3_ai_coach/vision/SPECIAL_GAUGE.md):
  detector deep dives
- [tools/README.md](tools/README.md): developer tools index
