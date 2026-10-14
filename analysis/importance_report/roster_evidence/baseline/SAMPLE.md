# Frozen gameplay-HUD roster hand-label sample

Status: frozen before labeling. Do not add, delete, reorder, replace, or
rebalance frames after labels are observed.

## Definition

- Detector: gameplay HUD roster X detector only.
- Seed: `20261014`.
- Frames: 120.
- Distinct source matches: 29.
- Source class: 97 live gameplay, 23 replay-derived.
- Fixed strata: 20 each of `pre_death`, `fused_change`, `flicker`, `margin`,
  `hud_hidden`, and `steady`.
- The predeclared source target was 96 live and 24 replay-derived (four replay
  frames per stratum). The replay source contained only three distinct
  `flicker` candidates, so that stratum was frozen as 17 live / 3 replay; the
  other five strata are 16 live / 4 replay.
- Config sha256:
  `17cf9fa6218686a2f2ba425cc2b80c23309cd71acbd71afb884b828263d19621`.
- Analyses without a same-stem source video in `~/Movies`: 13, recorded in
  `labels/sample_manifest.json`; they were excluded before sampling.
- Review timeline lamps and gameplay-to-Review alignment were not used.

The stratum records why a frame was sampled, not whether its detector result
is correct. Strata were assigned and frozen without human labels.

## Frozen artifacts

- `labels/sample_manifest.json`:
  `6dc7d574401a3a0e407fbe2d800397dcb07efcf116ba67324b97094f1e8ff113`
- `labels/key.json`:
  `3f882b2f69256b8264108e6bf941e81833f733d2bbf4a3b6c92911f8551612d2`
- Initial blank `labels/labels.csv`:
  `8740f4acbe0118510f5f45ebffdb5b359308e1bd4787b920f683af2a87ad145b`

The key contains the unchanged detector and fused predictions. It is separate
from the label sheet and images. Do not inspect it before labels are sealed.

## Labeling

Use `labels/LABELING.md`, the per-frame files under `labels/images/`, and the
12 contact sheets under `labels/sheets/`.

For each row:

- `hud_visible`: `visible`, `hidden`, or `?`;
- `a1..a4`, `o1..o4`: `x`, `alive`, or `?`;
- `notes`: optional.

`?` means the human cannot determine the state. It is measured as ambiguity,
not treated as an incorrect label. The completed CSV must be committed before
the key is opened or scoring is run.
