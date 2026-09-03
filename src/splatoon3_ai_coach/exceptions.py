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

