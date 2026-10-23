"""Review team identity resolution reuses gameplay calibration."""

import json
from pathlib import Path

from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.review.team_identity import resolve_team_identity


def test_gameplay_ally_hue_resolves_top_row(tmp_path: Path) -> None:
    (tmp_path / "match_identity.json").write_text(
        json.dumps(
            {
                "team_color_calibration": {
                    "ally_h": 22,
                    "opponent_h": 104,
                    "separation_degrees": 82,
                    "calibrated_at": 12,
                    "accepted_frame_ids": ["pc-1", "pc-2", "pc-3"],
                    "accepted_frame_times": [10.0, 10.5, 11.0],
                    "roster_visibility_basis": "player_count.roster_visible",
                    "source": "ready_hud_timer_adjacent_slots",
                }
            }
        ),
        encoding="utf-8",
    )
    identity = resolve_team_identity(
        20,
        105,
        config=ReviewTimelineConfig(),
        gameplay_analysis_dir=tmp_path,
    )
    assert identity.ally_row == "top"
    assert identity.source == "gameplay_color_roster_gated"
    assert identity.side_provenance == "roster_gated_calibration"
    assert identity.side_evidence_ids == ["pc-1", "pc-2", "pc-3"]


def test_ambiguous_gameplay_color_does_not_force_ally_row(tmp_path: Path) -> None:
    (tmp_path / "match_identity.json").write_text(
        json.dumps(
            {
                "team_color_calibration": {
                    "ally_h": 22,
                    "opponent_h": 104,
                    "separation_degrees": 82,
                    "calibrated_at": 12,
                }
            }
        ),
        encoding="utf-8",
    )
    identity = resolve_team_identity(
        20,
        25,
        config=ReviewTimelineConfig(team_color_min_margin=12),
        gameplay_analysis_dir=tmp_path,
    )
    assert identity.ally_row is None
    assert identity.source == "unknown"


def test_legacy_calibration_without_roster_provenance_fails_closed(
    tmp_path: Path,
) -> None:
    (tmp_path / "match_identity.json").write_text(
        json.dumps(
            {
                "team_color_calibration": {
                    "ally_h": 22,
                    "opponent_h": 104,
                    "separation_degrees": 82,
                    "calibrated_at": 12,
                }
            }
        ),
        encoding="utf-8",
    )
    identity = resolve_team_identity(
        20,
        105,
        config=ReviewTimelineConfig(),
        gameplay_analysis_dir=tmp_path,
    )
    assert identity.ally_row is None
    assert identity.side_provenance == "unknown"
