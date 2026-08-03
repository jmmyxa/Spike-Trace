from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .constants import BACKGROUND_LABEL
from .domain import ActionEvent, ActionWindow


@dataclass(slots=True)
class _EventCandidate:
    start_seconds: float
    end_seconds: float
    action: str
    confidences: list[float]


def merge_action_windows(
    windows: Iterable[ActionWindow],
    *,
    video_id: str,
    model_version: str,
    confidence_threshold: float = 0.5,
    merge_gap_seconds: float = 0.25,
    min_event_seconds: float = 0.2,
    background_label: str = BACKGROUND_LABEL,
) -> list[ActionEvent]:
    if not 0 <= confidence_threshold <= 1:
        raise ValueError("confidence_threshold must be between 0 and 1.")
    if merge_gap_seconds < 0 or min_event_seconds < 0:
        raise ValueError("Event durations and gaps cannot be negative.")

    filtered = sorted(
        (
            window
            for window in windows
            if window.action != background_label
            and window.confidence >= confidence_threshold
        ),
        key=lambda window: (window.start_seconds, window.end_seconds),
    )

    candidates: list[_EventCandidate] = []
    for window in filtered:
        if window.end_seconds <= window.start_seconds:
            continue
        if (
            candidates
            and candidates[-1].action == window.action
            and window.start_seconds <= candidates[-1].end_seconds + merge_gap_seconds
        ):
            current = candidates[-1]
            current.end_seconds = max(current.end_seconds, window.end_seconds)
            current.confidences.append(window.confidence)
        else:
            candidates.append(
                _EventCandidate(
                    start_seconds=window.start_seconds,
                    end_seconds=window.end_seconds,
                    action=window.action,
                    confidences=[window.confidence],
                )
            )

    events: list[ActionEvent] = []
    for candidate in candidates:
        if candidate.end_seconds - candidate.start_seconds < min_event_seconds:
            continue
        event_index = len(events) + 1
        events.append(
            ActionEvent(
                video_id=video_id,
                event_id=f"evt_{event_index:06d}",
                start_ms=round(candidate.start_seconds * 1000),
                end_ms=round(candidate.end_seconds * 1000),
                action=candidate.action,
                confidence=round(
                    sum(candidate.confidences) / len(candidate.confidences), 6
                ),
                team_side=None,
                player_number=None,
                status="predicted",
                model_version=model_version,
            )
        )
    return events
