"""Static guard: the game language comes from detection only.

Surviving language concepts fall into four categories: detected game language,
report/site language, capture language, and historical compatibility. These
checks pin the places a supplied game language used to enter analysis.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
import yaml
from public_site.app import CreateBody
from public_site.models import Submission
from public_site.telemetry import _SCHEMA
from vmv_site.app import JobCreate
from vmv_site.models import JobRecord

from splatoon3_ai_coach.config.paths import PROJECT_ROOT

PYTHON_ROOTS = ("src", "tools")
SKIPPED_PARTS = {"studies", "node_modules", "__pycache__"}
LANGUAGE_FLAG_ALLOWED = {
    # Capture language (capture package metadata), not the analysis language.
    "src/splatoon3_ai_coach/cli/capture.py",
    # Sealed Experiment J census: needs the pre-removal ``analyze --language``
    # CLI and is not a supported caller of the current command.
    "tools/reports/coaching_units_experiment_j_census.py",
}
VISION_ROOT = "src/splatoon3_ai_coach/vision/"
# Its caller passes the already-selected language template directory; the
# argument only labels emitted cues.
LANGUAGE_LABEL_ONLY = {
    (
        "src/splatoon3_ai_coach/vision/tower_control_messages.py",
        "TowerControlMessageDetector",
    )
}
SUPPLIED_NAMES = {"supplied_language", "supplied_agreement"}
REQUEST_OBJECTS = {"submission", "body", "job"}
WEB_SUFFIXES = {".js", ".jsx", ".json"}


def _python_files() -> list[Path]:
    files = []
    for root in PYTHON_ROOTS:
        for path in (PROJECT_ROOT / root).rglob("*.py"):
            if not SKIPPED_PARTS & set(path.relative_to(PROJECT_ROOT).parts):
                files.append(path)
    return files


def _trees() -> list[tuple[str, ast.AST]]:
    return [
        (str(path.relative_to(PROJECT_ROOT)), ast.parse(path.read_text(encoding="utf-8")))
        for path in _python_files()
    ]


@pytest.fixture(scope="module")
def trees() -> list[tuple[str, ast.AST]]:
    return _trees()


def test_language_flag_survives_only_in_capture_and_historical_code(trees) -> None:
    found = {
        rel
        for rel, tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == "--language"
    }
    assert found <= LANGUAGE_FLAG_ALLOWED
    assert "src/splatoon3_ai_coach/cli/analyze.py" not in found


def _defaulted_args(args: ast.arguments) -> set[str]:
    positional = [*args.posonlyargs, *args.args]
    names = {arg.arg for arg in positional[len(positional) - len(args.defaults):]}
    names |= {
        arg.arg
        for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
        if default is not None
    }
    return names


def test_vision_constructors_never_default_the_game_language(trees) -> None:
    found = []
    for rel, tree in trees:
        if not rel.startswith(VISION_ROOT):
            continue
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            if (rel, cls.name) in LANGUAGE_LABEL_ONLY:
                continue
            for node in cls.body:
                if isinstance(node, ast.FunctionDef) and node.name == "__init__":
                    defaulted = _defaulted_args(node.args) & {"language", "ocr_lang"}
                    found.extend((rel, cls.name, name) for name in defaulted)
    assert found == []


def test_no_code_reads_or_writes_a_supplied_language(trees) -> None:
    found = []
    for rel, tree in trees:
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            elif isinstance(node, (ast.keyword, ast.arg)):
                name = node.arg
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                name = node.value
            if name in SUPPLIED_NAMES:
                found.append((rel, node.lineno if hasattr(node, "lineno") else None))
    assert found == []


def test_public_and_vmv_never_read_a_request_language(trees) -> None:
    found = []
    for rel, tree in trees:
        if not rel.startswith(("tools/public_site/", "tools/vmv_site/")):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "language"
                and isinstance(node.value, ast.Name)
                and node.value.id in REQUEST_OBJECTS
            ):
                found.append((rel, node.lineno))
    assert found == []


@pytest.mark.parametrize("model", [CreateBody, Submission, JobCreate, JobRecord])
def test_request_and_job_models_carry_no_language(model) -> None:
    assert "language" not in model.model_fields


def test_web_sources_have_no_game_language_selector() -> None:
    markers = re.compile(
        r"--language|GAME_LANGUAGE_KEY|rememberGameLanguage|rememberedGameLanguage"
        r"|GameLanguageField|submit\.system_language|submit\.choose_language"
        r"|submit\.error_language|video_id: videoId, language"
    )
    found = []
    for app in ("web/public/src", "web/vmv/src"):
        for path in (PROJECT_ROOT / app).rglob("*"):
            if path.name.endswith(".test.js") or path.suffix not in WEB_SUFFIXES:
                continue
            if markers.search(path.read_text("utf-8")):
                found.append(str(path.relative_to(PROJECT_ROOT)))
    assert found == []


def test_no_shipped_config_sets_the_game_language() -> None:
    found = []
    for path in (PROJECT_ROOT / "configs").rglob("*.yaml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "language" in (raw.get("vision") or {}):
            found.append(path.name)
    assert found == []


def test_launch_configs_pass_no_language() -> None:
    launch = PROJECT_ROOT / ".vscode" / "launch.json"
    if not launch.is_file():
        pytest.skip("no launch.json")
    configs = json.loads(launch.read_text(encoding="utf-8"))["configurations"]
    assert all("--language" not in config.get("args", []) for config in configs)


def test_telemetry_ddl_has_only_the_detected_game_language() -> None:
    bare = re.compile(r"(?<![\w_])language(?![\w_])")
    mentions = [statement for statement in _SCHEMA if bare.search(statement)]
    assert [" ".join(statement.split()) for statement in mentions] == [
        "ALTER TABLE job_runs DROP COLUMN IF EXISTS language"
    ]
