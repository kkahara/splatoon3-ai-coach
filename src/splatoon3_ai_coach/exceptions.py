"""Application-specific errors with stable types for CLI handling."""


class S3CoachError(Exception):
    """Base error for all application failures."""


class ConfigError(S3CoachError):
    """Raised when configuration is missing or invalid."""


class VideoLoadError(S3CoachError):
    """Raised when a video cannot be opened or decoded."""


class VisionError(S3CoachError):
    """Raised when vision analysis fails."""


class ManifestError(S3CoachError):
    """Raised when a manifest cannot be read or validated."""


GAME_LANGUAGE_UNDETERMINED_MESSAGE = (
    "Game language could not be determined from the recording."
)


class GameLanguageUndeterminedError(VisionError):
    """Raised when the pre-scan probe cannot establish the game language.

    Analysis stops before any detector runs; only ``game_language.json`` is
    written. Never replaced by a default or supplied language.
    """

    def __init__(self, message: str = GAME_LANGUAGE_UNDETERMINED_MESSAGE) -> None:
        super().__init__(message)

