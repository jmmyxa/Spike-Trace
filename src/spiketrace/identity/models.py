from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


COURT_SIDES = frozenset({"near", "far", "unknown"})
TEAMS = frozenset({"usa", "opponent", "unknown", "out_of_scope"})
VISIBILITY_STATES = frozenset(
    {"visible_clear", "visible_blurred", "occluded", "back_only", "front_only", "not_visible"}
)
IDENTITY_STATUSES = frozenset({"confirmed", "candidate", "unresolved", "rejected"})
NUMBER_STATUSES = frozenset({"confirmed", "candidate", "unreadable", "not_visible", "not_run"})


def _finite_confidence(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1.")
    return float(value)


@dataclass(frozen=True, slots=True)
class PlayerDetection:
    """One detector observation in source-video pixel coordinates.

    ``court_side`` is a camera/court position (near/far), deliberately separate
    from ``team`` which is a visual identity classification and may be unknown.
    """

    frame_index: int
    timestamp_ms: int
    box_xyxy: tuple[float, float, float, float]
    confidence: float
    court_side: str = "unknown"
    team: str = "unknown"
    visibility_state: str = "visible_clear"

    def __post_init__(self) -> None:
        if type(self.frame_index) is not int or self.frame_index < 0:
            raise ValueError("frame_index must be a non-negative integer.")
        if type(self.timestamp_ms) is not int or self.timestamp_ms < 0:
            raise ValueError("timestamp_ms must be a non-negative integer.")
        if len(self.box_xyxy) != 4 or any(not isinstance(value, (int, float)) for value in self.box_xyxy):
            raise ValueError("box_xyxy must contain four numeric values.")
        x1, y1, x2, y2 = self.box_xyxy
        if x2 <= x1 or y2 <= y1:
            raise ValueError("box_xyxy must have positive width and height.")
        _finite_confidence(self.confidence, "confidence")
        if self.court_side not in COURT_SIDES:
            raise ValueError("court_side must be near, far, or unknown.")
        if self.team not in TEAMS:
            raise ValueError("team is not a supported identity value.")
        if self.visibility_state not in VISIBILITY_STATES:
            raise ValueError("visibility_state is not supported.")

    @property
    def team_identity(self) -> str:
        """Alias that makes the distinction from ``court_side`` explicit."""
        return self.team

    @property
    def team_side(self) -> str:
        """Compatibility alias for callers using the action vocabulary."""
        return self.court_side


@dataclass(frozen=True, slots=True)
class Track:
    """Short-lived sequence of detections from one pipeline run."""

    track_id: str
    detections: tuple[PlayerDetection, ...]
    max_occlusion_gap_ms: int = 1000

    def __post_init__(self) -> None:
        if not self.track_id:
            raise ValueError("track_id must not be empty.")
        if not self.detections:
            raise ValueError("a track requires at least one detection.")
        if any(not isinstance(item, PlayerDetection) for item in self.detections):
            raise TypeError("detections must contain PlayerDetection values.")
        timestamps = [item.timestamp_ms for item in self.detections]
        if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
            raise ValueError("track detections must have increasing timestamps.")
        if type(self.max_occlusion_gap_ms) is not int or self.max_occlusion_gap_ms < 0:
            raise ValueError("max_occlusion_gap_ms must be non-negative.")

    @property
    def start_ms(self) -> int:
        return self.detections[0].timestamp_ms

    @property
    def end_ms(self) -> int:
        return self.detections[-1].timestamp_ms


@dataclass(frozen=True, slots=True)
class NumberObservation:
    """OCR result attached to one visible detection."""

    timestamp_ms: int
    raw_text: str
    confidence: float
    visibility_state: str = "visible_clear"

    def __post_init__(self) -> None:
        if type(self.timestamp_ms) is not int or self.timestamp_ms < 0:
            raise ValueError("timestamp_ms must be a non-negative integer.")
        if not isinstance(self.raw_text, str):
            raise TypeError("raw_text must be a string.")
        _finite_confidence(self.confidence, "confidence")
        if self.visibility_state not in VISIBILITY_STATES:
            raise ValueError("visibility_state is not supported.")


@dataclass(frozen=True, slots=True)
class NumberResolution:
    number: str | None
    status: str
    confidence: float
    candidates: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        if self.status not in NUMBER_STATUSES:
            raise ValueError("status is not supported.")
        _finite_confidence(self.confidence, "confidence")
        if self.number is not None and (not self.number.isdigit() or not self.number):
            raise ValueError("number must contain digits only.")


@dataclass(frozen=True, slots=True)
class IdentityAssignment:
    """Resolved identity for a track, suitable for joining to action events."""

    track_id: str
    start_ms: int
    end_ms: int
    court_side: str
    team: str
    identity_ref: str | None
    number: str | None
    identity_status: str
    number_status: str
    assignment_confidence: float
    number_confidence: float = 0.0
    visibility_state: str = "not_visible"

    def __post_init__(self) -> None:
        if not self.track_id:
            raise ValueError("track_id must not be empty.")
        if type(self.start_ms) is not int or type(self.end_ms) is not int or self.end_ms <= self.start_ms:
            raise ValueError("assignment interval must be a positive millisecond range.")
        if self.court_side not in COURT_SIDES:
            raise ValueError("court_side must be near, far, or unknown.")
        if self.team not in TEAMS:
            raise ValueError("team is not a supported identity value.")
        if self.identity_status not in IDENTITY_STATUSES:
            raise ValueError("identity_status is not supported.")
        if self.number_status not in NUMBER_STATUSES:
            raise ValueError("number_status is not supported.")
        _finite_confidence(self.assignment_confidence, "assignment_confidence")
        _finite_confidence(self.number_confidence, "number_confidence")
        if self.number is not None and (not self.number.isdigit() or not self.number):
            raise ValueError("number must contain digits only.")
        if self.identity_status == "confirmed" and not self.identity_ref:
            raise ValueError("confirmed identity requires identity_ref.")
        if self.number_status == "confirmed" and self.number is None:
            raise ValueError("confirmed number requires number.")
        if self.visibility_state not in VISIBILITY_STATES:
            raise ValueError("visibility_state is not supported.")

    @property
    def confirmed(self) -> bool:
        return self.identity_status == "confirmed" and self.number_status == "confirmed"

    @property
    def team_side(self) -> str:
        """Compatibility alias for the camera-side field on ActionEvent."""
        return self.court_side

    @property
    def team_identity(self) -> str:
        return self.team
