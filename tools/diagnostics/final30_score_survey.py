"""Read-only survey: Splat Zones score facts at every final-30 death.

Stored ``scenario_contexts.json`` files written before score context existed
carry no ``score``; this tool rebuilds contexts in memory from the stored
scenarios, manifest events and evidence pack (no decode, nothing written) and
prints one JSON line per death whose ``death_final_30s`` factor is active.

``ally_values`` / ``opponent_values`` count the observed fused counter values
in the ``--window`` seconds before the death, to expose readings that flip
between values (Splat Zones counters never increase).
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "reports"))

from importance_report import MANIFEST_NAME, find_analyses  # noqa: E402

from splatoon3_ai_coach.analysis.pipeline import load_scenario_evidence_pack  # noqa: E402
from splatoon3_ai_coach.analysis.scenario_context import (
    build_scenario_contexts,  # noqa: E402
)
from splatoon3_ai_coach.coach.load_analysis import (  # noqa: E402
    CoachAnalysisBundle,
    load_coach_analysis_bundle,
)
from splatoon3_ai_coach.coach.match_scoring import score_match  # noqa: E402
from splatoon3_ai_coach.config import default_config_path, load_config  # noqa: E402
from splatoon3_ai_coach.config.models import AppConfig  # noqa: E402
from splatoon3_ai_coach.config.paths import PROJECT_ROOT  # noqa: E402
from splatoon3_ai_coach.media.vision_manifest import (  # noqa: E402
    VisionManifest,
    load_vision_manifest,
)

FINAL30 = "death_final_30s"


def rebuilt_bundle(
    folder: Path, config: AppConfig
) -> tuple[CoachAnalysisBundle, VisionManifest]:
    """Coach bundle whose contexts are rebuilt with the current code."""
    bundle = load_coach_analysis_bundle(
        folder,
        min_usable_confidence=config.vision.timer.min_usable_confidence,
        playback_config=config.coach.playback_clock,
    )
    manifest = load_vision_manifest(folder)
    evidence = load_scenario_evidence_pack(
        folder,
        manifest=manifest,
        map_overlay_min_confidence=config.vision.map_overlay.min_usable_confidence,
        special_ring_fill_threshold=config.vision.special_gauge.ready_fill_threshold,
    )
    kwargs = {}
    if manifest.video is not None:
        kwargs["map_overlay_observability"] = manifest.video.map_overlay_observability
    contexts = build_scenario_contexts(
        list(manifest.game_events),
        bundle.scenarios,
        config.scenarios,
        evidence=evidence,
        **kwargs,
    )
    return dataclasses.replace(bundle, contexts=contexts), manifest


def _values(manifest: VisionManifest, t: float, window: float, side: str) -> dict:
    snaps = [s for s in manifest.state_snapshots if t - window <= s.timestamp <= t]
    return dict(
        collections.Counter(
            getattr(s, f"{side}_remaining")
            for s in snaps
            if getattr(s, f"{side}_score_quality") == "observed"
        )
    )


def survey(root: Path, config: AppConfig, window: float) -> Iterator[dict[str, Any]]:
    """One row per final-30 death under ``root`` (same video counted once)."""
    seen: set[str] = set()
    for folder in find_analyses(root):
        raw = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))
        identity = str(raw["analysis"]["video_identity"])
        if identity in seen:
            continue
        seen.add(identity)
        bundle, manifest = rebuilt_bundle(folder, config)
        scoring = score_match(bundle, config, limit=999)
        for unit in scoring.units:
            if not any(f.factor_id == FINAL30 and f.active for f in unit.factors):
                continue
            yield _row(folder.relative_to(root), bundle, manifest, unit, scoring, window)


def _row(name, bundle, manifest, unit, scoring, window) -> dict[str, Any]:
    ci = scoring.coach_inputs[unit.candidate_id]
    sf = ci.score_facts
    death = ci.primary_context.death_episode
    span = ci.durations.death_to_active_again if ci.durations else None
    t = float(unit.video_time)
    return {
        "match": str(name),
        "death": unit.candidate_id,
        "mode": bundle.battle_mode_id,
        "has_active_again": death.has_active_again if death else None,
        "death_to_active_again": span.value if span else None,
        "sample": sf.sample_label if sf else None,
        "ally_remaining": sf.ally_remaining if sf else None,
        "opponent_remaining": sf.opponent_remaining if sf else None,
        "penalties": [sf.ally_penalty_quality, sf.opponent_penalty_quality]
        if sf
        else None,
        "zone_control_at_death": (
            ci.zone_control_facts.control_at_death if ci.zone_control_facts else None
        ),
        "ally_values": _values(manifest, t, window, "ally"),
        "opponent_values": _values(manifest, t, window, "opponent"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root", type=Path, default=PROJECT_ROOT / "analysis" / "_rescore_v2"
    )
    parser.add_argument("--window", type=float, default=20.0)
    parser.add_argument("--config", type=Path, default=default_config_path())
    args = parser.parse_args(argv)
    config = load_config(args.config)
    for row in survey(args.root.resolve(), config, args.window):
        sys.stdout.write(json.dumps(row, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
