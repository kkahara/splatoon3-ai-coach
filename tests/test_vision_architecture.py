"""Architecture boundary tests for the vision layer."""

import ast
from pathlib import Path


def test_events_module_does_not_import_timer_detector() -> None:
    source = Path("src/splatoon3_ai_coach/vision/events.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.names[0].name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "splatoon3_ai_coach.vision.timer" not in imports
    assert "splatoon3_ai_coach.vision.hud" not in imports
