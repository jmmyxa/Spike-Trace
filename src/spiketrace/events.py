from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .constants import BACKGROUND_LABEL
from .domain import ActionEvent, ActionWindow


@dataclass(slots=True)
class _EventCandidate:
    start_seconds: float
    end_seconds: float
    action: str
    confidences: list[float]
    window_indices: list[int]


def seconds_to_milliseconds(seconds: float) -> int:
    """Convert seconds to integer milliseconds using explicit half-up rounding."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise TypeError("seconds must be numeric.")
    try:
        if not math.isfinite(seconds):
            raise ValueError("seconds must be finite.")
    except OverflowError as exc:
        raise ValueError("seconds must be finite.") from exc
    return int(
        (Decimal(str(seconds)) * Decimal(1000)).to_integral_value(
            rounding=ROUND_HALF_UP
        )
    )


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
    events, _ = merge_action_windows_with_provenance(
        windows,
        video_id=video_id,
        model_version=model_version,
        confidence_threshold=confidence_threshold,
        merge_gap_seconds=merge_gap_seconds,
        min_event_seconds=min_event_seconds,
        background_label=background_label,
    )
    return events


def merge_action_windows_with_provenance(
    windows: Iterable[ActionWindow],
    *,
    video_id: str,
    model_version: str,
    confidence_threshold: float = 0.5,
    merge_gap_seconds: float = 0.25,
    min_event_seconds: float = 0.2,
    background_label: str = BACKGROUND_LABEL,
) -> tuple[list[ActionEvent], dict[str, list[int]]]:
    if not 0 <= confidence_threshold <= 1:
        raise ValueError("confidence_threshold must be between 0 and 1.")
    if merge_gap_seconds < 0 or min_event_seconds < 0:
        raise ValueError("Event durations and gaps cannot be negative.")

    indexed_windows = list(enumerate(windows))
    filtered = sorted(
        (
            (window_index, window)
            for window_index, window in indexed_windows
            if window.action != background_label
            and window.confidence >= confidence_threshold
        ),
        key=lambda indexed_window: (
            indexed_window[1].start_seconds,
            indexed_window[1].end_seconds,
        ),
    )

    candidates: list[_EventCandidate] = []
    for window_index, window in filtered:
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
            current.window_indices.append(window_index)
        else:
            candidates.append(
                _EventCandidate(
                    start_seconds=window.start_seconds,
                    end_seconds=window.end_seconds,
                    action=window.action,
                    confidences=[window.confidence],
                    window_indices=[window_index],
                )
            )

    events: list[ActionEvent] = []
    provenance: dict[str, list[int]] = {}
    for candidate in candidates:
        if candidate.end_seconds - candidate.start_seconds < min_event_seconds:
            continue
        event_index = len(events) + 1
        event = ActionEvent(
            video_id=video_id,
            event_id=f"evt_{event_index:06d}",
            start_ms=seconds_to_milliseconds(candidate.start_seconds),
            end_ms=seconds_to_milliseconds(candidate.end_seconds),
            action=candidate.action,
            confidence=round(sum(candidate.confidences) / len(candidate.confidences), 6),
            team_side=None,
            player_number=None,
            status="predicted",
            model_version=model_version,
        )
        events.append(event)
        provenance[event.event_id] = sorted(set(candidate.window_indices))
    return events, provenance
