class SpikeTraceError(Exception):
    """Base error for actionable command-line failures."""


class ManifestError(SpikeTraceError):
    """Raised when an annotation manifest is invalid."""


class VideoError(SpikeTraceError):
    """Raised when a video cannot be inspected or decoded."""


class CheckpointError(SpikeTraceError):
    """Raised when a model checkpoint is invalid or incompatible."""
