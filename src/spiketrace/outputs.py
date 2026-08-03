from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path

from .domain import ActionEvent, ActionWindow, VideoMetadata

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
) -> tuple[Path, Path]:
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    event_items = list(events)
    window_items = list(windows)

    json_path = destination / "events.json"
    csv_path = destination / "events.csv"
    payload = {
        "format_version": 1,
        "video": metadata.to_dict(),
        "model_version": model_version,
        "settings": settings,
        "events": [event.to_dict() for event in event_items],
        "windows": [
            {
                "start_seconds": window.start_seconds,
                "end_seconds": window.end_seconds,
                "action": window.action,
                "confidence": window.confidence,
            }
            for window in window_items
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
