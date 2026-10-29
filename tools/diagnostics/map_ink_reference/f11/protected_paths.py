"""The complete F11 protected-tool path set.

The tooling commit declares this tuple. After that commit is sealed, F11
commands reject any expansion, reduction or rename of it. Paths are not added
elsewhere.
"""

from __future__ import annotations

F11_PROTECTED_PATHS: tuple[str, ...] = (
    "tests/map_ink_reference/f11/test_f11_duplicates.py",
    "tests/map_ink_reference/f11/test_f11_eligibility.py",
    "tests/map_ink_reference/f11/test_f11_guard.py",
    "tests/map_ink_reference/f11/test_f11_sampling.py",
    "tests/map_ink_reference/f11/test_f11_statistics.py",
    "tests/map_ink_reference/f11/test_f11_summaries.py",
    "tools/diagnostics/map_ink_reference/f11/__init__.py",
    "tools/diagnostics/map_ink_reference/f11/__main__.py",
    "tools/diagnostics/map_ink_reference/f11/chain.py",
    "tools/diagnostics/map_ink_reference/f11/cli.py",
    "tools/diagnostics/map_ink_reference/f11/commands.py",
    "tools/diagnostics/map_ink_reference/f11/duplicates.py",
    "tools/diagnostics/map_ink_reference/f11/eligibility.py",
    "tools/diagnostics/map_ink_reference/f11/guard.py",
    "tools/diagnostics/map_ink_reference/f11/protected_paths.py",
    "tools/diagnostics/map_ink_reference/f11/sampling.py",
    "tools/diagnostics/map_ink_reference/f11/statistics.py",
    "tools/diagnostics/map_ink_reference/f11/summaries.py",
)
