"""Environment-based settings for secrets and runtime overrides."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class CoachSettings(BaseSettings):
    """Secrets and overrides from the process environment and optional ``.env``.

    Variables use the ``S3_COACH_`` prefix, e.g. ``S3_COACH_NVIDIA_API_KEY``.

    A gitignored ``.env`` in the working directory is loaded automatically so
    Cursor/VS Code Debug launches can see keys that were only ``export``-ed in
    a different terminal. Never put API keys in YAML or commit ``.env``.
    """

    model_config = SettingsConfigDict(
        env_prefix="S3_COACH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str | None = None
    nvidia_api_key: str | None = None
    llm_provider: str | None = None
    ollama_base_url: str | None = None
