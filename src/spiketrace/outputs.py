from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path

from .domain import ActionEvent, ActionWindow, VideoMetadata
from .events import seconds_to_milliseconds

EVENT_FIELDS = (
    "video_id",
    "event_id",
    "start_ms",
    "end_ms",
    "action",
    "confidence",
    "team_side",
    "player_number",
    "status",
    "model_version",
    "source",
)


def write_inference_outputs(
    output_dir: str | Path,
    *,
    metadata: VideoMetadata,
    model_version: str,
    events: Iterable[ActionEvent],
    windows: Iterable[ActionWindow],
    settings: dict[str, object],
    event_window_indices: dict[str, list[int]],
) -> tuple[Path, Path]:
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    event_items = list(events)
    window_items = list(windows)
    _validate_event_window_indices(
        event_items,
        window_items,
        settings=settings,
        event_window_indices=event_window_indices,
    )

    json_path = destination / "events.json"
    csv_path = destination / "events.csv"
    payload = {
        "format_version": 2,
        "video": metadata.to_dict(),
        "model_version": model_version,
        "settings": settings,
        "events": [
            event.to_dict()
            | {"source_window_indices": event_window_indices[event.event_id]}
            for event in event_items
        ],
        "windows": [
            {
                "window_index": window_index,
                "start_seconds": window.start_seconds,
                "end_seconds": window.end_seconds,
                "action": window.action,
                "confidence": window.confidence,
            }
            for window_index, window in enumerate(window_items)
        ],
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EVENT_FIELDS)
        writer.writeheader()
        writer.writerows(event.to_dict() for event in event_items)

    return json_path, csv_path


def _validate_event_window_indices(
    events: list[ActionEvent],
    windows: list[ActionWindow],
    *,
    settings: dict[str, object],
    event_window_indices: dict[str, list[int]],
) -> None:
    confidence_threshold = settings.get("confidence_threshold")
    if (
        not isinstance(confidence_threshold, (int, float))
        or isinstance(confidence_threshold, bool)
        or not 0 <= confidence_threshold <= 1
    ):
        raise ValueError("settings must include a valid confidence_threshold.")

    event_ids = [event.event_id for event in events]
    if len(set(event_ids)) != len(event_ids):
        raise ValueError("Event IDs must be unique.")
    if set(event_window_indices) != set(event_ids):
        raise ValueError("Each event must have exactly one provenance mapping.")

    assigned_indices: set[int] = set()
    for event in events:
        indices = event_window_indices[event.event_id]
        if not isinstance(indices, list) or not indices:
            raise ValueError("Each event provenance mapping must be a non-empty list.")
        if any(type(index) is not int for index in indices):
            raise ValueError("Window indices must be integers.")
        if indices != sorted(indices) or len(indices) != len(set(indices)):
            raise ValueError("Window indices must be unique and increasing.")
        for index in indices:
            if not 0 <= index < len(windows):
                raise ValueError("Window index is out of range.")
            if index in assigned_indices:
                raise ValueError("A window may belong to only one event.")
            window = windows[index]
            if window.action != event.action:
                raise ValueError("Provenance window action must match the event action.")
            if window.confidence < confidence_threshold:
                raise ValueError(
                    "Provenance window confidence must meet the configured threshold."
                )
            assigned_indices.add(index)
        member_windows = [windows[index] for index in indices]
        expected_start_ms = min(
            seconds_to_milliseconds(window.start_seconds)
            for window in member_windows
        )
        expected_end_ms = max(
            seconds_to_milliseconds(window.end_seconds) for window in member_windows
        )
        if event.start_ms != expected_start_ms or event.end_ms != expected_end_ms:
            raise ValueError("Event bounds must match the provenance windows.")
        expected_confidence = round(
            sum(window.confidence for window in member_windows) / len(member_windows),
            6,
        )
        if event.confidence != expected_confidence:
            raise ValueError(
                "Event confidence must equal the rounded mean of provenance windows."
            )
