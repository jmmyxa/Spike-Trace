"""Player detection, tracking, and jersey-number identity primitives."""

from .models import (
    IdentityAssignment,
    NumberObservation,
    NumberResolution,
    PlayerDetection,
    Track,
)
from .numbers import aggregate_number_candidates, normalize_number, resolve_number_observations
from .events import apply_identity_assignments, map_assignments_to_events

__all__ = [
    "IdentityAssignment",
    "NumberObservation",
    "NumberResolution",
    "PlayerDetection",
    "Track",
    "aggregate_number_candidates",
    "normalize_number",
    "resolve_number_observations",
    "apply_identity_assignments",
    "map_assignments_to_events",
]
