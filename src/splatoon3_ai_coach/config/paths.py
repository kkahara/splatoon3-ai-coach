"""Resolve bundled and project-local paths without hardcoding the cwd."""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def default_config_path() -> Path:
    """Return the default YAML config shipped with the repository."""
    return PROJECT_ROOT / "configs" / "default.yaml"


def resolve_config_path(path: Path | None = None) -> Path:
    """Resolve an explicit config path or fall back to the project default."""
    if path is None:
        return default_config_path()
    return path.expanduser().resolve()
