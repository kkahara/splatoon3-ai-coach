"""Architecture guard: pipeline stages never discover their execution environment.

Invariant (AGENTS.md, "Execution environment boundary"): analysis and detector
code must not infer behavior from machine identity, operating system, CPU
architecture, hosting provider, or deployment environment. Hardware capability
is resolved at an execution boundary and injected.

The guard is AST-based. It forbids *discovery* (``platform``, ``sys.platform``,
``os.uname``, environment variables, accelerator availability probes) and
hosting-provider identity in identifiers and comparisons. It does not forbid
accelerator libraries themselves: ``torch.zeros(...).to(device)`` is allowed,
``torch.cuda.is_available()`` is not. Docstrings and comments are not scanned.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "splatoon3_ai_coach"

# Pipeline stages that must stay environment-neutral. Deliberately not guarded:
# ``config/`` (settings boundary; reads secrets from env), ``cli/`` (entry
# boundary), ``capture/`` (acquisition boundary), and ``inspect_frame.py``
# (developer image viewer that opens the OS default app).
GUARDED_PACKAGES = (
    "media",
    "extraction",
    "vision",
    "analysis",
    "coach",
    "review",
    "alignment",
)

# Modules whose only purpose is describing the host machine.
FORBIDDEN_MODULES = frozenset({"platform"})

ENVIRONMENT_DISCOVERY = frozenset(
    {
        "sys.platform",
        "sys.getwindowsversion",
        "os.name",
        "os.uname",
        "os.environ",
        "os.environb",
        "os.getenv",
        "os.getenvb",
    }
)

ACCELERATOR_DISCOVERY = frozenset(
    {
        "torch.cuda.is_available",
        "torch.cuda.device_count",
        "torch.cuda.get_device_name",
        "torch.cuda.get_device_capability",
        "torch.backends.cuda.is_built",
        "torch.backends.cudnn.is_available",
        "torch.backends.mps.is_available",
        "torch.backends.mps.is_built",
        "torch.mps.device_count",
        "onnxruntime.get_available_providers",
        "onnxruntime.get_device",
        "cv2.cuda.getCudaEnabledDeviceCount",
        "cv2.ocl.haveOpenCL",
        "cv2.ocl.useOpenCL",
    }
)

FORBIDDEN_NAMES = ENVIRONMENT_DISCOVERY | ACCELERATOR_DISCOVERY

# Hosting providers are infrastructure identity, never a capability.
PROVIDER_TOKENS = ("runpod", "hetzner")


@dataclass(frozen=True)
class Violation:
    """One forbidden construct at a source location."""

    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.detail}"


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    """Map local names bound by absolute imports to their dotted origin."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    root = alias.name.split(".")[0]
                    aliases[root] = root
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _dotted(node: ast.expr, aliases: dict[str, str]) -> str | None:
    """Resolve ``a.b.c`` to its imported dotted name, if rooted in an import."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name) or node.id not in aliases:
        return None
    return ".".join([aliases[node.id], *reversed(parts)])


def _forbidden_match(dotted: str) -> str | None:
    for name in FORBIDDEN_NAMES:
        if dotted == name or dotted.startswith(name + "."):
            return name
    return None


def _module_violations(tree: ast.AST) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules = [node.module]
        else:
            continue
        for module in modules:
            if module.split(".")[0] in FORBIDDEN_MODULES:
                found.append((node.lineno, "host-identity module", module))
    return found


def _discovery_violations(tree: ast.AST) -> list[tuple[int, str, str]]:
    aliases = _import_aliases(tree)
    found: set[tuple[int, str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute | ast.Name):
            dotted = _dotted(node, aliases)
            match = _forbidden_match(dotted) if dotted else None
            if match:
                rule = (
                    "accelerator discovery"
                    if match in ACCELERATOR_DISCOVERY
                    else "environment discovery"
                )
                found.add((node.lineno, rule, match))
    return sorted(found)


def _identifiers(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, ast.arg | ast.keyword) and node.arg:
        return [node.arg]
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return [node.name]
    if isinstance(node, ast.alias):
        return [name for name in (node.name, node.asname) if name]
    return []


def _compared_strings(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Compare):
        operands = [node.left, *node.comparators]
    elif isinstance(node, ast.MatchValue):
        operands = [node.value]
    else:
        return []
    strings: list[str] = []
    for operand in operands:
        if isinstance(operand, ast.Tuple | ast.List | ast.Set):
            items = operand.elts
        else:
            items = [operand]
        strings.extend(
            item.value
            for item in items
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        )
    return strings


def _provider_violations(tree: ast.AST) -> list[tuple[int, str, str]]:
    found: set[tuple[int, str, str]] = set()
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        for name in _identifiers(node):
            if any(token in name.lower() for token in PROVIDER_TOKENS):
                found.add((line, "provider identifier", name))
        for text in _compared_strings(node):
            if any(token in text.lower() for token in PROVIDER_TOKENS):
                found.add((line, "provider comparison", text))
    return sorted(found)


def check_source(source: str, path: str = "<string>") -> list[Violation]:
    """Return every execution-environment violation in one module's source."""
    tree = ast.parse(source, filename=path)
    found = (
        _module_violations(tree)
        + _discovery_violations(tree)
        + _provider_violations(tree)
    )
    return [Violation(path, line, rule, detail) for line, rule, detail in found]


