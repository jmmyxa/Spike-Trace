"""Stable labels shared by training, inference, and future product modules."""

ACTION_LABELS: tuple[str, ...] = (
    "background",
    "serve",
    "receive",
    "set",
    "attack",
    "block",
    "dig",
)

BACKGROUND_LABEL = "background"
ACTION_LABEL_SCHEMA_VERSION = 2
CHECKPOINT_FORMAT_VERSION = 1
SAMPLING_CONTRACT = "center-nearest-frame-v1"
