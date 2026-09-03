"""Environment-based settings for secrets and runtime overrides."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class CoachSettings(BaseSettings):
    """Secrets and overrides loaded from environment variables.

    Variables are prefixed with ``S3_COACH_``, e.g. ``S3_COACH_OPENAI_API_KEY``.
    """

    model_config = SettingsConfigDict(env_prefix="S3_COACH_", extra="ignore")

    openai_api_key: str | None = None
    llm_provider: str | None = None
