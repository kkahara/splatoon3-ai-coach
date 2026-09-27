"""Web-server settings. Analysis YAML stays in configs/default.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from splatoon3_ai_coach.config.paths import PROJECT_ROOT, default_config_path


@dataclass(frozen=True)
class PlatformSettings:
    """Locations and limits for the local platform."""

    analysis_root: Path
    platform_root: Path
    movies_dir: Path
    config_path: Path
    static_dir: Path
    max_concurrent: int = 1
    host: str = "127.0.0.1"
    port: int = 8765

    @classmethod
    def from_env(cls) -> PlatformSettings:
        """Build settings from the repo layout and optional env overrides."""
        analysis = Path(
            os.environ.get("VMV_ANALYSIS_ROOT", PROJECT_ROOT / "analysis")
        ).expanduser()
        platform = Path(
            os.environ.get("VMV_PLATFORM_ROOT", analysis / "_platform")
        ).expanduser()
        movies = Path(os.environ.get("VMV_MOVIES_DIR", Path.home() / "Movies")).expanduser()
        static = PROJECT_ROOT / "web" / "vmv" / "dist"
        return cls(
            analysis_root=analysis.resolve(),
            platform_root=platform.resolve(),
            movies_dir=movies.resolve(),
            config_path=default_config_path(),
            static_dir=static,
            max_concurrent=int(os.environ.get("VMV_MAX_CONCURRENT", "1")),
            host=os.environ.get("VMV_HOST", "127.0.0.1"),
            port=int(os.environ.get("VMV_PORT", "8765")),
        )
