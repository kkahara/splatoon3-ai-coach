"""Amendment 3 isolation: blind tools neither load nor accept prohibited inputs.

Static import/AST checks over the extraction and annotation modules, plus
checks of their public function parameters and command-line options.
"""

from __future__ import annotations

import argparse
import ast
import inspect
import re
import subprocess
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path

import map_ink_reference
import pytest

PACKAGE_DIR = Path(map_ink_reference.__file__).parent

BLIND = (
    "population",
    "plan",
    "stage_identity",
    "domain",
    "points",
    "extraction",
    "render",
    "queueing",
    "session",
    "records",
    "server",
    "gate",
    "pilot",
    "trigger",
    "pipeline_blind",
    "execution",
    "pilot_pipeline",
    "pilot_addendum",
    "pilot2_sources",
    "pilot2_pipeline",
    "pilot2_addendum",
    "corpus_capacity",
    "stage1_recovery",
)
NEUTRAL = ("constants", "errors", "io_utils", "pins", "seal")
# Step 2a derivation: replays stored intro readings through production code.
DERIVATION = ("intro_latch",)
UNBLINDED = (
    "unblinded_pins",
    "mapping",
    "comparison",
    "statistics",
    "verdicts",
    "pipeline_unblinded",
    "criterion_4a",
    "report",
    "cli",
)
# Exactly the production modules amendment4-intro-latch-v1 is pinned to.
DERIVATION_PRODUCTION_IMPORTS = {
    "splatoon3_ai_coach.config.loader",
    "splatoon3_ai_coach.config.models",
    "splatoon3_ai_coach.vision.match_intro",
    "splatoon3_ai_coach.vision.models",
}

FORBIDDEN_PRODUCTION_IMPORTS = (
    "splatoon3_ai_coach.vision.team_color_calibration",
    "splatoon3_ai_coach.vision.match_side_effects",
    "splatoon3_ai_coach.vision.calibration_window",
    "splatoon3_ai_coach.review.team_identity",
    "splatoon3_ai_coach.analysis",
    "splatoon3_ai_coach.coach",
    "splatoon3_ai_coach.config",
)
ALLOWED_FROM_MAP_INK = {"_sample_mask"}

FORBIDDEN_TOKENS = re.compile(
    r"calibrat|map_observation|MapObservation|match_identity|classify_bgr|"
    r"MapInkClassifier|ally_h|opponent_h|CALIBRATION_PINS|COVERAGE_PINS|"
    r"results_stage|stage2_trigger|report\.json|own_swatch|reference_class",
    re.IGNORECASE,
)
# The sealed Stage 2 trigger (ids + digest only) is read only through ``trigger``.
TRIGGER_TOKEN_ALLOWED = {"trigger"}
# Reading team-identity records is only for the session that writes them.
TEAM_RECORD_TOKEN = re.compile(r"team_identity_name|team_identity_stage")
TEAM_RECORD_ALLOWED = {"records"}
# The manifest reducer names forbidden keys only to reject them.
DENYLIST_ASSIGNMENTS = {"_FORBIDDEN_MANIFEST_KEYS"}

FORBIDDEN_PARAMETER = re.compile(
    r"calibrat|hue|observation|classif|comparison|result|verdict|mapping|own_|"
    r"ally|opponent|answer|match_identity|q_all|p_reg|p_imp",
    re.IGNORECASE,
)
BLIND_CLI_COMMANDS = (
    "preconditions",
    "blind-inputs",
    "pilot-select",
    "plan",
    "extract",
    "points",
    "annotate",
    "export",
    "gate",
    "pilot-plan",
    "pilot-extract",
    "pilot-points",
    "pilot-annotate",
    "pilot-export",
    "pilot-gate",
    "pilot-addendum",
    "pilot1-close-gate",
    "pilot2-select",
    "pilot2-plan",
    "pilot2-extract",
    "pilot2-points",
    "pilot2-annotate",
    "pilot2-export",
    "pilot2-gate",
    "pilot2-addendum",
)
BLIND_CLI_OPTIONS = {
    "recording",
    "video",
    "stage",
    "recordings",
    "port",
    "scope",
    "item",
    "interface_commit",
    "none",
    "help",
}


