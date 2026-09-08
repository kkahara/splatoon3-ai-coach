"""Architecture boundary tests for the vision layer."""

import ast
from pathlib import Path


def _imported_symbol_names(path: Path) -> set[str]:
    """Return imported symbol names (``from x import Y`` / ``import Y``)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[-1] for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


def _import_modules(path: Path) -> set[str]:
    """Return ImportFrom module names in a source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }


def test_events_module_does_not_import_detectors() -> None:
    imports = _import_modules(Path("src/splatoon3_ai_coach/vision/events.py"))
    assert "splatoon3_ai_coach.vision.timer" not in imports
    assert "splatoon3_ai_coach.vision.death" not in imports
    assert "splatoon3_ai_coach.vision.respawn" not in imports
    assert "splatoon3_ai_coach.vision.active_gameplay" not in imports
    assert "splatoon3_ai_coach.vision.map_overlay" not in imports
    assert "splatoon3_ai_coach.vision.splat" not in imports


def test_pipeline_does_not_load_extraction_manifests() -> None:
    imports = _import_modules(Path("src/splatoon3_ai_coach/vision/pipeline.py"))
    assert "splatoon3_ai_coach.media.manifest" not in imports
    assert "splatoon3_ai_coach.extraction.models" not in imports
    source = Path("src/splatoon3_ai_coach/vision/pipeline.py").read_text(encoding="utf-8")
    assert "cv2.imread" not in source


def test_frame_sampler_does_not_decode_video() -> None:
    path = Path("src/splatoon3_ai_coach/extraction/sampler.py")
    imports = _import_modules(path)
    imported_names = _imported_symbol_names(path)
    assert "av" not in imports
    assert "VideoLoader" not in imported_names
    assert "splatoon3_ai_coach.media.video" in imports
    source = path.read_text(encoding="utf-8")
    assert "seek(" not in source


def test_detectors_do_not_decode_video() -> None:
    detector_files = (
        "timer.py",
        "death.py",
        "splat.py",
        "respawn.py",
        "active_gameplay.py",
        "map_overlay.py",
    )
    for name in detector_files:
        imports = _import_modules(Path("src/splatoon3_ai_coach/vision") / name)
        assert "splatoon3_ai_coach.media.video" not in imports
        assert "av" not in imports


def test_death_module_does_not_import_timer() -> None:
    imports = _import_modules(Path("src/splatoon3_ai_coach/vision/death.py"))
    assert "splatoon3_ai_coach.vision.timer" not in imports
    assert "splatoon3_ai_coach.vision.roi" in imports


def test_active_gameplay_does_not_import_timer() -> None:
    """HUD evidence must not nested-run the timer or interpret match phase."""
    imports = _import_modules(Path("src/splatoon3_ai_coach/vision/active_gameplay.py"))
    assert "splatoon3_ai_coach.vision.timer" not in imports
    assert "splatoon3_ai_coach.vision.death" not in imports


def test_respawn_module_does_not_import_events() -> None:
    imports = _import_modules(Path("src/splatoon3_ai_coach/vision/respawn.py"))
    assert "splatoon3_ai_coach.vision.events" not in imports
    assert "splatoon3_ai_coach.vision.roi" in imports
