"""Stable labels shared by training, inference, and future product modules."""

ACTION_LABELS: tuple[str, ...] = (
    "background",
    "serve",
    "receive",
    "set",
    "attack",
    "block",
)

BACKGROUND_LABEL = "background"
CHECKPOINT_FORMAT_VERSION = 1