def _tree(name: str) -> ast.Module:
    return ast.parse((PACKAGE_DIR / f"{name}.py").read_text())


def _imports(tree: ast.Module) -> list[tuple[str, list[str]]]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(alias.name, []) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append((node.module, [alias.name for alias in node.names]))
    return out


def _local_closure(start: str) -> set[str]:
    seen, todo = set(), [start]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        for module, _ in _imports(_tree(name)):
            if module.startswith("map_ink_reference."):
                todo.append(module.split(".", 1)[1])
    return seen


def _docstring_nodes(tree: ast.Module) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _denylist_nodes(tree: ast.Module) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in DENYLIST_ASSIGNMENTS for t in node.targets
        ):
            ids |= {id(n) for n in ast.walk(node.value)}
    return ids


def _code_tokens(name: str) -> list[str]:
    tree = _tree(name)
    skip = _docstring_nodes(tree) | _denylist_nodes(tree)
    tokens = []
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Name):
            tokens.append(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.append(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            tokens.append(node.value)
        elif isinstance(node, ast.alias):
            tokens.append(node.name)
        elif isinstance(node, ast.arg):
            tokens.append(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            tokens.append(node.name)
    return tokens


def test_module_groups_cover_the_package() -> None:
    modules = {p.stem for p in PACKAGE_DIR.glob("*.py")} - {"__init__", "__main__"}
    assert modules == set(BLIND) | set(NEUTRAL) | set(DERIVATION) | set(UNBLINDED)


@pytest.mark.parametrize("name", BLIND)
def test_blind_closure_excludes_unblinded_and_derivation_modules(name: str) -> None:
    closure = _local_closure(name)
    excluded = set(UNBLINDED) | set(DERIVATION)
    assert not closure & excluded, closure & excluded


@pytest.mark.parametrize("name", DERIVATION)
def test_derivation_imports_only_the_pinned_production_modules(name: str) -> None:
    assert not _local_closure(name) & set(UNBLINDED)
    production = {
        m for m, _ in _imports(_tree(name)) if m.startswith("splatoon3_ai_coach")
    }
    assert production == DERIVATION_PRODUCTION_IMPORTS
    for module, _ in _imports(_tree(name)):
        if module == "splatoon3_ai_coach.vision" or module == "splatoon3_ai_coach.config":
            pytest.fail(f"{name} imports a package root")
    hits = [t for t in _code_tokens(name) if FORBIDDEN_TOKENS.search(t)]
    assert not hits, (name, hits)


@pytest.mark.parametrize("name", BLIND + NEUTRAL)
def test_blind_modules_import_no_calibration_sources(name: str) -> None:
    for module, names in _imports(_tree(name)):
        assert not module.startswith(FORBIDDEN_PRODUCTION_IMPORTS), (name, module)
        if module == "splatoon3_ai_coach.vision.map_ink":
            assert set(names) <= ALLOWED_FROM_MAP_INK, (name, names)
        if module == "splatoon3_ai_coach.vision":
            pytest.fail(f"{name} imports the vision package root")


@pytest.mark.parametrize("name", BLIND + NEUTRAL)
def test_blind_code_names_no_prohibited_information(name: str) -> None:
    hits = [t for t in _code_tokens(name) if FORBIDDEN_TOKENS.search(t)]
    if name in TRIGGER_TOKEN_ALLOWED:
        hits = [t for t in hits if t not in {"STAGE2_TRIGGER", "stage2_trigger.json"}]
    assert not hits, (name, hits)
    if name not in TEAM_RECORD_ALLOWED:
        team = [t for t in _code_tokens(name) if TEAM_RECORD_TOKEN.search(t)]
        assert not team, (name, team)


def _public_callables(module):
    for attr, obj in vars(module).items():
        if attr.startswith("_") or getattr(obj, "__module__", None) != module.__name__:
            continue
        if inspect.isfunction(obj):
            yield attr, obj
        elif inspect.isclass(obj):
            if is_dataclass(obj):
                yield attr, obj
            for meth_name, meth in vars(obj).items():
                if inspect.isfunction(meth) and (
                    not meth_name.startswith("_") or meth_name == "__init__"
                ):
                    yield f"{attr}.{meth_name}", meth


@pytest.mark.parametrize("name", BLIND)
def test_blind_public_parameters_accept_no_prohibited_inputs(name: str) -> None:
    module = __import__(f"map_ink_reference.{name}", fromlist=["_"])
    checked = 0
    for label, obj in _public_callables(module):
        if is_dataclass(obj):
            params = [f.name for f in fields(obj)]
        else:
            params = list(inspect.signature(obj).parameters)
        bad = [p for p in params if FORBIDDEN_PARAMETER.search(p)]
        assert not bad, (label, bad)
        checked += 1
    assert checked or name in ("stage_identity",)


def _subcommands() -> dict[str, argparse.ArgumentParser]:
    from map_ink_reference.cli import build_parser

    parser = build_parser()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return dict(action.choices)


def test_blind_cli_commands_accept_only_neutral_options() -> None:
    subcommands = _subcommands()
    for command in BLIND_CLI_COMMANDS:
        dests = {a.dest for a in subcommands[command]._actions}
        assert dests <= BLIND_CLI_OPTIONS, (command, dests - BLIND_CLI_OPTIONS)


def test_unblinded_cli_commands_take_no_calibration_paths() -> None:
    subcommands = _subcommands()
    assert {a.dest for a in subcommands["compare"]._actions} == {"help", "stage"}
    assert {a.dest for a in subcommands["criterion-4a"]._actions} == {"help"}
    # Amendment 4 ruling 5: no operator-supplied 4a is accepted.
    assert {a.dest for a in subcommands["report"]._actions} == {"help"}


def test_blind_tools_take_stage_identity_only_from_the_sealed_manifest() -> None:
    tokens = set(_code_tokens("pipeline_blind"))
    assert "load_corpus_blind_input" in tokens
    for name in BLIND:
        assert not {"derive_latch", "MatchIdentityTracker", "match_intro"} & set(
            _code_tokens(name)
        ), name


def test_importing_blind_modules_loads_no_unblinded_module() -> None:
    code = (
        "import sys\n"
        + "".join(f"import map_ink_reference.{m}\n" for m in BLIND)
        + "bad = [m for m in sys.modules if m.split('.')[-1] in "
        + repr(UNBLINDED + DERIVATION)
        + " and m.startswith('map_ink_reference')]\n"
        + "assert not bad, bad\n"
    )
    env_path = str(PACKAGE_DIR.parent)
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": env_path, "PATH": ""},
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_checks_detect_violations_in_unblinded_code() -> None:
    """Negative control: the same checks flag the comparison stage."""
    assert any(FORBIDDEN_TOKENS.search(t) for t in _code_tokens("pipeline_unblinded"))
    assert "comparison" in _local_closure("pipeline_unblinded")
    from map_ink_reference import comparison

    params = [
        p
        for _, obj in _public_callables(comparison)
        if not is_dataclass(obj)
        for p in inspect.signature(obj).parameters
    ]
    assert any(FORBIDDEN_PARAMETER.search(p) for p in params)


def test_unblinded_comparison_is_only_place_calibrations_are_loaded() -> None:
    loaders = [
        name
        for name in BLIND + NEUTRAL + DERIVATION + UNBLINDED
        if "team_color_calibration" in _code_tokens(name)
    ]
    assert loaders == ["pipeline_unblinded", "criterion_4a"]
