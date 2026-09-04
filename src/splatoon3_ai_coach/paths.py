"""Path helpers shared across pipeline stages."""

from pathlib import Path


def portable_path(path: Path, base: Path) -> str:
    """Return a POSIX relative path from base, or the path name if outside base."""
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.name
