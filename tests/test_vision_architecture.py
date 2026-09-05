"""Architecture boundary tests for the vision layer."""

import ast
from pathlib import Path


def test_events_module_does_not_import_detectors() -> None:
    source = Path("src/splatoon3_ai_coach/vision/events.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "splatoon3_ai_coach.vision.timer" not in imports
    assert "splatoon3_ai_coach.vision.death" not in imports
    assert "splatoon3_ai_coach.vision.respawn" not in imports
    assert "splatoon3_ai_coach.vision.active_gameplay" not in imports


def test_death_module_does_not_import_timer() -> None:
    source = Path("src/splatoon3_ai_coach/vision/death.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "splatoon3_ai_coach.vision.timer" not in imports
    assert "splatoon3_ai_coach.vision.roi" in imports


def test_respawn_module_does_not_import_events() -> None:
    path = Path("src/splatoon3_ai_coach/vision/respawn.py")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "splatoon3_ai_coach.vision.events" not in imports
    assert "splatoon3_ai_coach.vision.roi" in imports
