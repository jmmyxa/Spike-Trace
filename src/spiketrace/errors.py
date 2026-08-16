class SpikeTraceError(Exception):
    """Base error for actionable command-line failures."""


class ManifestError(SpikeTraceError):
    """Raised when an annotation manifest is invalid."""


class VideoError(SpikeTraceError):
    """Raised when a video cannot be inspected or decoded."""


class CheckpointError(SpikeTraceError):
    """Raised when a model checkpoint is invalid or incompatible."""


class ReviewError(SpikeTraceError):
    """Raised when a review request specification is invalid."""


class ActiveLearningError(SpikeTraceError):
    """Raised when an active-learning artifact is invalid or unsafe to apply."""