def _guarded_files() -> list[Path]:
    files: list[Path] = []
    for package in GUARDED_PACKAGES:
        files.extend(sorted((PACKAGE_ROOT / package).rglob("*.py")))
    return files


def test_guarded_packages_exist() -> None:
    for package in GUARDED_PACKAGES:
        assert (PACKAGE_ROOT / package / "__init__.py").is_file(), package
    assert len(_guarded_files()) > 50


def test_pipeline_stages_do_not_discover_execution_environment() -> None:
    violations: list[Violation] = []
    for path in _guarded_files():
        rel = str(path.relative_to(PACKAGE_ROOT))
        violations.extend(check_source(path.read_text(encoding="utf-8"), rel))
    assert not violations, "\n".join(str(item) for item in violations)


@pytest.mark.parametrize(
    "source",
    [
        "import platform\nplatform.machine()",
        "from platform import system\nsystem()",
        "import sys\nif sys.platform == 'darwin':\n    pass",
        "import os\nos.uname()",
        "import os\nmode = os.environ.get('S3_MODE')",
        "from os import getenv\ngetenv('X')",
        "import torch\nif torch.cuda.is_available():\n    pass",
        "import torch.backends.mps as m\nm.is_available()",
        "from torch import cuda\ncuda.device_count()",
        "import onnxruntime as ort\nort.get_available_providers()",
        "import cv2\ncv2.ocl.haveOpenCL()",
        "def detect(on_runpod: bool) -> None:\n    pass",
        "if settings.host == 'Hetzner-fsn1':\n    pass",
        "if provider in ('aws', 'runpod'):\n    pass",
        "match host:\n    case 'runpod':\n        pass",
    ],
)
def test_guard_flags_environment_discovery(source: str) -> None:
    assert check_source(source)


@pytest.mark.parametrize(
    "source",
    [
        "import torch\nx = torch.zeros(1).to(device)",
        "import torch\nwith torch.no_grad():\n    pass",
        "import onnxruntime as ort\nort.InferenceSession(path, providers=providers)",
        "import cv2\nscore = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)",
        "def f():\n    '''Runs on Runpod, Hetzner, CUDA or MPS alike.'''\n    return 1",
        "provider = 'nvidia'\nif provider == 'ollama':\n    pass",
        "import os\npath = os.path.join('a', 'b')",
    ],
)
def test_guard_allows_capability_use_without_discovery(source: str) -> None:
    assert check_source(source) == []
